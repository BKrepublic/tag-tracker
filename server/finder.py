"""Adapter around GoogleFindMyTools (https://github.com/leonboe1/GoogleFindMyTools).

Google's Find Hub has no public API. GoogleFindMyTools reimplements the private
API used by the Find Hub app, including decryption of the end-to-end encrypted
location reports. This module wraps it so the rest of the app just gets plain
dicts with lat/lon/timestamp.
"""
import hashlib
import importlib.util
import json
import re
import sys
import threading
import types

from .config import GFMT_DIR, GOOGLE_SECRETS_PATH

_imported = False
_dispatch_lock = threading.Lock()
_pending: dict[str, dict] = {}  # request uuid -> {"event": Event, "result": hex}
_listener_registered = False


def _stub_browser_modules():
    """On Android there is no Chrome/Selenium; the sign-in steps are done in a WebView instead.
    The library still imports these at module level, so give it harmless placeholders."""
    names = ["selenium", "selenium.webdriver", "selenium.webdriver.support",
             "selenium.webdriver.support.ui", "selenium.webdriver.support.expected_conditions",
             "undetected_chromedriver"]
    for n in names:
        sys.modules.setdefault(n, types.ModuleType(n))
    sys.modules["selenium.webdriver.support.ui"].WebDriverWait = None


def _ensure_imported():
    global _imported
    if _imported:
        return
    if importlib.util.find_spec("NovaApi") is None:  # bundled on Android, vendored elsewhere
        if not (GFMT_DIR / "NovaApi").exists():
            raise RuntimeError(
                f"GoogleFindMyTools not found at {GFMT_DIR}. Run the setup script first (see README)."
            )
        sys.path.insert(0, str(GFMT_DIR))
    if importlib.util.find_spec("selenium") is None:
        _stub_browser_modules()
    # Prefer OpenSSL-backed hashlib.scrypt to upstream's source-only wrapper.
    if importlib.util.find_spec("pyscrypt") is None:
        from . import pyscrypt_compat
        sys.modules["pyscrypt"] = pyscrypt_compat
    # Keep Google tokens in our data dir instead of inside the vendored library.
    import Auth.token_cache as token_cache
    token_cache._get_secrets_file = lambda: str(GOOGLE_SECRETS_PATH)
    # Upstream chrome_driver imports undetected_chromedriver at module load.
    # Register a safe launcher before upstream auth or key retrieval imports it.
    # On Android the non-browser authentication path stays unchanged.
    from .safe_chrome import create_driver
    chrome_driver = types.ModuleType("chrome_driver")
    chrome_driver.create_driver = create_driver
    sys.modules["chrome_driver"] = chrome_driver
    # Upstream's pinned FCM client crashes on base64url padding and on pushes
    # intended for a different app. Apply the minimal in-memory compatibility fix.
    from .fcm_compat import install_fcm_patch
    from Auth.firebase_messaging.fcmpushclient import FcmPushClient
    install_fcm_patch(FcmPushClient)
    _imported = True


# ---------- sign-in without Chrome (used by the Android app's WebView) ----------

def is_connected() -> bool:
    _ensure_imported()
    from Auth.token_cache import get_cached_value
    return bool(get_cached_value("aas_token") and get_cached_value("shared_key"))


def auth_state() -> dict:
    """The two sign-in steps, tracked separately so the UI can show progress.
    signed_in = step 1 (account token, enough to list tags);
    unlocked  = step 2 (shared key, needed to decrypt locations)."""
    _ensure_imported()
    from Auth.token_cache import get_cached_value
    return {
        "signed_in": bool(get_cached_value("aas_token")),
        "unlocked": bool(get_cached_value("shared_key")),
        "email": get_cached_value("username") or "",
    }


def account_email() -> str:
    _ensure_imported()
    from Auth.token_cache import get_cached_value
    return get_cached_value("username") or ""


class GoogleTokenExchangeError(RuntimeError):
    """Safe-to-display token-exchange failure, without secrets or response dumps."""


def prepare_google_sign_in() -> None:
    """Set up the FCM Android identity before the short-lived cookie is copied."""
    _ensure_imported()
    from Auth.fcm_receiver import FcmReceiver

    if not FcmReceiver().get_android_id():
        raise RuntimeError("FCM Android device registration did not return an ID.")


