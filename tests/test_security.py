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


if __name__ == "__main__":
    unittest.main()
