# Tag Tracker — Linux server security hardening

This fork keeps the original Tag Tracker features while reducing avoidable
risks when collecting your own Find Hub tag history. This is **not** a complete
security audit, nor a guarantee that Google account access is safe.

## Applied changes

- Default bind address changed from `0.0.0.0` to `127.0.0.1`.
  Docker Compose publishes only `127.0.0.1:8000:8000`; its container
  process still listens on `0.0.0.0` so Docker's port mapping works.
- The private data directory is mode 0700; the Google credentials file
  and session secret are mode 0600. New files inherit restrictive permissions
  via umask 077. Existing paths are permission-corrected on startup.
- Signed sessions are bound to the user's current password hash using a
  server-side HMAC. Deleting a user or resetting their password invalidates
  existing browser sessions, including the web index route.
- The desktop Google login overrides the pinned GoogleFindMyTools' unsafe
  Chrome launcher without editing vendored source: it does not `pkill` Chrome
  and does not explicitly pass `--no-sandbox`.
- Nominatim reverse geocoding is off by default. It can be enabled in the
  web UI with an explicit checkbox.
- Minimum polling interval is five minutes, to avoid inadvertent very
  high request rates.

## Important remaining risks

- Google account tokens and Find Hub decryption keys remain **plaintext at
  rest**. File modes prevent access by other Unix users, but **do not**
  protect against programs running as the same user, root, malware, or backups.
  If the host is shared or untrusted, avoid signing in with a valuable
  Google account.
- Chrome sign-in still uses **undetected-chromedriver**; disabling sandbox
  is no longer requested by this launcher, but dependency behavior and
  Chromium security must be evaluated separately. The real sign-in flow
  has not been tested with Google.
- GoogleFindMyTools uses a **private, unsupported Google API**. Google could
  restrict access or change its behavior at any time.
- The unresolved `python-ecdsa` timing side-channel advisory and
  dependency-chain update risks have not been eliminated. Android bundled
  libraries and APK behavior are **out of scope** for this Linux fix.
- The map uses external tile providers and CDN-hosted Leaflet assets.
  Viewing a map can disclose viewed map areas and network metadata to
  those providers even when Nominatim lookup is disabled.
- The local HTTP server is intended for localhost. **Do not open port 8000**
  to the public Internet; HTTPS, secure cookies, and authorization hardening
  are required before publishing behind a proxy.
- Older login cookies will be invalid after this change; sign in again.

## Offline check (does not use Google)

Run `python -m unittest discover -s tests -v` from the repository root after
installing its regular Python dependencies. The tests only use a temporary
database and mock the Chrome launcher.

The `setup.sh` installer and upstream dependencies were not rewritten here.
No Google login or live tag polling was performed for this change.
