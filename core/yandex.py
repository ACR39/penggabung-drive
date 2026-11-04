"""Pembungkus Yandex ID (OAuth) dan Yandex Disk REST API.

Fungsi-fungsi penyimpanan memakai nama dan bentuk yang sama dengan core/drive.py,
supaya service.py bisa memilih salah satunya per akun.

Aplikasi memakai izin "app folder" (cloud_api:disk.app_folder): hanya folder khusus aplikasi
yang bisa dibaca dan ditulis, jadi sisa isi Disk pengguna tidak tersentuh. Jalur berawalan "app:/".
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

from .config import Settings
from .drive import DriveError, Quota, TooLarge

AUTH_URL = "https://oauth.yandex.com/authorize"
TOKEN_URL = "https://oauth.yandex.com/token"
INFO_URL = "https://login.yandex.ru/info"
API = "https://cloud-api.yandex.net/v1/disk"
ROOT = "app:/"
SCOPE = "cloud_api:disk.app_folder login:info login:email"
TIMEOUT = 30


class YandexAuthError(DriveError):
    """Token Yandex ditolak (kedaluwarsa atau dicabut)."""


def authorization_url(settings: Settings, state: str) -> str:
    query = {
        "response_type": "code",
        "client_id": settings.yandex_client_id,
        "redirect_uri": settings.app_url,
        "state": state,
        "force_confirm": "yes",
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def _token_request(settings: Settings, data: dict) -> dict:
    payload = {**data, "client_id": settings.yandex_client_id, "client_secret": settings.yandex_client_secret}
    response = requests.post(TOKEN_URL, data=payload, timeout=TIMEOUT)
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("error_description", "")
        except ValueError:
            pass
        raise YandexAuthError(f"Yandex menolak permintaan token. {detail}".strip())
    return response.json()


def exchange_code(settings: Settings, code: str) -> tuple[dict, dict]:
    """Tukar kode dengan token. Mengembalikan (token, profil) dengan profil berisi sub/email/name."""
    token = _token_request(settings, {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.app_url})
    info = requests.get(
        INFO_URL, params={"format": "json"}, headers={"Authorization": f"OAuth {token['access_token']}"}, timeout=TIMEOUT
    )
    if info.status_code != 200:
        raise YandexAuthError("Tidak bisa membaca profil Yandex.")
    data = info.json()
    email = data.get("default_email") or (data.get("emails") or [""])[0] or f"{data.get('login', 'yandex')}@yandex"
    profile = {
        "sub": str(data["id"]),
        "email": email,
        "name": data.get("real_name") or data.get("display_name") or data.get("login"),
        "picture": None,
    }
    return token, profile


def refresh_token(settings: Settings, refresh: str) -> dict:
    return _token_request(settings, {"grant_type": "refresh_token", "refresh_token": refresh})


def expiry_from(token: dict, now: Optional[datetime] = None) -> Optional[datetime]:
    seconds = token.get("expires_in")
    if not seconds:
        return None
    return (now or datetime.utcnow()) + timedelta(seconds=int(seconds) - 60)


class Client:
    """Klien tipis untuk satu token akses."""

    def __init__(self, access_token: str):
        self.headers = {"Authorization": f"OAuth {access_token}"}

    def call(self, method: str, path: str, **kwargs) -> requests.Response:
        response = requests.request(
            method, f"{API}{path}", headers=self.headers, timeout=kwargs.pop("timeout", TIMEOUT), **kwargs
        )
        if response.status_code == 401:
            raise YandexAuthError("Izin Yandex habis atau dicabut. Hubungkan ulang akun ini.")
        return response


def _check(response: requests.Response, ok=(200, 201, 202, 204)) -> requests.Response:
    if response.status_code in ok:
        return response
    message = ""
    try:
        message = response.json().get("description") or response.json().get("message", "")
    except ValueError:
        pass
    raise DriveError(f"Yandex Disk menolak permintaan (kode {response.status_code}). {message}".strip())


def get_quota(client: Client) -> Quota:
    data = _check(client.call("GET", "")).json()
    return Quota(
        total=int(data["total_space"]) if data.get("total_space") else None,
        used=int(data.get("used_space", 0)),
        trash=int(data["trash_size"]) if data.get("trash_size") is not None else None,
    )


def ensure_app_folder(client: Client, cached_id: Optional[str] = None) -> str:
    """Folder aplikasi sudah ada sebagai akar "app:/"; tidak perlu dibuat."""
    return ROOT


def _exists(client: Client, path: str) -> bool:
    return client.call("GET", "/resources", params={"path": path, "fields": "name"}).status_code == 200


def _free_name(client: Client, folder: str, name: str) -> str:
    """Yandex menolak nama kembar di satu folder, jadi tambahkan (2), (3), dst."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 1
    while _exists(client, folder + candidate):
        n += 1
        candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
        if n > 200:
            raise DriveError("Terlalu banyak berkas bernama sama.")
    return candidate


