import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("TT_DATA_DIR", ROOT / "data")).expanduser()
if DATA_DIR.is_symlink():
    raise RuntimeError("Private data directory must not be a symlink")
DATA_DIR = DATA_DIR.resolve()
GFMT_DIR = Path(os.environ.get("TT_GFMT_DIR", ROOT / "vendor" / "GoogleFindMyTools")).resolve()
DB_PATH = DATA_DIR / "tracker.db"
GOOGLE_SECRETS_PATH = DATA_DIR / "google_secrets.json"

# How often to ask Google for new tag locations. Keep >= 5 min to avoid rate limits.
POLL_MINUTES = max(5.0, float(os.environ.get("TT_POLL_MINUTES", "10")))
POLLING_ENABLED = os.environ.get("TT_POLLING", "1") != "0"

# Online backup via rclone, e.g. "gdrive:TagTracker" (empty = off).
RCLONE_REMOTE = os.environ.get("TT_RCLONE_REMOTE", "").strip()
BACKUP_MINUTES = max(5.0, float(os.environ.get("TT_BACKUP_MINUTES", "60")))

HOST = os.environ.get("TT_HOST", "127.0.0.1")
PORT = int(os.environ.get("TT_PORT", "8000"))

# Set to 1 when served over HTTPS so the session cookie is only sent securely.
COOKIE_SECURE = os.environ.get("TT_COOKIE_SECURE", "0") == "1"
SESSION_DAYS = int(os.environ.get("TT_SESSION_DAYS", "30"))

DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(DATA_DIR, 0o700)

# Restrict credentials left by earlier versions before they can be read.
for private_file in (GOOGLE_SECRETS_PATH, DATA_DIR / "session_secret.txt"):
    if private_file.is_symlink():
        raise RuntimeError(f"Private file must not be a symlink: {private_file}")
    if private_file.exists():
        os.chmod(private_file, 0o600)


def _load_secret_key() -> str:
    env = os.environ.get("TT_SECRET_KEY")
    if env:
        return env
    path = DATA_DIR / "session_secret.txt"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # Another process created it first.
        else:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(48))
    if path.is_symlink():
        raise RuntimeError("Session secret must not be a symlink")
    os.chmod(path, 0o600)
    return path.read_text().strip()


SECRET_KEY = _load_secret_key()
