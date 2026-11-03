"""Pengaturan aplikasi. Dibaca dari variabel lingkungan atau st.secrets."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        found = st.secrets.get(name, default)
        return str(found) if found is not None else default
    except Exception:
        return default


@dataclass(frozen=True)
class Settings:
    google_client_id: str
    google_client_secret: str
    app_url: str
    encryption_key: str
    database_url: str
    allowed_emails: tuple[str, ...]
    max_download_mb: int

    def missing(self) -> list[str]:
        """Nama pengaturan wajib yang belum diisi."""
        problems = []
        if not self.google_client_id:
            problems.append("GOOGLE_CLIENT_ID")
        if not self.google_client_secret:
            problems.append("GOOGLE_CLIENT_SECRET")
        if not self.app_url:
            problems.append("APP_URL")
        if len(self.encryption_key) < 32:
            problems.append("TOKEN_ENCRYPTION_KEY (minimal 32 karakter)")
        return problems


def load_settings() -> Settings:
    emails = tuple(
        e.strip().lower() for e in _get("ALLOWED_EMAILS").split(",") if e.strip()
    )
    try:
        max_mb = int(_get("MAX_DOWNLOAD_MB", "200"))
    except ValueError:
        max_mb = 200
    return Settings(
        google_client_id=_get("GOOGLE_CLIENT_ID"),
        google_client_secret=_get("GOOGLE_CLIENT_SECRET"),
        app_url=_get("APP_URL", "http://localhost:8501").rstrip("/"),
        encryption_key=_get("TOKEN_ENCRYPTION_KEY"),
        database_url=_get("DATABASE_URL", "sqlite:///data/penggabung.db"),
        allowed_emails=emails,
        max_download_mb=max_mb,
    )
