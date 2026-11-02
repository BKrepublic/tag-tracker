"""Decode identifiers in newer Find Hub device-list protobuf layouts.

Some Fast Pair accessories put canonical IDs in unknown identifier fields and
registration metadata under device-entry fields 10/11. Do not expose raw IDs.
Reference: beabel/ioBroker.googlefindmydevice lib/proto-fields.js.
"""

import re

_UUID = re.compile(rb"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("Truncated protobuf varint")
        b = data[pos]
        pos += 1
        value |= (b & 127) << shift
        if b < 128:
            return value, pos
    raise ValueError("Protobuf varint too long")


def length_fields(data: bytes):
    """Yield (field number, value bytes) for length-delimited wire fields."""
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        field, wire = key >> 3, key & 7
        if not field:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            _, pos = _varint(data, pos)
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
            if pos > len(data):
                raise ValueError("Truncated protobuf fixed field")
        elif wire == 2:
            length, pos = _varint(data, pos)
            if pos + length > len(data):
                raise ValueError("Truncated protobuf byte field")
            yield field, data[pos:pos + length]
            pos += length
        else:
            raise ValueError("Unsupported protobuf wire type")


def nested_field(data: bytes, path: tuple[int, ...]) -> bytes | None:
    for number in path:
        data = next((v for k, v in length_fields(data) if k == number), None)
        if data is None:
            return None
    return data


def identifier_uuid(raw: bytes, depth: int = 0) -> str | None:
    """Find a UUID in a device's identifier block, including unknown fields."""
    if depth > 5:
        return None
    try:
        for _, value in length_fields(raw):
            if _UUID.fullmatch(value):
                return value.decode("ascii")
            nested = identifier_uuid(value, depth + 1)
            if nested:
                return nested
    except ValueError:
        return None
    return None


def inspect_device(device):
    """Inspect a decoded entry without leaking identifiers or crypto secrets."""
    from ProtoDecoders import DeviceUpdate_pb2 as pb

    ident = device.identifierInformation
    phone_ids = [v.id for v in ident.phoneInformation.canonicIds.canonicId if v.id]
    direct_ids = [v.id for v in ident.canonicIds.canonicId if v.id]
    # Protobuf preserves unknown fields when re-serializing a parsed message.
    extra_uuid = identifier_uuid(ident.SerializeToString())

    alt_bytes = nested_field(device.SerializeToString(), (10, 11))
    alternate = None
    if alt_bytes is not None:
        try:
            alternate = pb.DeviceInformation.FromString(alt_bytes)
        except Exception:
            pass

    primary = device.information
    primary_identity = bool(primary.deviceRegistration.encryptedUserSecrets.encryptedIdentityKey)
    alt_identity = bool(
        alternate and alternate.deviceRegistration.encryptedUserSecrets.encryptedIdentityKey
    )
    has_identity = primary_identity or alt_identity
    kind = {
        pb.IDENTIFIER_ANDROID: "ANDROID",
        pb.IDENTIFIER_SPOT: "SPOT",
    }.get(ident.type, f"OTHER({ident.type})")
    canonical = (direct_ids or phone_ids or ([extra_uuid] if extra_uuid else []))
    # Do not treat every Android phone as a tag. Alternative-ID devices must
    # have an encrypted tracker identity key before being considered for poll.
    is_tag = (ident.type == pb.IDENTIFIER_SPOT or
              bool(not phone_ids and not direct_ids and extra_uuid and has_identity))
    return {
        "name": device.userDefinedDeviceName or "(unnamed)",
        "kind": kind,
        "ids": len(phone_ids if ident.type == pb.IDENTIFIER_ANDROID else direct_ids),
        "direct_ids": len(direct_ids),
        "phone_ids": len(phone_ids),
        "hidden_uuid": bool(extra_uuid and extra_uuid not in phone_ids + direct_ids),
        "identity_key": has_identity,
        "alt_registration": bool(alternate),
        "candidate": bool(is_tag and canonical),
        "_canonical": canonical,
    }
