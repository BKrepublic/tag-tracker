"""Regression tests for the Linux server security changes (no Google access)."""
import atexit
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# Must be set before any Tag Tracker server modules are imported.
_TEST_DIR = tempfile.TemporaryDirectory(prefix="tag-tracker-security-test-")
atexit.register(_TEST_DIR.cleanup)
os.environ["TT_DATA_DIR"] = str(Path(_TEST_DIR.name) / "data")
os.environ["TT_POLLING"] = "0"
os.environ["TT_RCLONE_REMOTE"] = ""

from fastapi.testclient import TestClient
from server import config, db
from server.app import app
from server.safe_chrome import create_driver


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def test_private_data_permissions(self):
        self.assertEqual(stat.S_IMODE(config.DATA_DIR.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((config.DATA_DIR / "session_secret.txt").stat().st_mode),
            0o600,
        )
        self.assertEqual(config.HOST, "127.0.0.1")
        self.assertGreaterEqual(config.POLL_MINUTES, 5)

    def test_deleted_user_session_is_rejected(self):
        db.upsert_user("delete-test", "safe-password-123")
        with TestClient(app) as client:
            res = client.post("/api/login", json={
                "username": "delete-test", "password": "safe-password-123"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(client.get("/api/me").status_code, 200)
            self.assertTrue(db.delete_user("delete-test"))
            self.assertEqual(client.get("/api/me").status_code, 401)
            self.assertEqual(client.get("/", follow_redirects=False).status_code, 307)

    def test_password_reset_invalidates_session(self):
        db.upsert_user("reset-test", "first-password-123")
        with TestClient(app) as client:
            res = client.post("/api/login", json={
                "username": "reset-test", "password": "first-password-123"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(client.get("/api/me").status_code, 200)
            db.upsert_user("reset-test", "new-password-456")
            self.assertEqual(client.get("/api/me").status_code, 401)

    def test_detects_arch_google_chrome_stable(self):
        from server.safe_chrome import _select_browser_binary

        def fake_which(name):
            return "/usr/bin/google-chrome-stable" if name == "google-chrome-stable" else None

        with patch("server.safe_chrome.shutil.which", side_effect=fake_which):
            self.assertEqual(_select_browser_binary(), "/usr/bin/google-chrome-stable")
            with self.assertRaisesRegex(RuntimeError, "Matching Chromium"):
                create_driver()

    def test_safe_browser_requires_explicit_driver(self):
        with patch("server.safe_chrome.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Matching Chromium"):
                create_driver()

    def test_safe_browser_rejects_mismatched_versions(self):
        def fake_version(args, **kwargs):
            if args[0].endswith("chromium"):
                return "Chromium 156.0.8000.1"
            return "ChromeDriver 155.0.8000.1"
        with patch("server.safe_chrome.shutil.which", side_effect=lambda name: "/usr/bin/" + name):
            with patch("server.safe_chrome.subprocess.check_output", side_effect=fake_version):
                with self.assertRaisesRegex(RuntimeError, "major versions differ"):
                    create_driver()

    def test_scrypt_compat_rfc7914_vector(self):
        from server.pyscrypt_compat import hash as scrypt_hash
        expected = (
            "77d6576238657b203b19ca42c18a0497f16b4844e3074ae8dfdffa3fede21442"
            "fcd0069ded0948f8326a753a0fc81f17e8d3e0fb2e0d3628cf35e20c38d18906"
        )
        self.assertEqual(scrypt_hash(b"", b"", 16, 1, 1, 64).hex(), expected)

    def test_upstream_scrypt_import_uses_compat(self):
        from server import finder, pyscrypt_compat
        finder._ensure_imported()
        from KeyBackup import lskf_hasher
        self.assertIs(lskf_hasher.pyscrypt, pyscrypt_compat)

    def test_upstream_auth_imports_use_safe_driver(self):
        from server import finder
        finder._ensure_imported()  # import only: no Google network request
        from Auth import auth_flow
        from KeyBackup import shared_key_flow
        self.assertIs(auth_flow.create_driver, create_driver)
        self.assertIs(shared_key_flow.create_driver, create_driver)
        self.assertNotIn("undetected_chromedriver", sys.modules)



    def test_manual_google_login_does_not_echo_secrets(self):
        import contextlib
        import io
        from server import manage
        captured = io.StringIO()
        with patch("server.finder.auth_state", side_effect=[
            {"signed_in": False, "unlocked": False},
            {"signed_in": True, "unlocked": False},
        ]), patch("server.finder.prepare_google_sign_in"), \
             patch("builtins.input", return_value="user@example.test"), \
             patch("server.finder.sign_in_with_oauth_token", return_value="user@example.test") as sign_in, \
             patch("server.finder.shared_key_url", return_value="https://accounts.google.com/encryption/unlock/android?kdi=test"), \
             patch("server.finder.save_vault_keys") as save, \
             patch("server.manage.getpass.getpass", side_effect=["oauth2_4/SUPER_SECRET_TOKEN", '{"finder_hw":[]}']), \
             contextlib.redirect_stdout(captured):
            manage.google_manual_login()
        sign_in.assert_called_once_with("oauth2_4/SUPER_SECRET_TOKEN", email="user@example.test")
        save.assert_called_once_with('{"finder_hw":[]}')
        self.assertNotIn("SUPER_SECRET_TOKEN", captured.getvalue())
        self.assertNotIn('{"finder_hw":[]}', captured.getvalue())
        self.assertIn("capture", captured.getvalue())

    def test_manual_login_reports_safe_google_error(self):
        import contextlib
        import io
        from server import finder, manage
        with patch("server.finder.auth_state", return_value={"signed_in": False, "unlocked": False}), \
             patch("server.finder.prepare_google_sign_in"), \
             patch("builtins.input", return_value="user@example.test"), \
             patch("server.manage.getpass.getpass", return_value="oauth2_4/PRIVATE_COOKIE"), \
             patch("server.finder.sign_in_with_oauth_token", side_effect=finder.GoogleTokenExchangeError(
                 "Google token exchange rejected: BadAuthentication"
             )), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                manage.google_manual_login()
        self.assertIn("BadAuthentication", str(caught.exception))
        self.assertNotIn("PRIVATE_COOKIE", str(caught.exception))


    def test_fcm_compat_fixes_padding_and_skips_unrelated_pushes(self):
        from server.fcm_compat import _normalize_header, install_fcm_patch
        import binascii
        self.assertEqual(_normalize_header("abC;p256ecdsa=def"), "abC=")
        self.assertEqual(_normalize_header("abcdefgh"), "abcdefgh")

        class FakeReceiver:
            _decrypt_raw_data = staticmethod(
                lambda credentials, ck, salt, raw: (ck, salt, raw)
            )

            def __init__(self):
                self.credentials = {"gcm": {"app_id": "expected"}}
                self.calls = 0

            def _app_data_by_key(self, msg, key, do_not_raise=False):
                return msg.get(key, "")

            def _handle_data_message(self, msg):
                self.calls += 1
                if msg.get("invalid"):
                    raise binascii.Error("Incorrect padding")
                return "received"

        install_fcm_patch(FakeReceiver)
        install_fcm_patch(FakeReceiver)  # idempotent
        self.assertEqual(
            FakeReceiver._decrypt_raw_data({}, "A-B;p=xyz", "abcd", b"x"),
            ("A-B=", "abcd", b"x"),
        )
        receiver = FakeReceiver()
        self.assertIsNone(receiver._handle_data_message({"subtype": "foreign"}))
        self.assertEqual(receiver.calls, 0)
        self.assertEqual(receiver._handle_data_message({"subtype": "expected"}), "received")
        self.assertIsNone(receiver._handle_data_message({"subtype": "expected", "invalid": True}))
        self.assertEqual(receiver.calls, 2)

    def test_tag_device_list_excludes_phones(self):
        from server import finder
        finder._ensure_imported()  # vendored protobuf path is inserted lazily
        from ProtoDecoders import DeviceUpdate_pb2 as pb

        response = pb.DevicesList()
        phone = response.deviceMetadata.add()
        phone.userDefinedDeviceName = "Jelly Star"
        phone.identifierInformation.type = pb.IDENTIFIER_ANDROID
        phone.identifierInformation.phoneInformation.canonicIds.canonicId.add().id = "PHONE_SECRET_ID"
        tag = response.deviceMetadata.add()
        tag.userDefinedDeviceName = "Loshall Tag"
        tag.identifierInformation.type = pb.IDENTIFIER_SPOT
        tag.identifierInformation.canonicIds.canonicId.add().id = "SPOT_SECRET_ID"

        with patch("NovaApi.ListDevices.nbe_list_devices.request_device_list",
                   return_value=response.SerializeToString().hex()):
            self.assertEqual(finder.list_trackers(), [("Loshall Tag", "SPOT_SECRET_ID")])
            summary = finder.list_device_summary()
            self.assertEqual(
                [(row["name"], row["kind"]) for row in summary],
                [("Jelly Star", "ANDROID"), ("Loshall Tag", "SPOT")],
            )
            self.assertNotIn("SECRET_ID", str(summary))

    def test_manual_auth_script_is_local_only(self):
        script = (Path(__file__).resolve().parents[1] / "tools" / "capture-vault-keys.js").read_text()
        self.assertIn("setVaultSharedKeys", script)
        self.assertIn('location.origin !== "https://accounts.google.com"', script)
        self.assertIn('location.pathname === "/v3/signin/challenge/kls"', script)
        self.assertIn('new URLSearchParams(location.search).get("flowName") === "EncryptionUnlockAndroid"', script)
        for disallowed in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket("):
            self.assertNotIn(disallowed, script)

if __name__ == "__main__":
    unittest.main()
