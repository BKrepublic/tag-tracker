"""Compatibility fixes for GoogleFindMyTools' pinned FCM receiver.

The upstream receiver dies on unpadded FCM base64url headers and tries to
decrypt messages for different app IDs. Based on upstream PR #114.
Patches only the in-process class; never writes to the vendored checkout.
"""
import binascii


def _normalize_header(value: str) -> str:
    """Use the first header parameter and add missing RFC 4648 padding."""
    value = value.split(";", 1)[0].strip()
    return value + "=" * (-len(value) % 4)


def install_fcm_patch(client_class) -> None:
    if getattr(client_class, "_tag_tracker_fcm_patched", False):
        return

    original_decrypt = client_class._decrypt_raw_data
    original_handle = client_class._handle_data_message

    @staticmethod
    def decrypt(credentials, crypto_key, salt, raw_data):
        return original_decrypt(
            credentials,
            _normalize_header(crypto_key),
            _normalize_header(salt),
            raw_data,
        )

    def handle(self, msg):
        # MCS carries pushes intended for other apps under the same Android ID.
        # Only our registered subtype may be decrypted with our key material.
        if not self.credentials:
            return
        if self._app_data_by_key(msg, "message_type", do_not_raise=True) == "deleted_messages":
            return
        expected = self.credentials.get("gcm", {}).get("app_id")
        subtype = self._app_data_by_key(msg, "subtype", do_not_raise=True)
        if not expected or subtype != expected:
            return
        try:
            return original_handle(self, msg)
        except (binascii.Error, ValueError, TypeError, KeyError) as exc:
            # A malformed push must not terminate the entire listener.
            # Don't log the header values or key material.
            print(f"[fcm] skipped malformed notification ({type(exc).__name__})")

    client_class._decrypt_raw_data = decrypt
    client_class._handle_data_message = handle
    client_class._tag_tracker_fcm_patched = True
