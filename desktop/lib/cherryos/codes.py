"""Transfer codes and the encryption Cherry Move uses.

A transfer code is 20 random bytes plus a checksum byte, written as 34 base32 letters in groups of five
(ABCDE-FGHIJ-...). Everything else comes from the code with HKDF-SHA256:
  id     the transfer's folder name in someone's cloud drive or NAS (not secret on its own)
  key    AES-256-GCM key for the files; it never leaves your computers
The Windows/macOS app (move/codes.go) does exactly the same.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CODE_LETTERS = 34


class BadCode(ValueError):
    pass


def _hkdf(secret: bytes, info: str, length: int, salt: bytes = b"cherry-move-v1") -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info.encode()).derive(secret)


def normalise(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def pretty(code: str) -> str:
    c = normalise(code)
    return "-".join(c[i:i + 5] for i in range(0, len(c), 5))


@dataclass(frozen=True)
class TransferKeys:
    code: str
    id: str
    key: bytes

    def _nonce(self, index: int) -> bytes:
        return b"\0\0\0\0" + struct.pack(">Q", index)

    def seal(self, index: int, data: bytes) -> bytes:
        return AESGCM(self.key).encrypt(self._nonce(index), data, f"{self.id}:{index}".encode())

    def open(self, index: int, data: bytes) -> bytes:
        return AESGCM(self.key).decrypt(self._nonce(index), data, f"{self.id}:{index}".encode())


def new_code() -> TransferKeys:
    secret = secrets.token_bytes(20)
    raw = secret + hashlib.sha256(secret).digest()[:1]
    return keys_from_code(base64.b32encode(raw).decode().rstrip("="))


def keys_from_code(code: str) -> TransferKeys:
    c = normalise(code)
    if len(c) != CODE_LETTERS:
        raise BadCode(f"a transfer code has {CODE_LETTERS} letters and numbers; this one has {len(c)}")
    raw = base64.b32decode(c + "=" * (-len(c) % 8))
    secret, check = raw[:20], raw[20:21]
    if hashlib.sha256(secret).digest()[:1] != check:
        raise BadCode("that transfer code has a typo in it")
    return TransferKeys(code=c, id=_hkdf(secret, "id", 16).hex(), key=_hkdf(secret, "key", 32))
