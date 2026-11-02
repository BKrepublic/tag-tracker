#!/usr/bin/env sh
# Linux / macOS / Raspberry Pi installer. No side effects unless --install is passed.
set -eu

GFMT_COMMIT=d46e9528578015b51d3b84dd91bf8f16e9ab850f
UPSTREAM=https://github.com/leonboe1/GoogleFindMyTools

if [ "${1:-}" != "--install" ] || [ "$#" -ne 1 ]; then
    cat <<EOF
Tag Tracker local setup (no changes made).
To install, inspect setup.sh and requirements first, then run:
  sh setup.sh --install

This command will:
  - clone GoogleFindMyTools and check out pinned commit $GFMT_COMMIT
  - create the local .venv if missing
  - install pre-built Python wheels into .venv (no source builds)
  - exclude upstream stealth driver, Frida and source-only pyscrypt
  - install audited, vendored http_ece wheel (Git object checked)

No sudo, Google login, browser launch, or server startup.
Python dependencies are not fully hash-pinned. Review SECURITY-HARDENING.md.
EOF
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    echo "Refusing to run the installer as root." >&2
    exit 1
fi

if [ ! -f requirements.txt ] || [ ! -f server/manage.py ]; then
    echo "Run this command from the tag-tracker repository root." >&2
    exit 1
fi

mkdir -p vendor
if [ -e vendor/GoogleFindMyTools ] && [ ! -d vendor/GoogleFindMyTools/.git ]; then
    echo "Existing vendor/GoogleFindMyTools is not a Git clone; refusing to overwrite it." >&2
    exit 1
fi

if [ ! -d vendor/GoogleFindMyTools ]; then
    git clone "$UPSTREAM" vendor/GoogleFindMyTools
fi

origin=$(git -C vendor/GoogleFindMyTools remote get-url origin)
if [ "$origin" != "$UPSTREAM" ] && [ "$origin" != "$UPSTREAM.git" ]; then
    echo "Unexpected GoogleFindMyTools origin: $origin" >&2
    exit 1
fi
git -C vendor/GoogleFindMyTools fetch --quiet origin
git -C vendor/GoogleFindMyTools checkout --quiet "$GFMT_COMMIT"
if [ "$(git -C vendor/GoogleFindMyTools rev-parse HEAD)" != "$GFMT_COMMIT" ]; then
    echo "Pinned GoogleFindMyTools checkout failed." >&2
    exit 1
fi

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
# The pinned upstream lists source-only Chrome and scrypt packages.
# Use a system ChromeDriver and hashlib.scrypt; filter these requirements
# without changing pinned vendored sources.
vendor_req=$(mktemp)
trap 'rm -f "$vendor_req"' EXIT HUP INT TERM
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        undetected-chromedriver\>=*|frida\>=*|pyscrypt\>=*|http_ece\>=*) continue ;;
        -*|git+*|http://*|https://*) echo "Unsupported upstream requirement: $line" >&2; exit 1 ;;
        *) printf '%s\n' "$line" >> "$vendor_req" ;;
    esac
done < vendor/GoogleFindMyTools/requirements.txt

# PyPI publishes http_ece only as a source archive. Use the pure-Python
# wheel already audited with the Android build, and verify its exact Git blob.
ece_wheel=android/app/wheels/http_ece-1.2.1-py2.py3-none-any.whl
ece_blob=e37e573977a368292c312247ce6ac174548875db
if [ ! -f "$ece_wheel" ] || [ "$(git hash-object "$ece_wheel")" != "$ece_blob" ]; then
    echo "Vendored http_ece wheel is missing or does not match the reviewed Git blob." >&2
    exit 1
fi

.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt -r "$vendor_req" "$ece_wheel"
.venv/bin/python -m pip check

echo
echo "Dependencies installed in .venv. No Google sign-in was performed."
echo "Before signing in, run: .venv/bin/python -m unittest discover -s tests -v"
