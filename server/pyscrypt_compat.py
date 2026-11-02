"""Minimal replacement for the upstream pyscrypt.hash interface (RFC 7914)."""
import hashlib


def hash(password: bytes, salt: bytes, N: int, r: int, p: int, dkLen: int) -> bytes:
    return hashlib.scrypt(password, salt=salt, n=N, r=r, p=p, dklen=dkLen)