def upload_file(
    client: Client,
    folder_id: str,
    name: str,
    mime: str,
    fileobj,
    size: int,
    on_progress: Optional[Callable[[float], None]] = None,
) -> dict:
    final = _free_name(client, folder_id, name)
    path = folder_id + final
    target = _check(client.call("GET", "/resources/upload", params={"path": path, "overwrite": "false"})).json()
    if hasattr(fileobj, "seek"):
        fileobj.seek(0)
    put = requests.put(target["href"], data=fileobj, headers={"Content-Length": str(size)}, timeout=600)
    _check(put, ok=(200, 201, 202))
    if on_progress:
        on_progress(1.0)
    return {"id": path, "name": final, "mimeType": mime or "application/octet-stream", "size": size}


def download_bytes(client: Client, file_id: str, max_bytes: int) -> tuple[bytes, str, str]:
    meta = _check(client.call("GET", "/resources", params={"path": file_id, "fields": "name,mime_type,size"})).json()
    if int(meta.get("size", 0)) > max_bytes:
        raise TooLarge(f"File lebih besar dari batas unduh {max_bytes // (1024 * 1024)} MB.")
    link = _check(client.call("GET", "/resources/download", params={"path": file_id})).json()["href"]
    buffer = io.BytesIO()
    with requests.get(link, stream=True, timeout=600) as stream:
        _check(stream, ok=(200,))
        for chunk in stream.iter_content(1024 * 1024):
            buffer.write(chunk)
            if buffer.tell() > max_bytes:
                raise TooLarge(f"File lebih besar dari batas unduh {max_bytes // (1024 * 1024)} MB.")
    return buffer.getvalue(), meta["name"], meta.get("mime_type") or "application/octet-stream"


def rename_file(client: Client, file_id: str, new_name: str) -> None:
    folder = file_id.rsplit("/", 1)[0] + "/"
    target = folder + new_name
    if _exists(client, target):
        raise DriveError("Sudah ada berkas dengan nama itu di Yandex Disk.")
    _check(client.call("POST", "/resources/move", params={"from": file_id, "path": target, "overwrite": "false"}))


def delete_file(client: Client, file_id: str) -> None:
    response = client.call("DELETE", "/resources", params={"path": file_id, "permanently": "true"})
    if response.status_code != 404:
        _check(response)


def list_folder_files(client: Client, folder_id: str) -> list[dict]:
    files: list[dict] = []
    offset = 0
    while True:
        data = _check(
            client.call(
                "GET",
                "/resources",
                params={
                    "path": folder_id,
                    "limit": 1000,
                    "offset": offset,
                    "fields": "_embedded.items.name,_embedded.items.path,_embedded.items.type,"
                    "_embedded.items.size,_embedded.items.mime_type",
                },
            )
        ).json()
        items = data.get("_embedded", {}).get("items", [])
        for item in items:
            if item.get("type") == "file":
                path = item["path"] if str(item["path"]).startswith("app:") else folder_id + item["name"]
                files.append(
                    {
                        "id": path,
                        "name": item["name"],
                        "mimeType": item.get("mime_type") or "application/octet-stream",
                        "size": item.get("size", 0),
                    }
                )
        if len(items) < 1000:
            return files
        offset += 1000
