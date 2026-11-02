"""Admin commands.

  python -m server.manage adduser <username>     create a login (or reset its password)
  python -m server.manage deluser <username>
  python -m server.manage users
  python -m server.manage google-login           automated Chrome sign-in (may be rejected)
  python -m server.manage google-manual-login    sign in using normal Chrome, without driver
  python -m server.manage poll                   fetch tag locations once and exit
  python -m server.manage demo                   add a fake tag with 1 year of sample history
  python -m server.manage backup                 upload a backup to Google Drive now
  python -m server.manage restore                replace local data with the Google Drive backup
  python -m server.manage import-csv <file>      merge a CSV (e.g. TagTracker-history.csv from the phone app)
"""
import getpass
import math
from pathlib import Path
import random
import sys
import time

from . import db


def adduser(username: str):
    pw = getpass.getpass(f"Password for {username}: ")
    if len(pw) < 8:
        sys.exit("Password must be at least 8 characters.")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    db.upsert_user(username, pw)
    print(f"User '{username}' saved.")


def google_login():
    from . import finder

    print("A Chrome window will open. Sign in to the Google account that owns your tag.")
    print("You may also be asked for your phone's screen lock PIN/pattern - this unlocks")
    print("the end-to-end encryption keys so locations can be decrypted.\n")
    trackers = finder.list_trackers()
    if not trackers:
        print("Signed in, but no trackers were found on this account.")
        return
    print("Trackers found:")
    for name, cid in trackers:
        print(f"  - {name}  ({cid})")
    name, cid = trackers[0]
    print(f"\nTesting location fetch for '{name}'...")
    locs = finder.locate(cid)
    print(f"Got {len(locs)} location report(s). Setup complete.")
    print("Tokens are saved in data/google_secrets.json - keep this file private.")


def google_manual_login():
    """One-time interactive browser login; never drives Chrome or echoes secrets."""
    from . import finder

    print("Manual Find Hub sign-in. No browser automation or ChromeDriver.")
    print("Use the same Google account as your own Find Hub tag.")
    print("The oauth_token and vaultKeys are secrets. Never paste them in chat.")
    state = finder.auth_state()

    if not state["signed_in"]:
        print("\nSTEP 1 — Google account")
        print("Before opening the browser, preparing the device identity...")
        try:
            finder.prepare_google_sign_in()
        except Exception as exc:
            raise SystemExit(
                f"Google device registration failed: {type(exc).__name__}. No login cookie was used."
            ) from None
        email = input("Find Hub Google account email: ").strip()
        if "@" not in email:
            raise SystemExit("Enter the email address of the account with your tag.")
        print("Now open this URL in your everyday Chrome:")
        print("https://accounts.google.com/EmbeddedSetup")
        print("In that tab open DevTools (F12) > Application > Cookies >")
        print("https://accounts.google.com. Find the cookie named oauth_token.")
        print("After Google finishes sign-in, copy only its value.")
        print("The cookie expires quickly; enter it here immediately.")
        token = getpass.getpass("oauth_token (hidden): ").strip()
        if not token:
            raise SystemExit("No token supplied. Nothing was saved.")
        if not token.startswith("oauth2_4/"):
            raise SystemExit("Unexpected cookie format. Copy the full oauth_token Value (oauth2_4/...).")
        try:
            finder.sign_in_with_oauth_token(token, email=email)
        except finder.GoogleTokenExchangeError as exc:
            raise SystemExit(
                f"{exc}. The cookie may be expired or already used; get a NEW cookie."
            ) from None
        except Exception as exc:
            raise SystemExit(
                "Token exchange failed before completion: " + type(exc).__name__
                + ". The cookie may have been consumed. Get a fresh one after checking the error."
            ) from None
        finally:
            token = None
        print("Account token saved locally.")
        state = finder.auth_state()
        if not state["signed_in"]:
            raise SystemExit("Account token was not saved; cannot continue.")

    if not state["unlocked"]:
        print("\nSTEP 2 — Unlock Find Hub end-to-end encryption")
        print("Open this URL in your normal, already signed-in Chrome:")
        print(finder.shared_key_url().strip())
        print("Before entering your screen-lock PIN on that page,")
        print("open DevTools (F12) > Console and paste the following")
        print("LOCAL-ONLY listener script (review it before running).")
        print("It does not make network requests; it displays the returned")
        print("vaultKeys JSON in a text box at the top of Google's page.")
        script_path = Path(__file__).resolve().parents[1] / "tools" / "capture-vault-keys.js"
        print("\n" + script_path.read_text(encoding="utf-8") + "\n")
        print("Then complete Google's unlock page. Copy the displayed JSON,")
        print("and paste it into the hidden prompt in kitty below.")
        vault_json = getpass.getpass("vaultKeys JSON (hidden): ").strip()
        if not vault_json:
            raise SystemExit("No key supplied; your Google account token remains saved.")
        try:
            finder.save_vault_keys(vault_json)
        except Exception as exc:
            raise SystemExit(
                "Could not verify the unlock result: " + type(exc).__name__
            ) from None
        finally:
            vault_json = None
        print("Find Hub location decryption key saved locally.")

    print("Authentication steps finished. No browser was started by this tool.")
    print("To request one location check, run:")
    print("  .venv/bin/python -m server.manage poll")
    print("Credentials are stored in data/google_secrets.json (0600, plaintext).")


def demo():
    dev = "demo-tag"
    db.upsert_device(dev, "Demo tag (sample data)")
    now = int(time.time())
    lat, lon = 23.7806, 90.4070  # start point
    locs = []
    t = now - 365 * 86400
    while t < now:
        # drift around with occasional trips
        step = 0.02 if random.random() < 0.05 else 0.002
        ang = random.random() * 2 * math.pi
        lat += step * math.sin(ang)
        lon += step * math.cos(ang)
        locs.append({"ts": t, "lat": round(lat, 6), "lon": round(lon, 6),
                     "accuracy": random.randint(5, 60), "is_own_report": random.random() < 0.3})
        t += random.randint(600, 3600)
    added = db.insert_locations(dev, locs)
    print(f"Inserted {added} demo points.")


def restore():
    from . import backup
    from .config import DB_PATH

    print("Stop the server before restoring. Downloading backup...")
    tmp = backup.restore()
    if DB_PATH.exists():
        old = DB_PATH.with_suffix(f".before-restore-{int(time.time())}.db")
        DB_PATH.rename(old)
        print(f"Current database kept as {old.name}")
    for ext in ("-wal", "-shm"):
        p = DB_PATH.with_name(DB_PATH.name + ext)
        if p.exists():
            p.unlink()
    tmp.rename(DB_PATH)
    print("Restored. Start the server again.")


def main(argv):
    db.init()
    if len(argv) < 1:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "adduser" and len(args) == 1:
        adduser(args[0])
    elif cmd == "deluser" and len(args) == 1:
        print("Deleted." if db.delete_user(args[0]) else "No such user.")
    elif cmd == "users":
        print("\n".join(db.list_users()) or "(no users)")
    elif cmd == "google-login":
        google_login()
    elif cmd == "google-manual-login" and not args:
        google_manual_login()
    elif cmd == "poll":
        from . import poller
        poller.poll_once()
    elif cmd == "demo":
        demo()
    elif cmd == "backup":
        from . import backup
        backup.run_once(full_csv=True)
        print("Backup uploaded.")
    elif cmd == "restore":
        restore()
    elif cmd == "import-csv" and len(args) == 1:
        print(f"Imported {db.import_csv(args[0])} new locations.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
