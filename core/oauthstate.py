"""Status OAuth tanpa database.

State ditandatangani HMAC dengan TOKEN_ENCRYPTION_KEY, jadi halaman login tidak perlu menyentuh database
dan callback Google bisa diperiksa tanpa mencarinya. Kunci PKCE (code_verifier) diturunkan dari
state + kunci rahasia, jadi tidak perlu disimpan di mana pun.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Optional

TTL_SECONDS = 30 * 60


@dataclass(frozen=True)
class PendingAuth:
    purpose: str  # "login" | "connect" | "connect_yandex"
    user_id: Optional[str]
    code_verifier: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(key: str, label: str, payload: bytes) -> bytes:
    return hmac.new(key.encode("utf-8"), label.encode("ascii") + payload, hashlib.sha256).digest()


def _verifier(key: str, payload: bytes) -> str:
    return _b64(_mac(key, "pkce|", payload))  # 43 karakter, termasuk karakter yang diizinkan PKCE


def make_state(key: str, purpose: str, user_id: Optional[str], now: Optional[float] = None) -> tuple[str, str]:
    """Kembalikan (state, code_verifier)."""
    payload = "|".join(
        [purpose, user_id or "", str(int(now if now is not None else time.time())), secrets.token_urlsafe(12)]
    ).encode("utf-8")
    signature = _b64(_mac(key, "state|", payload))
    return f"{_b64(payload)}.{signature}", _verifier(key, payload)


def read_state(
    key: str, state: str, now: Optional[float] = None, ttl: int = TTL_SECONDS
) -> Optional[PendingAuth]:
    """Kembalikan data state bila tanda tangan sah dan belum kedaluwarsa, selain itu None."""
    try:
        encoded, signature = state.split(".", 1)
        payload = _unb64(encoded)
        expected = _b64(_mac(key, "state|", payload))
        if not hmac.compare_digest(signature, expected):
            return None
        purpose, user_id, issued, _nonce = payload.decode("utf-8").split("|")
        age = (now if now is not None else time.time()) - int(issued)
    except (ValueError, UnicodeDecodeError):
        return None
    if purpose not in ("login", "connect", "connect_yandex") or age < -60 or age > ttl:
        return None
    return PendingAuth(purpose, user_id or None, _verifier(key, payload))