def sign_in_with_oauth_token(oauth_token: str, email: str = "") -> str:
    """Exchange the one-time Google EmbeddedSetup cookie for a longer-lived AAS token."""
    _ensure_imported()
    import gpsoauth
    from Auth.fcm_receiver import FcmReceiver
    from Auth.token_cache import set_cached_value

    android_id = FcmReceiver().get_android_id()
    if not android_id:
        raise RuntimeError("Could not register with Google (no network?). Please try again.")
    resp = gpsoauth.exchange_token(email, oauth_token, android_id)
    if "Token" not in resp:
        # Only show an alphanumeric Google error code; never print raw API responses.
        raw_code = resp.get("Error", "NoToken") if isinstance(resp, dict) else "InvalidResponse"
        code = raw_code if isinstance(raw_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw_code) else "UnknownError"
        raise GoogleTokenExchangeError(f"Google token exchange rejected: {code}")
    set_cached_value("aas_token", resp["Token"])
    if "Email" in resp:
        set_cached_value("username", resp["Email"])
    return resp.get("Email", "")


def import_secrets_file(src_path: str) -> dict:
    """Reliable alternative to the in-app login: copy a secrets file made by the desktop
    'google-login' into place. No new Google auth happens, so it can't trip account
    protection. Returns the resulting auth_state."""
    import json
    import shutil

    _ensure_imported()
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("aas_token"):
        raise RuntimeError("This file isn't a valid Google login file (no account token found).")
    GOOGLE_SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if GOOGLE_SECRETS_PATH.is_symlink():
        raise RuntimeError("Refusing to overwrite a symlinked secrets file")
    shutil.copyfile(src_path, GOOGLE_SECRETS_PATH)
    GOOGLE_SECRETS_PATH.chmod(0o600)
    return auth_state()


def shared_key_url() -> str:
    _ensure_imported()
    from KeyBackup.shared_key_request import get_security_domain_request_url
    return get_security_domain_request_url()


def save_vault_keys(vault_keys) -> None:
    """Called with the vaultKeys the Google 'unlock' page hands to window.mm.setVaultSharedKeys."""
    _ensure_imported()
    from Auth.token_cache import set_cached_value
    from KeyBackup.response_parser import get_fmdn_shared_key

    if not isinstance(vault_keys, str):
        vault_keys = json.dumps(vault_keys)
    set_cached_value("shared_key", get_fmdn_shared_key(vault_keys).hex())


def _device_list():
    _ensure_imported()
    from NovaApi.ListDevices.nbe_list_devices import request_device_list
    from ProtoDecoders.decoder import parse_device_list_protobuf

    return parse_device_list_protobuf(request_device_list())


def list_device_summary() -> list[dict]:
    """Return device names/types only; never expose canonical IDs or credentials."""
    from ProtoDecoders import DeviceUpdate_pb2

    result = []
    for device in _device_list().deviceMetadata:
        info = device.identifierInformation
        kind = {
            DeviceUpdate_pb2.IDENTIFIER_ANDROID: "ANDROID",
            DeviceUpdate_pb2.IDENTIFIER_SPOT: "SPOT",
        }.get(info.type, f"OTHER({info.type})")
        ids = (
            info.phoneInformation.canonicIds.canonicId
            if info.type == DeviceUpdate_pb2.IDENTIFIER_ANDROID
            else info.canonicIds.canonicId
        )
        result.append({"name": device.userDefinedDeviceName or "(unnamed)",
                       "kind": kind, "ids": len(ids)})
    return result


def list_trackers() -> list[tuple[str, str]]:
    """Return only real SPOT tags, not Android phones/tablets."""
    from ProtoDecoders import DeviceUpdate_pb2

    result = []
    for device in _device_list().deviceMetadata:
        if device.identifierInformation.type != DeviceUpdate_pb2.IDENTIFIER_SPOT:
            continue
        for item in device.identifierInformation.canonicIds.canonicId:
            if item.id:
                result.append((device.userDefinedDeviceName or "(unnamed)", item.id))
    return result


def _on_fcm_message(hex_string: str):
    from ProtoDecoders.decoder import parse_device_update_protobuf

    try:
        update = parse_device_update_protobuf(hex_string)
        uuid = update.fcmMetadata.requestUuid
    except Exception:
        return
    with _dispatch_lock:
        slot = _pending.get(uuid)
    if slot is not None:
        slot["result"] = hex_string
        slot["event"].set()


