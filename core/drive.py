"""Pembungkus Google OAuth dan Google Drive API."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from .config import Settings

# Google bisa mengembalikan cakupan dalam urutan/penulisan berbeda; jangan dianggap error.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    # Akses penuh dibutuhkan agar file yang ditaruh manual di folder aplikasi bisa disinkronkan.
    "https://www.googleapis.com/auth/drive",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
APP_FOLDER_NAME = "PenggabungDrive"
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024


class DriveError(Exception):
    """Kesalahan yang aman ditampilkan ke pengguna."""


class TooLarge(DriveError):
    pass


class MissingScope(DriveError):
    """Google tidak memberikan izin Drive pada token."""


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
SCOPE_HINT = (
    "Izin Google Drive belum diberikan untuk akun ini. Hubungkan ulang akun ini dan pada layar "
    "izin Google, centang semua kotak (terutama izin untuk melihat, mengedit, dan menghapus file Google Drive)."
)


def has_drive_scope(scope) -> bool:
    if isinstance(scope, str):
        scope = scope.split()
    return DRIVE_SCOPE in set(scope or [])


def is_insufficient_scope(exc: Exception) -> bool:
    """403 karena token tidak punya izin (bukan karena Drive API belum diaktifkan)."""
    return isinstance(exc, HttpError) and getattr(exc.resp, "status", None) == 403 and (
        "insufficient" in str(exc).lower()
    )


def friendly_error(exc: Exception) -> str:
    if is_insufficient_scope(exc):
        return SCOPE_HINT
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", "?")
        reason = ""
        try:
            reason = exc.error_details[0].get("message", "") if exc.error_details else ""
        except Exception:
            reason = ""
        return f"Google Drive menolak permintaan (kode {status}). {reason}".strip()
    return str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------- OAuth


def _build_flow(settings: Settings) -> Flow:
    if settings.app_url.startswith("http://localhost") or settings.app_url.startswith(
        "http://127.0.0.1"
    ):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.app_url],
        }
    }
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=settings.app_url)


def authorization_url(
    settings: Settings, state: str, force_consent: bool, code_verifier: str
) -> str:
    """Buat tautan masuk Google. force_consent=True memunculkan pilihan akun + izin."""
    flow = _build_flow(settings)
    flow.code_verifier = code_verifier
    options = {
        "access_type": "offline",
        "include_granted_scopes": "false",
        "state": state,
    }
    if force_consent:
        options["prompt"] = "select_account consent"
    url, _ = flow.authorization_url(**options)
    return url


def exchange_code(
    settings: Settings, code: str, code_verifier: Optional[str]
) -> tuple[Credentials, dict]:
    flow = _build_flow(settings)
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    granted = flow.oauth2session.token.get("scope")
    if not has_drive_scope(granted):
        raise MissingScope(SCOPE_HINT)
    creds = flow.credentials
    response = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=20,
    )
    response.raise_for_status()
    return creds, response.json()


def revoke_token(token: str) -> None:
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=10
        )
    except requests.RequestException:
        pass


# ---------------------------------------------------------------- Drive


def make_credentials(
    settings: Settings,
    access_token: Optional[str],
    refresh_token: str,
    expiry: Optional[datetime],
) -> Credentials:
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
        expiry=expiry,
    )


def refresh_if_needed(creds: Credentials) -> bool:
    """Segarkan access token bila kedaluwarsa. True bila token berubah."""
    if creds.valid:
        return False
    creds.refresh(Request())
    return True


def drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@dataclass
class Quota:
    total: Optional[int]
    used: int
    trash: Optional[int]


def get_quota(service) -> Quota:
    about = service.about().get(fields="storageQuota").execute()
    q = about.get("storageQuota", {})
    return Quota(
        total=int(q["limit"]) if q.get("limit") else None,
        used=int(q.get("usage", 0)),
        trash=int(q["usageInDriveTrash"]) if q.get("usageInDriveTrash") else None,
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_app_folder(service, cached_id: Optional[str] = None) -> str:
    if cached_id:
        try:
            meta = service.files().get(fileId=cached_id, fields="id,trashed").execute()
            if not meta.get("trashed"):
                return meta["id"]
        except HttpError as exc:
            if getattr(exc.resp, "status", None) != 404:
                raise
    found = (
        service.files()
        .list(
            q=(
                f"name = '{_escape(APP_FOLDER_NAME)}' and mimeType = '{FOLDER_MIME}' "
                "and 'root' in parents and trashed = false"
            ),
            spaces="drive",
            fields="files(id)",
            pageSize=1,
        )
        .execute()
        .get("files", [])
    )
    if found:
        return found[0]["id"]
    created = (
        service.files()
        .create(
            body={"name": APP_FOLDER_NAME, "mimeType": FOLDER_MIME, "parents": ["root"]},
            fields="id",
        )
        .execute()
    )
    return created["id"]


def upload_file(
    service,
    folder_id: str,
    name: str,
    mime: str,
    fileobj,
    size: int,
    on_progress: Optional[Callable[[float], None]] = None,
) -> dict:
    """Unggah ke Drive langsung dari memori (tanpa menulis ke disk)."""
    mime = mime or "application/octet-stream"
    resumable = size > 5 * 1024 * 1024
    media = MediaIoBaseUpload(fileobj, mimetype=mime, chunksize=CHUNK, resumable=resumable)
    request = service.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,name,mimeType,size",
    )
    if not resumable:
        result = request.execute()
    else:
        result = None
        while result is None:
            status, result = request.next_chunk()
            if status and on_progress:
                on_progress(status.progress())
    if on_progress:
        on_progress(1.0)
    return result


def download_bytes(service, file_id: str, max_bytes: int) -> tuple[bytes, str, str]:
    """Unduh ke memori. Mengembalikan (isi, nama, mime)."""
    meta = service.files().get(fileId=file_id, fields="id,name,mimeType,size").execute()
    name, mime = meta["name"], meta["mimeType"]
    if mime.startswith("application/vnd.google-apps."):
        request = service.files().export_media(fileId=file_id, mimeType="application/pdf")
        name, mime = f"{name}.pdf", "application/pdf"
    else:
        if int(meta.get("size", 0)) > max_bytes:
            raise TooLarge(
                f"File lebih besar dari batas unduh {max_bytes // (1024 * 1024)} MB."
            )
        request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=CHUNK)
    done = False
    while not done:
        _, done = downloader.next_chunk()
        if buffer.tell() > max_bytes:
            raise TooLarge(
                f"File lebih besar dari batas unduh {max_bytes // (1024 * 1024)} MB."
            )
    return buffer.getvalue(), name, mime


def rename_file(service, file_id: str, new_name: str) -> None:
    service.files().update(fileId=file_id, body={"name": new_name}).execute()


def delete_file(service, file_id: str) -> None:
    try:
        service.files().delete(fileId=file_id).execute()
    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 404:
            raise


def list_folder_files(service, folder_id: str) -> list[dict]:
    """Semua file (bukan folder) langsung di dalam folder aplikasi."""
    files: list[dict] = []
    token = None
    while True:
        response = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and mimeType != '{FOLDER_MIME}' and trashed = false",
                spaces="drive",
                fields="nextPageToken,files(id,name,mimeType,size)",
                pageSize=1000,
                pageToken=token,
            )
            .execute()
        )
        files.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            return files
