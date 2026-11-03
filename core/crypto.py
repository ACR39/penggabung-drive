"""Enkripsi token Google sebelum disimpan ke database."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(key_material: str) -> Fernet:
    digest = hashlib.sha256(key_material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str, key_material: str) -> str:
    return _fernet(key_material).encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token: str, key_material: str) -> str:
    try:
        return _fernet(key_material).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Token tidak bisa dibuka. TOKEN_ENCRYPTION_KEY mungkin berubah."
        ) from exc