def _register_listener() -> str:
    global _listener_registered
    from Auth.fcm_receiver import FcmReceiver

    receiver = FcmReceiver()
    if not _listener_registered:
        token = receiver.register_for_location_updates(_on_fcm_message)
        _listener_registered = True
        return token
    return receiver.credentials["fcm"]["registration"]["token"]


def locate(canonic_id: str, timeout: float = 45) -> list[dict]:
    """Asks Google for the tag's recent locations and returns decrypted points."""
    _ensure_imported()
    from NovaApi.ExecuteAction.LocateTracker.location_request import create_location_request
    from NovaApi.nova_request import nova_request
    from NovaApi.scopes import NOVA_ACTION_API_SCOPE
    from NovaApi.util import generate_random_uuid
    from ProtoDecoders.decoder import parse_device_update_protobuf

    fcm_token = _register_listener()
    request_uuid = generate_random_uuid()
    slot = {"event": threading.Event(), "result": None}
    with _dispatch_lock:
        _pending[request_uuid] = slot
    try:
        nova_request(NOVA_ACTION_API_SCOPE, create_location_request(canonic_id, fcm_token, request_uuid))
        if not slot["event"].wait(timeout):
            raise TimeoutError("No location response from Google within %ss" % timeout)
    finally:
        with _dispatch_lock:
            _pending.pop(request_uuid, None)

    return _decrypt(parse_device_update_protobuf(slot["result"]))


def stop_listening() -> None:
    """Drops the FCM connection between checks so the app uses almost no battery while idle.
    The next locate() re-registers quickly."""
    global _listener_registered
    if _listener_registered:
        try:
            from Auth.fcm_receiver import FcmReceiver
            FcmReceiver().stop_listening()
        except Exception:
            pass
        _listener_registered = False


def play_sound(canonic_id: str, start: bool = True) -> None:
    """Rings (or stops ringing) the tag, like Find Hub's 'Play sound'."""
    _ensure_imported()
    from NovaApi.ExecuteAction.PlaySound.sound_request import create_sound_request
    from NovaApi.nova_request import nova_request
    from NovaApi.scopes import NOVA_ACTION_API_SCOPE

    fcm_token = _register_listener()
    nova_request(NOVA_ACTION_API_SCOPE, create_sound_request(start, canonic_id, fcm_token))


def _decrypt(device_update) -> list[dict]:
    """Same logic as GoogleFindMyTools' decrypt_location_response_locations, but returns data."""
    from FMDNCrypto.foreign_tracker_cryptor import decrypt
    from KeyBackup.cloud_key_decryptor import decrypt_aes_gcm
    from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import is_mcu_tracker, retrieve_identity_key
    from ProtoDecoders import Common_pb2, DeviceUpdate_pb2

    registration = device_update.deviceMetadata.information.deviceRegistration
    try:
        identity_key = retrieve_identity_key(registration)
    except SystemExit:
        raise RuntimeError(
            "Could not decrypt this tag's key. If you reset Find Hub end-to-end encryption, "
            "delete data/google_secrets.json and run the Google login setup again."
        )
    is_mcu = is_mcu_tracker(registration)
    reports = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations

    pairs = list(zip(reports.networkLocations, reports.networkLocationTimestamps))
    if reports.HasField("recentLocation"):
        pairs.append((reports.recentLocation, reports.recentLocationTimestamp))

    out = []
    for loc, t in pairs:
        ts = int(t.seconds)
        if loc.status == Common_pb2.Status.SEMANTIC:
            out.append({"ts": ts, "semantic_name": loc.semanticLocation.locationName, "is_own_report": True})
            continue
        enc = loc.geoLocation.encryptedReport
        try:
            if enc.publicKeyRandom == b"":
                raw = decrypt_aes_gcm(hashlib.sha256(identity_key).digest(), enc.encryptedLocation)
            else:
                offset = 0 if is_mcu else loc.geoLocation.deviceTimeOffset
                raw = decrypt(identity_key, enc.encryptedLocation, enc.publicKeyRandom, offset)
            p = DeviceUpdate_pb2.Location()
            p.ParseFromString(raw)
        except Exception as e:  # one bad report shouldn't drop the rest
            print(f"[finder] could not decrypt a report: {e}")
            continue
        out.append({
            "ts": ts,
            "lat": p.latitude / 1e7,
            "lon": p.longitude / 1e7,
            "altitude": p.altitude,
            "accuracy": loc.geoLocation.accuracy,
            "is_own_report": enc.isOwnReport,
        })
    return out
