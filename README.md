# Tag Tracker

**🌐 Website:** https://shekh-muhsen.github.io/tag-tracker-web/ &nbsp;·&nbsp;
**⬇ Download APK:** [OneDrive folder](https://1drv.ms/f/c/d253a14c69792d4f/IgD2Wvb7ZuMkRYcGL9R43zagAfLFCZ9MhQ_tpxYC5Lf0UwM?e=tLMbGT) (updated time to time) or [GitHub Releases](../../releases) &nbsp;·&nbsp;
**Source:** this repo (open-source, GPL-3.0)

Keeps the **full location history** of your Google **Find Hub** tags and shows it on a map.
The Find Hub app only shows where a tag is *right now*. Tag Tracker checks Find Hub every few minutes,
**saves every location it gets**, and lets you look back over today, 2 days, 7 days, 30 days, 90 days, a
full year, or any date range you choose.

- 🌐 **Web app** with login. Shows the travel path on a map, lets you replay the trip, gives distance and time stats, and exports CSV or GPX files.
- 📱 **Android app** (`android/`). GitHub Actions builds the APK automatically.
- 🔒 Private by default. Your locations and Google tokens stay on your own server, in `data/`.

## How it works

```
Tag ──BLE──▶ nearby Android phones ──▶ Google Find Hub (end-to-end encrypted)
                                             │  every N minutes
                                             ▼
                          Tag Tracker server (this repo, runs on your PC / VPS / Raspberry Pi)
                          • signs in to your Google account (one time)
                          • decrypts the reports and stores them in SQLite
                                             │
                               web browser  /  Android app  (login required)
```

Google has no public Find Hub API. The server uses the open-source
[GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools), which works like the official Find Hub app does.

**Things to know**

- History starts **when you start the server**. Google does not keep old history, so earlier trips can't be loaded.
- The server only records **while it is running**. For unbroken history, run it on a machine that stays on, such as a cheap VPS, a Raspberry Pi or an always-on PC.
- The tag reports a location only when an Android phone passes near it. Quiet areas give fewer points.
- On your phone, open Find Hub settings and turn on **"Find your offline devices" → "With network in all areas"** to get the most reports.
- This uses an unofficial API, so a Google change could break it for a while. If it does, update the pinned `GoogleFindMyTools` commit in `setup.ps1`, `setup.sh` and `Dockerfile`.
- Don't set the check interval too short. Every **10 minutes** (the default) is a sensible value.

## 1. Install the server (Windows)

You need Python 3.11+, Git and Google Chrome.

```powershell
git clone https://github.com/<you>/tag-tracker.git
cd tag-tracker
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Create your web login:

```powershell
.\.venv\Scripts\python -m server.manage adduser myname
```

Connect your Google account (one time only):

```powershell
.\.venv\Scripts\python -m server.manage google-login
```

A Chrome window opens. Sign in to the **Google account you use in Find Hub**. Google may also ask for
your phone's **screen lock PIN or pattern**, which unlocks the encryption key for your tags' locations. The tokens are saved to
`data/google_secrets.json`. **Keep this file private.** It gives access to your Find Hub.

Start the server:

```powershell
.\start.ps1
```

Open http://localhost:8000 and sign in.

Want to try the map before connecting your tag? Run `python -m server.manage demo` to add a sample tag with a year of fake history.

### Linux / Raspberry Pi / VPS

The Linux installer is **read-only by default**: `sh setup.sh` prints the
steps without installing anything. Review the source first and explicitly
opt in with `sh setup.sh --install` to clone a pinned GoogleFindMyTools
commit and install Python wheels into the local `.venv`. It never requires
`sudo` and does not log in to Google. The Python dependencies still use
version ranges, not a fully hash-pinned lockfile; install only if you accept
that residual supply-chain risk. The installer skips unused upstream
`undetected-chromedriver` and `frida`, replaces source-only `pyscrypt`
with `hashlib.scrypt`, and uses a checked vendored `http_ece` wheel.
See SECURITY-HARDENING.md for the remaining package-provenance risks.
Linux Google sign-in uses Selenium
with an explicitly installed, matching browser and chromedriver. On CachyOS
the official `chromium` package contains both. No browser or driver is
installed automatically.

```sh
sh setup.sh
# After reviewing the commands and their security implications:
sh setup.sh --install
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m server.manage adduser myname
# Google sign-in is a separate, sensitive step. Review SECURITY-HARDENING.md first.
.venv/bin/python -m server.manage google-login
.venv/bin/python -m server
```

The server listens on `127.0.0.1:8000` by default. In a headless
environment, conduct the Google sign-in on a trusted computer and transfer
`data/google_secrets.json` **securely** if you decide to use it.

Or use Docker: `docker compose up -d`, then run `docker compose exec tracker python -m server.manage adduser myname`.

### Alternative: normal Chrome sign-in without ChromeDriver

Google may reject Selenium-controlled Chrome with a "browser or app may not
be secure" error. The **interactive manual login** uses the Chrome browser
you normally use and never starts a ChromeDriver session. It is based on
the independently verified two-part flow described by
[ioBroker GoogleFindMyDevice](https://github.com/beabel/ioBroker.googlefindmydevice).

```sh
.venv/bin/python -m server.manage google-manual-login
```

This command guides you through Google's `EmbeddedSetup` page and then the
Find Hub encryption-unlock page. You copy one short-lived `oauth_token`
cookie and one `vaultKeys` JSON value from your **own Chrome** into
hidden local terminal prompts. The tool registers its FCM device identity
**before** the cookie is requested, prompts for your Google account email,
and reports Google's non-secret error code on failure. The `oauth_token`
value must start with `oauth2_4/`, is short-lived and single-use:
you must obtain a **fresh** cookie each time an exchange fails. Keep the
prompt open while signing in, and paste the cookie promptly.
The second step shows the entire source of
[`tools/capture-vault-keys.js`](tools/capture-vault-keys.js) for inspection
before you paste it into **the Google page only**. It only intercepts the
Google page's `setVaultSharedKeys` callback and displays the returned JSON
in a local text box; it never sends it to another server.

**Security:** The cookie and vaultKeys are sensitive credentials. Do not
paste either into a chat, public website, shell command arguments, browser
extension, or issue report. Only use this on a machine you trust. Terminal
input is hidden and the temporary cookie is not written to a file by the
manual-login command. The exchanged AAS token and the shared decryption key
are stored as plaintext at `data/google_secrets.json` with restrictive
permissions. If the command is interrupted after Step 1, rerun it; Step 1
will be skipped when already authenticated. The Google login or unlock
page could change; this path is not an official or supported Google API.

After successful authentication:

```sh
.venv/bin/python -m server.manage poll
.venv/bin/python -m server.manage adduser myname
.venv/bin/python -m server
```

Then open `http://127.0.0.1:8000` to inspect saved positions.
The browser-based unlock flow must succeed before locations can be decrypted.

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `TT_POLL_MINUTES` | `10` | How often to check Find Hub |
| `TT_PORT` | `8000` | Web port |
| `TT_DATA_DIR` | `./data` | Folder for the database and secrets |
| `TT_COOKIE_SECURE` | `0` | Set to `1` when you serve the site over HTTPS |
| `TT_SESSION_DAYS` | `30` | How long a login lasts |
| `TT_RCLONE_REMOTE` | *(off)* | Google Drive backup target, e.g. `gdrive:TagTracker` |
| `TT_BACKUP_MINUTES` | `60` | How often to back up |

## Google Drive backup (keep your history safe online)

The server can copy your data to Google Drive every hour. It uploads:
- `tracker.db`: the full database, which you can restore on any machine
- `csv/locations-YYYY-MM.csv`: one file per month that you can open in Google Sheets

Google sign-in tokens are **never** uploaded.

1. Install [rclone](https://rclone.org/downloads/). On Windows, run `winget install Rclone.Rclone`.
2. Connect it to your Drive: run `rclone config` and choose **n** (new remote). Name it `gdrive`, pick storage **drive**, and leave
   client id and secret blank. Choose scope **1** (full access) or **3** (`drive.file`, which only sees files rclone creates). Accept the rest,
   then sign in to Google in the browser that opens.
3. Turn on the backup before you start the server:
   ```powershell
   $env:TT_RCLONE_REMOTE = "gdrive:TagTracker"
   .\start.ps1
   ```
   A `TagTracker` folder appears in your Drive. The status line in the web app shows when the last backup ran.

Other commands: `python -m server.manage backup` uploads a backup now. `python -m server.manage restore` downloads the
Drive backup onto a new machine. Stop the server before you restore.

With Docker, copy your `rclone.conf` (run `rclone config file` to find it) into `data/`, then set `TT_RCLONE_REMOTE` in `docker-compose.yml`.

## 2. Reach it from your phone and from anywhere

Pick one:

- **Tailscale** (easiest and private): install Tailscale on the server and on your phone. Then use `http://<server-tailscale-name>:8000`.
- **Cloudflare Tunnel** (a public HTTPS address, no router changes): run `cloudflared tunnel --url http://localhost:8000`, or set up a named tunnel. Then set `TT_COOKIE_SECURE=1`.
- A VPS with a reverse proxy (Caddy or nginx) and HTTPS.

Don't open port 8000 on your router without HTTPS.

## Android app (works on its own — no server needed)

The Android app is **standalone**: it signs in to Google, checks Find Hub **in the background**, stores the
history on the phone, and backs it up to **Google Drive**. You do not need to run the Python server for the app.
(The server above is an optional alternative for people who want an always-on machine and a web login.)

**Install:** open **Actions → Android APK → latest run** in GitHub and download the **TagTracker-apk** artifact
(or grab the APK from the latest [Release](../../releases)). Unzip if needed, copy to your phone, and install it
(allow "install unknown apps"). To cut a release with the APK attached: `git tag v2.0 && git push --tags`.

**First run** opens a setup screen:
1. **Sign in to Google** — a Google page opens; sign in with the account you use in Find Hub.
2. **Unlock encryption keys** — a Google page asks for your phone's screen lock (PIN/pattern). This releases the
   keys that decrypt the tag's location. Your password and PIN are typed into Google's own pages; the app never sees them.
3. **Background checking** — pick how often to check (15 min minimum, an Android limit) and tap **Allow background
   running** so Android doesn't suspend it.
4. **Back up to Google Drive** — tap **Choose Google Drive file** and create a file such as `TagTracker-history.csv`
   inside your Drive. The app overwrites it with your full history every hour. You can open it in Google Sheets.

Then tap **Open map** for the same map, history ranges and trip replay as the web app.

*How it runs offline:* the app bundles the Python location code (via [Chaquopy](https://chaquo.com/chaquopy/)) and
runs it with Android WorkManager, so checks continue when the app is closed and after a reboot. Because of Android
battery limits, exact timing varies and the phone must have a network connection when a check runs.

*Note on Drive:* the app writes to **one Drive file you pick**, using the normal file picker, so there is no Google
Cloud project or API key to set up. It does not need the full server's rclone setup.

*Optional: stable signing.* By default each build is signed with a temporary debug key, so moving to a newer
build may mean uninstalling the old one first. To avoid that, create a keystore and add these repository secrets:
`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`.

## Admin commands

```
python -m server.manage adduser <name>   # add a user or reset their password
python -m server.manage deluser <name>
python -m server.manage users
python -m server.manage poll             # fetch locations once
```

## Security notes for the self-hosted Linux server

The self-hosted Python server binds to `127.0.0.1:8000` by default; Docker Compose also
publishes the port on host loopback only. This version hardens file permissions
and invalidates sessions on account deletion/password reset. See [SECURITY-HARDENING.md](SECURITY-HARDENING.md)
for the scope and remaining risks. The Android app has **not** been audited or hardened
by this change.

Reverse geocoding is **opt-in**; enabling "Online address lookup" shares tag
coordinates with Nominatim. Map imagery still contacts the chosen tile provider
(OpenStreetMap, Google, or Esri), which can observe map areas viewed.
GoogleFindMyTools uses an unsupported Google API; sign-in and location retrieval
may fail if Google changes it. Do not publicly expose the server without
a properly configured HTTPS reverse proxy and additional safeguards.

## Legal, disclaimer & privacy

**Not affiliated with Google.** Tag Tracker is an independent, open-source project — not created,
endorsed, or supported by Google LLC. "Google", "Find Hub", "Find My Device" and "Android" are
trademarks of Google LLC, used nominatively to describe compatibility.

It reads the Find network with the **unofficial** [GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools).
Google provides no public API and **may block this at any time**; using an unofficial client **may violate
Google's Terms of Service**. **Use at your own risk**, including risk to your Google account.

**Track only your own property** — never a person without their knowledge and consent (illegal in many
places). If something is stolen, give the location to the **police**; don't confront anyone.

**Privacy:** your data stays on your phone and, if enabled, your own Google Drive. The app has **no servers
of its own** and sends **no data to the developer** or any third party.

Provided **"AS IS", without warranty**. See [LEGAL.md](LEGAL.md) for the full text.

## License

GPL-3.0, the same license as GoogleFindMyTools, which this project builds on. Full credit to Leon Böttger.
