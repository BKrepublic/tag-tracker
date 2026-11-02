import csv
import io
import hmac
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import backup, db, poller
from .config import COOKIE_SECURE, DATA_DIR, POLL_MINUTES, POLLING_ENABLED, ROOT, SECRET_KEY, SESSION_DAYS

WEB_DIR = ROOT / "web"


@asynccontextmanager
async def lifespan(_app):
    db.init()
    if db.user_count() == 0:
        print("\n  No users yet. Create one with:  python -m server.manage adduser <name>\n")
    if POLLING_ENABLED:
        poller.start()
    backup.start()
    yield


app = FastAPI(title="Tag Tracker", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=SESSION_DAYS * 86400,
    same_site="lax",
    https_only=COOKIE_SECURE,
)


def require_user(request: Request) -> str:
    user = request.session.get("user")
    proof = request.session.get("auth_proof")
    expected = db.session_proof(user, SECRET_KEY) if isinstance(user, str) else None
    if not expected or not isinstance(proof, str) or not hmac.compare_digest(proof, expected):
        request.session.clear()
        raise HTTPException(401, "Not logged in")
    return user


# ---------- auth ----------

_failed: dict[str, list[float]] = defaultdict(list)


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    _failed[ip] = [t for t in _failed[ip] if now - t < 900]
    if len(_failed[ip]) >= 10:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not db.check_login(body.username.strip(), body.password):
        _failed[ip].append(now)
        raise HTTPException(401, "Wrong username or password")
    _failed.pop(ip, None)
    username = body.username.strip()
    request.session.clear()
    request.session["user"] = username
    request.session["auth_proof"] = db.session_proof(username, SECRET_KEY)
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(user: str = Depends(require_user)):
    return {"user": user}


# ---------- data ----------

_GUARD_FILE = DATA_DIR / "guard.flag"


def _guard_get() -> bool:
    return _GUARD_FILE.exists()


@app.get("/api/devices")
def devices(user: str = Depends(require_user)):
    return {
        "devices": db.get_devices(),
        "guard": _guard_get(),
        "poller": {**poller.status, "enabled": POLLING_ENABLED, "interval_minutes": POLL_MINUTES},
        "backup": backup.status,
    }


@app.post("/api/guard")
def set_guard(body: dict, user: str = Depends(require_user)):
    on = bool(body.get("parked"))
    _GUARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    if on:
        _GUARD_FILE.write_text("1")
    elif _GUARD_FILE.exists():
        _GUARD_FILE.unlink()
    return {"ok": True, "parked": on}


class DeviceEdit(BaseModel):
    name: str
    color: str | None = None


@app.put("/api/devices/{device_id}")
def edit_device(device_id: str, body: DeviceEdit, user: str = Depends(require_user)):
    db.rename_device(device_id, body.name.strip() or "Tag", body.color)
    return {"ok": True}


@app.post("/api/poll-now")
def poll_now(user: str = Depends(require_user)):
    if not POLLING_ENABLED:
        raise HTTPException(400, "Polling is disabled on this server")
    poller.trigger_now()
    return {"ok": True}


@app.post("/api/locate/{device_id}")
def locate_now(device_id: str, user: str = Depends(require_user)):
    from . import finder
    st = finder.auth_state()
    if not st["signed_in"] or not st["unlocked"]:
        raise HTTPException(400, "Google account not fully connected (needs sign-in + encryption unlock)")
    try:
        added = db.insert_locations(device_id, finder.locate(device_id))
    except Exception as e:
        raise HTTPException(502, f"Could not locate: {e}")
    return {"ok": True, "added": added}


@app.post("/api/sound/{device_id}")
def sound(device_id: str, user: str = Depends(require_user)):
    from . import finder
    if not finder.auth_state()["signed_in"]:
        raise HTTPException(400, "This server isn't connected to Google yet, so it can't ring the tag. "
                                 "Ring/Locate need the Google location sign-in (Drive backup is separate).")
    try:
        finder.play_sound(device_id, True)
    except Exception as e:
        raise HTTPException(502, f"Could not ring tag: {e}")
    return {"ok": True}


@app.get("/api/history/{device_id}")
def history(device_id: str, start: int, end: int, format: str = "json", user: str = Depends(require_user)):
    points = db.get_history(device_id, start, end)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time_utc", "unix", "lat", "lon", "altitude", "accuracy_m", "own_report", "place"])
        for p in points:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(p["ts"])), p["ts"], p["lat"], p["lon"],
                        p["altitude"], p["accuracy"], p["is_own_report"], p["semantic_name"] or ""])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="track-{start}-{end}.csv"'})
    if format == "gpx":
        pts = "".join(
            f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}"><time>'
            f'{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p["ts"]))}</time></trkpt>'
            for p in points if p["lat"] is not None
        )
        gpx = ('<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="Tag Tracker" '
               'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>'
               f'{escape(device_id)}</name><trkseg>{pts}</trkseg></trk></gpx>')
        return Response(gpx, media_type="application/gpx+xml",
                        headers={"Content-Disposition": f'attachment; filename="track-{start}-{end}.gpx"'})
    return {"points": points}


# ---------- pages ----------

@app.get("/")
def index(request: Request):
    try:
        require_user(request)
    except HTTPException:
        return RedirectResponse("/login")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(WEB_DIR / "login.html")


@app.get("/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
