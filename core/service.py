"""Logika bisnis: akun, kuota, routing upload, folder virtual, berbagi.

Semua fungsi menerima sesi database dan melakukan commit sendiri,
sehingga aman dipakai dari Streamlit (yang bisa menghentikan skrip kapan saja).
"""
from __future__ import annotations

import io
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional, Sequence

from google.auth.exceptions import RefreshError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import drive, yandex
from .config import Settings
from .crypto import decrypt, encrypt
from .db import (
    get_session_factory,
    Account,
    Activity,
    File,
    Folder,
    Gallery,
    GalleryItem,
    ImportSource,
    RoutingPolicy,
    Share,
    User,
    utcnow,
)
from .routing import MODES, Candidate, pick_account
from .ui_assets import COLORS, DEFAULT_COLOR, DEFAULT_ICON, FOLDER_ICONS

STALE_AFTER = timedelta(minutes=5)
EXPIRY_CHOICES: dict[str, Optional[int]] = {
    "Tanpa batas waktu": None,
    "1 hari": 1,
    "7 hari": 7,
    "30 hari": 30,
}


class AppError(Exception):
    """Kesalahan yang pesannya boleh langsung ditampilkan."""


class NeedsConsent(AppError):
    """Google tidak mengirim refresh token; pengguna perlu memberi izin ulang."""


def _error_text(exc: Exception) -> str:
    return str(exc) if isinstance(exc, AppError) else drive.friendly_error(exc)


# ---------------------------------------------------------------- Pengguna dan akun


def get_or_create_user(db: Session, email: str, name: Optional[str]) -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name)
        db.add(user)
    elif name and not user.name:
        user.name = name
    db.commit()
    return user


def resolve_login_user(db: Session, profile: dict) -> User:
    """Masuk dengan akun Google yang sudah terhubung -> pakai dasbor pemiliknya."""
    sub = str(profile["sub"])
    acc = db.scalar(select(Account).where(Account.google_sub == sub))
    if acc is not None:
        user = db.get(User, acc.user_id)
        if user is not None:
            return user
    return get_or_create_user(db, profile.get("email", ""), profile.get("name"))


def upsert_account(
    db: Session, settings: Settings, user: User, profile: dict, creds, provider: str = "google"
) -> Account:
    sub = str(profile["sub"])
    acc = db.scalar(
        select(Account).where(
            Account.user_id == user.id, Account.google_sub == sub, Account.provider == provider
        )
    )
    if acc is None:
        if not creds.refresh_token:
            raise NeedsConsent("Penyedia belum memberi izin offline untuk akun ini.")
        acc = Account(user_id=user.id, google_sub=sub, email=profile.get("email", ""), provider=provider)
        db.add(acc)
    elif not creds.refresh_token and not acc.refresh_token_enc:
        raise NeedsConsent("Google belum memberi izin offline untuk akun ini.")
    if creds.refresh_token:
        acc.refresh_token_enc = encrypt(creds.refresh_token, settings.encryption_key)
    acc.email = profile.get("email", acc.email)
    acc.name = profile.get("name")
    acc.picture = profile.get("picture")
    acc.access_token_enc = encrypt(creds.token, settings.encryption_key)
    acc.token_expiry = creds.expiry
    acc.status = "connected"
    acc.last_error = None
    db.commit()
    _forget_accounts(db)
    return acc


def list_accounts(db: Session, user_id: str, connected_only: bool = False) -> list[Account]:
    """Daftar akun. Disimpan per sesi database (satu kali jalan skrip) agar tidak mengulang kueri."""
    cache = db.info.setdefault("_accounts", {})
    key = (user_id, connected_only)
    if key not in cache:
        query = select(Account).where(Account.user_id == user_id)
        if connected_only:
            query = query.where(Account.status == "connected")
        cache[key] = list(db.scalars(query.order_by(Account.created_at)))
    return list(cache[key])


def _forget_accounts(db: Session) -> None:
    db.info.pop("_accounts", None)


def _ops(acc: Account):
    """Modul penyimpanan untuk akun ini (drive atau yandex); keduanya punya fungsi yang sama."""
    return yandex if acc.provider == "yandex" else drive


def _yandex_client(db: Session, settings: Settings, acc: Account):
    try:
        refresh = decrypt(acc.refresh_token_enc, settings.encryption_key)
        access = decrypt(acc.access_token_enc, settings.encryption_key) if acc.access_token_enc else None
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    if access and acc.token_expiry and acc.token_expiry > utcnow():
        return yandex.Client(access)
    try:
        token = yandex.refresh_token(settings, refresh)
    except yandex.YandexAuthError as exc:
        acc.status = "reauth"
        acc.last_error = "Izin Yandex habis atau dicabut. Hubungkan ulang akun ini."
        db.commit()
        raise AppError(acc.last_error) from exc
    except Exception as exc:
        raise AppError(f"Gagal menyegarkan token Yandex: {exc}") from exc
    acc.access_token_enc = encrypt(token["access_token"], settings.encryption_key)
    if token.get("refresh_token"):
        acc.refresh_token_enc = encrypt(token["refresh_token"], settings.encryption_key)
    acc.token_expiry = yandex.expiry_from(token)
    acc.status, acc.last_error = "connected", None
    db.commit()
    return yandex.Client(token["access_token"])


def service_for(db: Session, settings: Settings, acc: Account):
    """Klien penyimpanan untuk satu akun; token disegarkan dan disimpan bila perlu."""
    if not acc.refresh_token_enc:
        raise AppError("Akun ini belum punya izin offline. Hubungkan ulang.")
    if acc.provider == "yandex":
        return _yandex_client(db, settings, acc)
    if not acc.refresh_token_enc:
        raise AppError("Akun ini belum punya izin offline. Hubungkan ulang.")
    try:
        refresh = decrypt(acc.refresh_token_enc, settings.encryption_key)
        access = (
            decrypt(acc.access_token_enc, settings.encryption_key)
            if acc.access_token_enc
            else None
        )
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    creds = drive.make_credentials(settings, access, refresh, acc.token_expiry)
    try:
        if drive.refresh_if_needed(creds):
            acc.access_token_enc = encrypt(creds.token, settings.encryption_key)
            acc.token_expiry = creds.expiry
            acc.status = "connected"
            acc.last_error = None
            db.commit()
    except RefreshError as exc:
        acc.status = "reauth"
        acc.last_error = "Izin Google habis atau dicabut. Hubungkan ulang akun ini."
        db.commit()
        raise AppError(acc.last_error) from exc
    except Exception as exc:
        raise AppError(f"Gagal menyegarkan token: {exc}") from exc
    return drive.drive_service(creds)


def _flag_scope_problem(db: Session, acc: Account, exc: Exception) -> None:
    """Bila 403 karena izin kurang, tandai akun supaya UI meminta hubungkan ulang."""
    if drive.is_insufficient_scope(exc):
        acc.status = "reauth"
        acc.last_error = drive.SCOPE_HINT
        db.commit()


def refresh_quota(db: Session, settings: Settings, acc: Account) -> None:
    service = service_for(db, settings, acc)
    try:
        quota = _ops(acc).get_quota(service)
    except Exception as exc:
        acc.last_error = _error_text(exc)
        db.commit()
        _flag_scope_problem(db, acc, exc)
        raise AppError(acc.last_error) from exc
    acc.total_bytes = quota.total
    acc.used_bytes = quota.used
    acc.trash_bytes = quota.trash
    acc.last_synced_at = utcnow()
    acc.last_error = None
    db.commit()


def refresh_stale(
    db: Session, settings: Settings, accounts: Sequence[Account], force: bool = False
) -> list[str]:
    """Segarkan kuota akun yang basi. Mengembalikan daftar pesan galat."""
    errors = []
    for acc in accounts:
        if acc.status != "connected":
            continue
        stale = acc.last_synced_at is None or utcnow() - acc.last_synced_at > STALE_AFTER
        if force or stale:
            try:
                refresh_quota(db, settings, acc)
            except AppError as exc:
                errors.append(f"{acc.email}: {exc}")
    return errors


def stale_ids(accounts: Sequence[Account]) -> list[str]:
    return [
        a.id
        for a in accounts
        if a.status == "connected"
        and (a.last_synced_at is None or utcnow() - a.last_synced_at > STALE_AFTER)
    ]


def refresh_many(settings: Settings, account_ids: Sequence[str], max_workers: int = 4) -> list[str]:
    """Segarkan kuota beberapa akun sekaligus. Tiap akun memakai thread dan sesi database sendiri."""
    ids = list(account_ids)
    if not ids:
        return []
    factory = get_session_factory(settings.database_url)

    def work(account_id: str) -> Optional[str]:
        db = factory()
        try:
            acc = db.get(Account, account_id)
            if acc is None or acc.status != "connected":
                return None
            email = acc.email
            try:
                refresh_quota(db, settings, acc)
            except AppError as exc:
                return f"{email}: {exc}"
            return None
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=min(max_workers, len(ids))) as pool:
        return [msg for msg in pool.map(work, ids) if msg]


_bg_lock = threading.Lock()
_bg_inflight: set[str] = set()
_bg_last_try: dict[str, float] = {}
BG_RETRY_SECONDS = 120


def refresh_in_background(settings: Settings, account_ids: Sequence[str]) -> bool:
    """Segarkan kuota tanpa membuat halaman menunggu. Hasilnya terlihat pada interaksi berikutnya."""
    now = time.monotonic()
    with _bg_lock:
        ids = [
            i
            for i in account_ids
            if i not in _bg_inflight and now - _bg_last_try.get(i, -1e9) > BG_RETRY_SECONDS
        ]
        _bg_inflight.update(ids)
        for i in ids:
            _bg_last_try[i] = now
    if not ids:
        return False

    def run() -> None:
        try:
            refresh_many(settings, ids)
        except Exception:
            pass
        finally:
            with _bg_lock:
                _bg_inflight.difference_update(ids)

    threading.Thread(target=run, name="refresh-kuota", daemon=True).start()
    return True


@dataclass
class QuotaTotals:
    total: int
    used: int
    free: int
    unlimited_accounts: int


def quota_totals(accounts: Sequence[Account]) -> QuotaTotals:
    total = used = unlimited = 0
    for acc in accounts:
        if acc.status != "connected":
            continue
        if acc.total_bytes is None:
            unlimited += 1
            continue
        total += acc.total_bytes
        used += acc.used_bytes or 0
    return QuotaTotals(total, used, max(total - used, 0), unlimited)


def remove_account(db: Session, settings: Settings, user_id: str, account_id: str) -> None:
    acc = db.get(Account, account_id)
    if acc is None or acc.user_id != user_id:
        raise AppError("Akun tidak ditemukan.")
    if acc.refresh_token_enc:
        try:
            if acc.provider == "google":
                drive.revoke_token(decrypt(acc.refresh_token_enc, settings.encryption_key))
        except ValueError:
            pass
    policy = db.get(RoutingPolicy, user_id)
    if policy and account_id in (policy.priority_ids or []):
        policy.priority_ids = [i for i in policy.priority_ids if i != account_id]
    db.delete(acc)  # file milik akun ini ikut terhapus dari database (bukan dari Drive)
    db.commit()
    _forget_accounts(db)


# ---------------------------------------------------------------- Aturan upload


def get_policy(db: Session, user_id: str) -> RoutingPolicy:
    policy = db.get(RoutingPolicy, user_id)
    if policy is None:
        policy = RoutingPolicy(user_id=user_id, mode="most_available", priority_ids=[], cursor=0)
        db.add(policy)
        db.commit()
    return policy


def set_policy(db: Session, user_id: str, mode: str, priority_ids: list[str]) -> None:
    if mode not in MODES:
        raise AppError("Mode tidak dikenal.")
    policy = get_policy(db, user_id)
    policy.mode = mode
    policy.priority_ids = list(priority_ids)
    db.commit()


def choose_account(
    db: Session,
    settings: Settings,
    user_id: str,
    size: int,
    target_id: Optional[str] = None,
) -> Optional[Account]:
    query = select(Account).where(Account.user_id == user_id, Account.status == "connected")
    if target_id:
        query = query.where(Account.id == target_id)
    accounts = list(db.scalars(query.order_by(Account.created_at)))
    refresh_stale(db, settings, accounts)
    usable = [a for a in accounts if a.status == "connected" and a.last_synced_at]
    by_id = {a.id: a for a in usable}
    candidates = [Candidate(a.id, a.created_at, a.available_bytes) for a in usable]
    if target_id:
        chosen, _ = pick_account(candidates, size)
        return by_id.get(chosen) if chosen else None
    policy = get_policy(db, user_id)
    chosen, cursor = pick_account(
        candidates, size, policy.mode, policy.priority_ids or [], policy.cursor
    )
    if chosen and policy.mode == "round_robin":
        policy.cursor = cursor
        db.commit()
    return by_id.get(chosen) if chosen else None


# ---------------------------------------------------------------- Upload


@dataclass
class UploadResult:
    name: str
    ok: bool
    message: str
    account_email: Optional[str] = None


def upload_batch(
    db: Session,
    settings: Settings,
    user_id: str,
    items: Sequence[tuple[str, str, int, object]],
    folder_id: Optional[str],
    target_id: Optional[str] = None,
    on_progress: Optional[Callable[[int, int, float], None]] = None,
) -> list[UploadResult]:
    """items: (nama, mime, ukuran, objek_file). Tiap file memilih akunnya sendiri."""
    results: list[UploadResult] = []
    for index, (name, mime, size, fileobj) in enumerate(items):
        acc = None
        try:
            acc = choose_account(db, settings, user_id, size, target_id)
            if acc is None:
                results.append(
                    UploadResult(name, False, "Tidak ada akun dengan ruang yang cukup.")
                )
                continue
            service = service_for(db, settings, acc)
            folder = _ops(acc).ensure_app_folder(service, acc.app_folder_id)
            if folder != acc.app_folder_id:
                acc.app_folder_id = folder
                db.commit()
            callback = (
                (lambda fraction, i=index: on_progress(i, len(items), fraction))
                if on_progress
                else None
            )
            meta = _ops(acc).upload_file(service, folder, name, mime, fileobj, size, callback)
            db.add(
                File(
                    user_id=user_id,
                    account_id=acc.id,
                    folder_id=folder_id,
                    drive_file_id=meta["id"],
                    name=meta["name"][:255],
                    mime_type=meta.get("mimeType") or mime or "application/octet-stream",
                    size_bytes=int(meta.get("size", size)),
                )
            )
            acc.used_bytes = (acc.used_bytes or 0) + size
            if folder_id:
                target = db.get(Folder, folder_id)
                if target is not None:
                    target.updated_at = utcnow()
            db.commit()
            results.append(UploadResult(name, True, "Berhasil", acc.email))
        except Exception as exc:
            db.rollback()
            if acc is not None:
                _flag_scope_problem(db, acc, exc)
            results.append(UploadResult(name, False, _error_text(exc)))
    return results


def sync_account_files(db: Session, settings: Settings, acc: Account) -> dict[str, int]:
    """Samakan database dengan isi folder aplikasi di Drive (Drive jadi acuan)."""
    service = service_for(db, settings, acc)
    try:
        folder = _ops(acc).ensure_app_folder(service, acc.app_folder_id)
        remote = {f["id"]: f for f in _ops(acc).list_folder_files(service, folder)}
    except Exception as exc:
        _flag_scope_problem(db, acc, exc)
        raise AppError(_error_text(exc)) from exc
    if folder != acc.app_folder_id:
        acc.app_folder_id = folder
    existing = {
        f.drive_file_id: f
        for f in db.scalars(select(File).where(File.account_id == acc.id, File.source_id.is_(None)))
    }
    created = updated = removed = 0
    for remote_id, item in remote.items():
        name, mime = item["name"][:255], item["mimeType"]
        size = int(item.get("size", 0))
        current = existing.get(remote_id)
        if current is None:
            db.add(
                File(
                    user_id=acc.user_id,
                    account_id=acc.id,
                    drive_file_id=remote_id,
                    name=name,
                    mime_type=mime,
                    size_bytes=size,
                )
            )
            created += 1
            continue
        changed = (current.name, current.mime_type, current.size_bytes) != (name, mime, size)
        if current.status == "deleted":  # muncul lagi di Drive
            current.status, current.deleted_at, changed = "active", None, True
        if changed:
            current.name, current.mime_type, current.size_bytes = name, mime, size
            updated += 1
    for remote_id, current in existing.items():
        if remote_id not in remote and current.status != "deleted":
            current.status, current.deleted_at = "deleted", utcnow()
            removed += 1
    db.commit()
    try:
        refresh_quota(db, settings, acc)
    except AppError:
        pass
    return {"dibuat": created, "diperbarui": updated, "dihapus": removed}


# ---------------------------------------------------------------- Folder virtual


def _same(column, value):
    return column.is_(None) if value is None else column == value


def list_subfolders(db: Session, user_id: str, parent_id: Optional[str]) -> list[Folder]:
    return list(
        db.scalars(
            select(Folder)
            .where(Folder.user_id == user_id, _same(Folder.parent_id, parent_id))
            .order_by(Folder.name)
        )
    )


def breadcrumb(db: Session, user_id: str, folder_id: Optional[str]) -> list[Folder]:
    chain: list[Folder] = []
    seen: set[str] = set()
    current = folder_id
    while current and current not in seen:
        seen.add(current)
        folder = db.get(Folder, current)
        if folder is None or folder.user_id != user_id:
            break
        chain.append(folder)
        current = folder.parent_id
    return list(reversed(chain))


def folder_options(db: Session, user_id: str) -> list[tuple[Optional[str], str]]:
    folders = list(db.scalars(select(Folder).where(Folder.user_id == user_id)))
    by_id = {f.id: f for f in folders}

    def label(folder: Folder) -> str:
        parts, seen, cur = [], set(), folder
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            parts.append(cur.name)
            cur = by_id.get(cur.parent_id) if cur.parent_id else None
        return " / ".join(reversed(parts))

    options = sorted(((f.id, label(f)) for f in folders), key=lambda pair: pair[1].lower())
    return [(None, "Beranda (akar)")] + options


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise AppError("Nama tidak boleh kosong.")
    if "/" in name or "\\" in name:
        raise AppError("Nama tidak boleh memuat garis miring.")
    return name[:255]


def _owned_folder(db: Session, user_id: str, folder_id: Optional[str]) -> Optional[Folder]:
    if folder_id is None:
        return None
    folder = db.get(Folder, folder_id)
    if folder is None or folder.user_id != user_id:
        raise AppError("Folder tidak ditemukan.")
    return folder


def _valid_style(color: str, icon: str) -> tuple[str, str]:
    return (color if color in COLORS else DEFAULT_COLOR, icon if icon in FOLDER_ICONS else DEFAULT_ICON)


def create_folder(
    db: Session,
    user_id: str,
    parent_id: Optional[str],
    name: str,
    color: str = DEFAULT_COLOR,
    icon: str = DEFAULT_ICON,
) -> Folder:
    name = _clean_name(name)
    _owned_folder(db, user_id, parent_id)
    clash = db.scalar(
        select(Folder).where(
            Folder.user_id == user_id, _same(Folder.parent_id, parent_id), Folder.name == name
        )
    )
    if clash:
        raise AppError("Sudah ada folder dengan nama itu di sini.")
    color, icon = _valid_style(color, icon)
    folder = Folder(user_id=user_id, parent_id=parent_id, name=name, color=color, icon=icon, updated_at=utcnow())
    db.add(folder)
    db.commit()
    return folder


def update_folder(
    db: Session, user_id: str, folder_id: str, name: str, color: str, icon: str
) -> None:
    folder = _owned_folder(db, user_id, folder_id)
    name = _clean_name(name)
    clash = db.scalar(
        select(Folder).where(
            Folder.user_id == user_id,
            _same(Folder.parent_id, folder.parent_id),
            Folder.name == name,
            Folder.id != folder.id,
        )
    )
    if clash:
        raise AppError("Sudah ada folder dengan nama itu di sini.")
    folder.name = name
    folder.color, folder.icon = _valid_style(color, icon)
    folder.updated_at = utcnow()
    db.commit()


def rename_folder(db: Session, user_id: str, folder_id: str, name: str) -> None:
    folder = _owned_folder(db, user_id, folder_id)
    folder.name = _clean_name(name)
    db.commit()


def delete_folder(db: Session, user_id: str, folder_id: str) -> Optional[str]:
    """Hapus folder; isinya naik ke folder induk. Mengembalikan id induk."""
    folder = _owned_folder(db, user_id, folder_id)
    parent = folder.parent_id
    for child in db.scalars(select(Folder).where(Folder.parent_id == folder.id)):
        child.parent_id = parent
    for item in db.scalars(select(File).where(File.folder_id == folder.id)):
        item.folder_id = parent
    db.flush()
    db.delete(folder)
    db.commit()
    return parent


# ---------------------------------------------------------------- File


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_files(
    db: Session,
    user_id: str,
    folder_id: Optional[str] = None,
    query: Optional[str] = None,
    status: str = "active",
    limit: int = 300,
    starred_only: bool = False,
    recent: bool = False,
) -> list[tuple[File, str]]:
    """Daftar berkas beserta email akunnya.

    - query: cari nama di semua folder
    - starred_only / recent: lintas folder (terbaru = urut dari yang paling baru dibuat)
    """
    stmt = (
        select(File, Account.email)
        .join(Account, File.account_id == Account.id)
        .where(File.user_id == user_id, File.status == status)
    )
    if query:
        stmt = stmt.where(File.name.ilike(_like(query.strip()), escape="\\"))
    elif status == "active" and not (starred_only or recent):
        stmt = stmt.where(_same(File.folder_id, folder_id))
    if starred_only:
        stmt = stmt.where(File.starred.is_(True))
    if status == "trashed":
        order = File.deleted_at.desc()
    elif recent:
        order = File.created_at.desc()
    else:
        order = File.name
    return [(f, email) for f, email in db.execute(stmt.order_by(order).limit(limit))]


def set_starred(db: Session, user_id: str, file_ids: Sequence[str], value: bool) -> int:
    files = [f for f in _owned_files(db, user_id, file_ids) if f.status == "active"]
    for item in files:
        item.starred = value
    db.commit()
    return len(files)


def _owned_files(db: Session, user_id: str, file_ids: Sequence[str]) -> list[File]:
    return list(
        db.scalars(select(File).where(File.user_id == user_id, File.id.in_(list(file_ids))))
    )


def move_files(db: Session, user_id: str, file_ids: Sequence[str], folder_id: Optional[str]) -> int:
    _owned_folder(db, user_id, folder_id)
    files = _owned_files(db, user_id, file_ids)
    for item in files:
        item.folder_id = folder_id
    target = _owned_folder(db, user_id, folder_id)
    if target is not None:
        target.updated_at = utcnow()
    db.commit()
    return len(files)


def rename_file(
    db: Session, settings: Settings, user_id: str, file_id: str, new_name: str
) -> None:
    new_name = _clean_name(new_name)
    files = _owned_files(db, user_id, [file_id])
    if not files:
        raise AppError("File tidak ditemukan.")
    item = files[0]
    acc = db.get(Account, item.account_id)
    service = service_for(db, settings, acc)
    try:
        _ops(acc).rename_file(service, item.drive_file_id, new_name)
    except Exception as exc:
        raise AppError(_error_text(exc)) from exc
    item.name = new_name
    db.commit()


def trash_files(db: Session, user_id: str, file_ids: Sequence[str]) -> int:
    files = [f for f in _owned_files(db, user_id, file_ids) if f.status == "active"]
    for item in files:
        item.status, item.deleted_at = "trashed", utcnow()
    db.commit()
    return len(files)


def restore_files(db: Session, user_id: str, file_ids: Sequence[str]) -> int:
    files = [f for f in _owned_files(db, user_id, file_ids) if f.status == "trashed"]
    for item in files:
        item.status, item.deleted_at = "active", None
    db.commit()
    return len(files)


def delete_forever(
    db: Session, settings: Settings, user_id: str, file_ids: Sequence[str]
) -> tuple[int, list[str]]:
    """Hapus permanen dari Drive dan database. Mengembalikan (jumlah, daftar galat)."""
    done, errors = 0, []
    for item in _owned_files(db, user_id, file_ids):
        try:
            acc = db.get(Account, item.account_id)
            service = service_for(db, settings, acc)
            _ops(acc).delete_file(service, item.drive_file_id)
            acc.used_bytes = max((acc.used_bytes or 0) - (item.size_bytes or 0), 0)
            db.delete(item)
            db.commit()
            done += 1
        except Exception as exc:
            db.rollback()
            errors.append(f"{item.name}: {_error_text(exc)}")
    return done, errors


def download_file(db: Session, settings: Settings, item: File) -> tuple[bytes, str, str]:
    acc = db.get(Account, item.account_id)
    if acc is None:
        raise AppError("Akun penyimpan file ini sudah dilepas.")
    service = service_for(db, settings, acc)
    try:
        return _ops(acc).download_bytes(
            service, item.drive_file_id, settings.max_download_mb * 1024 * 1024
        )
    except drive.DriveError as exc:
        raise AppError(str(exc)) from exc
    except Exception as exc:
        raise AppError(_error_text(exc)) from exc


# ---------------------------------------------------------------- Berbagi


SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SLUG_MIN, SLUG_MAX = 3, 40


def clean_slug(slug: Optional[str]) -> Optional[str]:
    """Nama tautan kustom: huruf kecil, angka, tanda hubung. Kosong berarti tautan acak."""
    slug = (slug or "").strip().lower()
    if not slug:
        return None
    if not SLUG_MIN <= len(slug) <= SLUG_MAX:
        raise AppError(f"Nama tautan harus {SLUG_MIN}–{SLUG_MAX} karakter.")
    if not SLUG_RE.match(slug):
        raise AppError("Nama tautan hanya boleh huruf kecil, angka, dan tanda hubung (tidak di awal atau akhir).")
    return slug


def create_share(
    db: Session, user_id: str, file_id: str, days: Optional[int], slug: Optional[str] = None
) -> Share:
    files = [f for f in _owned_files(db, user_id, [file_id]) if f.status == "active"]
    if not files:
        raise AppError("File tidak ditemukan.")
    slug = clean_slug(slug)
    if slug and db.scalar(select(Share).where(Share.token == slug, Share.file_id != file_id)) is not None:
        raise AppError("Nama tautan itu sudah dipakai. Pilih yang lain.")
    expires = None if days is None else utcnow() + timedelta(days=days)
    current = [
        sh
        for sh in db.scalars(select(Share).where(Share.file_id == file_id, Share.enabled.is_(True)))
        if sh.expires_at is None or sh.expires_at > utcnow()
    ]
    if current:  # satu berkas cukup satu tautan aktif: pakai ulang, perbarui masa berlakunya
        share = current[0]
        share.expires_at = expires
        if slug and share.token != slug:
            share.token = slug
        for extra in current[1:]:
            extra.enabled = False
        db.commit()
        return share
    share = Share(
        file_id=file_id,
        user_id=user_id,
        token=slug or secrets.token_urlsafe(24),
        expires_at=expires,
    )
    db.add(share)
    db.commit()
    return share


def list_shares(db: Session, user_id: str) -> list[tuple[Share, File]]:
    rows = db.execute(
        select(Share, File)
        .join(File, Share.file_id == File.id)
        .where(Share.user_id == user_id, Share.enabled.is_(True), File.status == "active")
        .order_by(Share.created_at.desc())
    )
    return [(s, f) for s, f in rows]


def disable_share(db: Session, user_id: str, share_id: str) -> None:
    share = db.get(Share, share_id)
    if share is None or share.user_id != user_id:
        raise AppError("Tautan tidak ditemukan.")
    share.enabled = False
    db.commit()


def resolve_public_share(db: Session, token: str) -> Optional[tuple[Share, File]]:
    """Tautan valid hanya bila aktif, belum kedaluwarsa, dan filenya masih aktif."""
    share = db.scalar(select(Share).where(Share.token == token))
    if share is None or not share.enabled:
        return None
    if share.expires_at is not None and share.expires_at < utcnow():
        return None
    item = db.get(File, share.file_id)
    if item is None or item.status != "active":
        return None
    return share, item


# ---------------------------------------------------------------- Galeri foto

MAX_GALLERY_ITEMS = 100
DOWNLOAD_MODES = ("none", "small", "original")


def gallery_mode(gallery: Gallery) -> str:
    """Mode unduh galeri; galeri lama hanya punya allow_download (dianggap "small")."""
    return gallery.download_mode or ("small" if gallery.allow_download else "none")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
THUMB_PX, VIEW_PX = 480, 1600
_img_lock = threading.Lock()
_img_cache: dict[tuple[str, int, bool], bytes] = {}
_img_cache_bytes = 0
IMG_CACHE_LIMIT = 80 * 1024 * 1024


def is_image(item: File) -> bool:
    return (item.mime_type or "").startswith("image/")


def create_gallery(
    db: Session,
    user_id: str,
    title: str,
    file_ids: Sequence[str],
    days: Optional[int],
    download_mode: str = "none",
    slug: Optional[str] = None,
) -> Gallery:
    title = (title or "").strip()
    if not title:
        raise AppError("Judul galeri tidak boleh kosong.")
    if download_mode is True:  # kompatibel dengan pemanggil lama
        download_mode = "small"
    if download_mode not in DOWNLOAD_MODES:
        raise AppError("Pilihan unduhan tidak dikenal.")
    slug = clean_slug(slug)
    if slug and db.scalar(select(Gallery).where(Gallery.token == slug)) is not None:
        raise AppError("Nama tautan itu sudah dipakai. Pilih yang lain.")
    files = [f for f in _owned_files(db, user_id, list(dict.fromkeys(file_ids))) if f.status == "active" and is_image(f)]
    if not files:
        raise AppError("Pilih minimal satu berkas gambar.")
    if len(files) > MAX_GALLERY_ITEMS:
        raise AppError(f"Satu galeri maksimal {MAX_GALLERY_ITEMS} foto.")
    order = {fid: i for i, fid in enumerate(file_ids)}
    files.sort(key=lambda f: order.get(f.id, 0))
    gallery = Gallery(
        user_id=user_id,
        token=slug or secrets.token_urlsafe(18),
        title=title[:191],
        download_mode=download_mode,
        allow_download=download_mode != "none",
        expires_at=None if days is None else utcnow() + timedelta(days=days),
    )
    gallery.items = [GalleryItem(file_id=f.id, position=i) for i, f in enumerate(files)]
    db.add(gallery)
    db.commit()
    return gallery


def list_galleries(db: Session, user_id: str) -> list[Gallery]:
    return list(
        db.scalars(
            select(Gallery).where(Gallery.user_id == user_id, Gallery.enabled.is_(True)).order_by(Gallery.created_at.desc())
        )
    )


def delete_gallery(db: Session, user_id: str, gallery_id: str) -> None:
    gallery = db.get(Gallery, gallery_id)
    if gallery is None or gallery.user_id != user_id:
        raise AppError("Galeri tidak ditemukan.")
    db.delete(gallery)
    db.commit()


def resolve_gallery(db: Session, token: str) -> Optional[tuple[Gallery, list[File]]]:
    """Galeri valid bila aktif dan belum kedaluwarsa. Hanya foto yang masih aktif yang ikut."""
    gallery = db.scalar(select(Gallery).where(Gallery.token == token))
    if gallery is None or not gallery.enabled:
        return None
    if gallery.expires_at is not None and gallery.expires_at < utcnow():
        return None
    files = []
    for entry in gallery.items:
        item = db.get(File, entry.file_id)
        if item is not None and item.status == "active":
            files.append(item)
    return (gallery, files) if files else None


def _resize(data: bytes, max_px: int, square: bool = False) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        if square:  # ukuran seragam: potong dari tengah
            img = ImageOps.fit(img, (max_px, max_px), method=Image.LANCZOS)
        else:
            img.thumbnail((max_px, max_px))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()


def _cache_put(key: tuple[str, int, bool], data: bytes) -> None:
    global _img_cache_bytes
    with _img_lock:
        if key in _img_cache:
            return
        while _img_cache and _img_cache_bytes + len(data) > IMG_CACHE_LIMIT:
            old = next(iter(_img_cache))
            _img_cache_bytes -= len(_img_cache.pop(old))
        _img_cache[key] = data
        _img_cache_bytes += len(data)


def gallery_image(db: Session, settings: Settings, gallery: Gallery, file_id: str, max_px: int, square: bool = False) -> bytes:
    """JPEG berukuran max_px untuk satu foto di galeri. Foto di luar galeri ditolak."""
    if file_id not in {e.file_id for e in gallery.items}:
        raise AppError("Foto tidak ada di galeri ini.")
    key = (file_id, max_px, square)
    with _img_lock:
        hit = _img_cache.get(key)
    if hit is not None:
        return hit
    item = db.get(File, file_id)
    if item is None or item.status != "active":
        raise AppError("Foto tidak tersedia.")
    acc = db.get(Account, item.account_id)
    if acc is None:
        raise AppError("Akun penyimpan foto ini sudah dilepas.")
    try:
        data, _, _ = _ops(acc).download_bytes(service_for(db, settings, acc), item.drive_file_id, MAX_IMAGE_BYTES)
        out = _resize(data, max_px, square)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(_error_text(exc)) from exc
    _cache_put(key, out)
    return out


def gallery_thumbs(settings: Settings, gallery_id: str, file_ids: Sequence[str], max_workers: int = 6) -> dict[str, Optional[bytes]]:
    """Ambil banyak thumbnail sekaligus; tiap thread memakai sesi database sendiri. Gagal -> None."""
    ids = list(file_ids)
    factory = get_session_factory(settings.database_url)

    def work(fid: str) -> tuple[str, Optional[bytes]]:
        db = factory()
        try:
            gallery = db.get(Gallery, gallery_id)
            return fid, gallery_image(db, settings, gallery, fid, THUMB_PX, True)
        except Exception:
            return fid, None
        finally:
            db.close()

    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(ids))) as pool:
        return dict(pool.map(work, ids))


# ---------------------------------------------------------------- Impor dari Drive

MAX_IMPORT_FILES = 5000


def _google_account(db: Session, user_id: str, account_id: str) -> Account:
    acc = db.get(Account, account_id)
    if acc is None or acc.user_id != user_id:
        raise AppError("Akun tidak ditemukan.")
    if acc.provider != "google":
        raise AppError("Impor dari Drive hanya tersedia untuk akun Google.")
    return acc


def browse_drive_folders(
    db: Session, settings: Settings, user_id: str, account_id: str, parent_id: str = "root"
) -> list[tuple[str, str]]:
    acc = _google_account(db, user_id, account_id)
    service = service_for(db, settings, acc)
    try:
        return [(f["id"], f["name"]) for f in drive.list_child_folders(service, parent_id)]
    except Exception as exc:
        _flag_scope_problem(db, acc, exc)
        raise AppError(_error_text(exc)) from exc


def list_import_sources(db: Session, user_id: str) -> list[tuple[ImportSource, str]]:
    rows = db.execute(
        select(ImportSource, Account.email)
        .join(Account, ImportSource.account_id == Account.id)
        .where(ImportSource.user_id == user_id)
        .order_by(ImportSource.created_at)
    )
    return [(src, email) for src, email in rows]


def add_import_source(
    db: Session, user_id: str, account_id: str, folder_id: str, name: str
) -> ImportSource:
    acc = _google_account(db, user_id, account_id)
    if not folder_id or folder_id == "root":
        raise AppError("Pilih sebuah folder, bukan seluruh Drive.")
    dup = db.scalar(
        select(ImportSource).where(
            ImportSource.account_id == acc.id, ImportSource.drive_folder_id == folder_id
        )
    )
    if dup is not None:
        raise AppError("Folder ini sudah diimpor.")
    src = ImportSource(
        user_id=user_id, account_id=acc.id, drive_folder_id=folder_id, name=(name or "Folder")[:255]
    )
    db.add(src)
    db.commit()
    return src


def _parse_time(value: Optional[str]):
    from datetime import datetime, timezone

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def sync_import_source(db: Session, settings: Settings, user_id: str, source_id: str) -> dict[str, int]:
    """Catat berkas dari folder Drive (beserta sub-foldernya). Berkas tidak dipindah atau diubah."""
    src = db.get(ImportSource, source_id)
    if src is None or src.user_id != user_id:
        raise AppError("Sumber impor tidak ditemukan.")
    acc = _google_account(db, user_id, src.account_id)
    service = service_for(db, settings, acc)
    try:
        items, truncated = drive.list_tree_files(service, src.drive_folder_id, MAX_IMPORT_FILES)
    except Exception as exc:
        _flag_scope_problem(db, acc, exc)
        raise AppError(_error_text(exc)) from exc
    remote = {f["id"]: f for f in items}
    existing = {
        f.drive_file_id: f for f in db.scalars(select(File).where(File.account_id == acc.id))
    }
    created = updated = removed = 0
    for rid, item in remote.items():
        name, mime = item["name"][:255], item["mimeType"]
        size = int(item.get("size", 0))
        current = existing.get(rid)
        if current is None:
            db.add(
                File(
                    user_id=user_id,
                    account_id=acc.id,
                    drive_file_id=rid,
                    name=name,
                    mime_type=mime,
                    size_bytes=size,
                    source_id=src.id,
                    created_at=_parse_time(item.get("modifiedTime")) or utcnow(),
                )
            )
            created += 1
        elif current.source_id == src.id:
            if current.status == "deleted":
                current.status, current.deleted_at = "active", None
                updated += 1
            if (current.name, current.mime_type, current.size_bytes) != (name, mime, size):
                current.name, current.mime_type, current.size_bytes = name, mime, size
                updated += 1
    if not truncated:  # bila daftar terpotong, jangan simpulkan ada berkas yang hilang
        for rid, current in existing.items():
            if current.source_id == src.id and rid not in remote and current.status != "deleted":
                current.status, current.deleted_at = "deleted", utcnow()
                removed += 1
    src.last_synced_at, src.last_truncated = utcnow(), truncated
    db.commit()
    return {"dibuat": created, "diperbarui": updated, "dihapus": removed, "terpotong": int(truncated)}


def remove_import_source(db: Session, user_id: str, source_id: str) -> int:
    """Hapus sumber dan catatan berkas dari aplikasi. Berkas di Drive tidak disentuh."""
    src = db.get(ImportSource, source_id)
    if src is None or src.user_id != user_id:
        raise AppError("Sumber impor tidak ditemukan.")
    rows = list(db.scalars(select(File).where(File.source_id == src.id)))
    for item in rows:
        db.delete(item)
    db.delete(src)
    db.commit()
    return len(rows)


def gallery_download(db: Session, settings: Settings, gallery: Gallery, file_id: str) -> tuple[bytes, str, str]:
    """Isi unduhan galeri sesuai mode: (data, nama, mime). Mode "none" ditolak."""
    mode = gallery_mode(gallery)
    if mode == "none":
        raise AppError("Galeri ini tidak mengizinkan unduhan.")
    if file_id not in {e.file_id for e in gallery.items}:
        raise AppError("Foto tidak ada di galeri ini.")
    item = db.get(File, file_id)
    if item is None or item.status != "active":
        raise AppError("Foto tidak tersedia.")
    if mode == "small":
        data = gallery_image(db, settings, gallery, file_id, VIEW_PX)
        return data, item.name.rsplit(".", 1)[0] + ".jpg", "image/jpeg"
    acc = db.get(Account, item.account_id)
    if acc is None:
        raise AppError("Akun penyimpan foto ini sudah dilepas.")
    try:
        data, _, mime = _ops(acc).download_bytes(service_for(db, settings, acc), item.drive_file_id, MAX_IMAGE_BYTES)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(_error_text(exc)) from exc
    return data, item.name, item.mime_type or mime


# ---------------------------------------------------------------- Pindah antar drive

MAX_BALANCE_MOVES = 50


def _movable(item: File, max_bytes: int) -> bool:
    return (
        item.status == "active"
        and not (item.mime_type or "").startswith("application/vnd.google-apps.")
        and 0 < (item.size_bytes or 0) <= max_bytes
    )


def move_to_account(
    db: Session,
    settings: Settings,
    user_id: str,
    file_ids: Sequence[str],
    target_id: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, list[str]]:
    """Pindahkan berkas ke akun lain: unduh, unggah ke tujuan, baru hapus dari asal.

    Mengembalikan (jumlah pindah, daftar pesan). Berkas asal tidak dihapus bila unggahan gagal.
    """
    target = db.get(Account, target_id)
    if target is None or target.user_id != user_id:
        raise AppError("Akun tujuan tidak ditemukan.")
    if target.status != "connected":
        raise AppError("Akun tujuan perlu dihubungkan ulang.")
    limit = settings.max_download_mb * 1024 * 1024
    files = _owned_files(db, user_id, list(file_ids))
    moved, notes = 0, []
    for index, item in enumerate(files):
        if on_progress:
            on_progress(index, len(files))
        if item.account_id == target.id:
            continue
        if not _movable(item, limit):
            notes.append(f"{item.name}: tidak bisa dipindah (dokumen Google, kosong, atau lebih dari {settings.max_download_mb} MB).")
            continue
        free = target.available_bytes
        if free is not None and free < item.size_bytes:
            notes.append(f"{item.name}: ruang akun tujuan tidak cukup.")
            continue
        source = db.get(Account, item.account_id)
        if source is None:
            notes.append(f"{item.name}: akun asal sudah dilepas.")
            continue
        try:
            data, _, _ = _ops(source).download_bytes(service_for(db, settings, source), item.drive_file_id, limit)
            t_service = service_for(db, settings, target)
            folder = _ops(target).ensure_app_folder(t_service, target.app_folder_id)
            if folder != target.app_folder_id:
                target.app_folder_id = folder
            meta = _ops(target).upload_file(
                t_service, folder, item.name, item.mime_type, io.BytesIO(data), len(data), None
            )
        except Exception as exc:
            db.rollback()
            notes.append(f"{item.name}: {_error_text(exc)}")
            continue
        old_id, size = item.drive_file_id, item.size_bytes or 0
        item.account_id, item.drive_file_id, item.source_id = target.id, meta["id"], None
        target.used_bytes = (target.used_bytes or 0) + size
        source.used_bytes = max((source.used_bytes or 0) - size, 0)
        db.commit()  # catatan baru tersimpan sebelum berkas asal dihapus
        moved += 1
        try:
            _ops(source).delete_file(service_for(db, settings, source), old_id)
        except Exception as exc:
            notes.append(f"{item.name}: sudah dipindah, tapi salinan lama di {source.email} gagal dihapus ({_error_text(exc)}).")
    if on_progress:
        on_progress(len(files), len(files))
    _forget_accounts(db)
    return moved, notes


@dataclass
class PlannedMove:
    file_id: str
    name: str
    size: int
    from_id: str
    to_id: str


def balance_plan(
    accounts: Sequence[Account],
    files: Sequence[File],
    max_bytes: int,
    threshold: float = 0.9,
    target: float = 0.8,
    limit: int = MAX_BALANCE_MOVES,
) -> list[PlannedMove]:
    """Rencana memindahkan berkas terbesar dari akun yang lewat batas ke akun yang paling lega."""
    live = [a for a in accounts if a.status == "connected"]
    used = {a.id: a.used_bytes or 0 for a in live}
    plan: list[PlannedMove] = []
    for src in sorted(live, key=lambda a: -(used[a.id] / a.total_bytes) if a.total_bytes else 0):
        if not src.total_bytes or used[src.id] / src.total_bytes <= threshold:
            continue
        mine = sorted((f for f in files if f.account_id == src.id and _movable(f, max_bytes)), key=lambda f: -f.size_bytes)
        for item in mine:
            if used[src.id] <= target * src.total_bytes or len(plan) >= limit:
                break
            options = []
            for dst in live:
                if dst.id == src.id:
                    continue
                if dst.total_bytes is None:
                    options.append((float("inf"), dst))
                    continue
                after = used[dst.id] + item.size_bytes
                if after <= target * dst.total_bytes:
                    options.append((dst.total_bytes - after, dst))
            if not options:
                continue
            _, dst = max(options, key=lambda o: o[0])
            used[src.id] -= item.size_bytes
            used[dst.id] += item.size_bytes
            plan.append(PlannedMove(item.id, item.name, item.size_bytes, src.id, dst.id))
    return plan


# ---------------------------------------------------------------- Log aktivitas

# aksi -> (label, kelompok)
ACTIONS: dict[str, tuple[str, str]] = {
    "file.upload": ("Unggah", "Berkas"),
    "file.rename": ("Ganti nama", "Berkas"),
    "file.move": ("Pindah folder", "Berkas"),
    "file.move_drive": ("Pindah drive", "Berkas"),
    "file.trash": ("Dibuang", "Berkas"),
    "file.restore": ("Dipulihkan", "Berkas"),
    "file.delete": ("Dihapus permanen", "Berkas"),
    "folder.create": ("Folder dibuat", "Berkas"),
    "folder.update": ("Folder diubah", "Berkas"),
    "folder.delete": ("Folder dihapus", "Berkas"),
    "file.share": ("Tautan dibuat", "Berbagi"),
    "file.unshare": ("Tautan dimatikan", "Berbagi"),
    "gallery.create": ("Galeri dibuat", "Berbagi"),
    "gallery.delete": ("Galeri dihapus", "Berbagi"),
    "account.connect": ("Akun terhubung", "Akun"),
    "account.remove": ("Akun dilepas", "Akun"),
    "import.add": ("Folder diimpor", "Akun"),
    "import.sync": ("Impor disinkronkan", "Akun"),
    "import.remove": ("Impor dilepas", "Akun"),
}
ACTIVITY_GROUPS = ("Semua", "Berkas", "Berbagi", "Akun")
ACTIVITY_KEEP_DAYS = 90


def log_activity(db: Session, user_id: str, action: str, detail: str = "") -> None:
    """Catat satu aktivitas. Tidak pernah menggagalkan aksi yang sedang berjalan."""
    try:
        db.add(Activity(user_id=user_id, action=action, detail=(detail or "")[:500]))
        db.commit()
        if secrets.randbelow(50) == 0:  # sesekali bersihkan catatan lama
            from sqlalchemy import delete

            db.execute(
                delete(Activity).where(Activity.user_id == user_id, Activity.created_at < utcnow() - timedelta(days=ACTIVITY_KEEP_DAYS))
            )
            db.commit()
    except Exception:
        db.rollback()


def list_activity(db: Session, user_id: str, group: str = "Semua", limit: int = 30, offset: int = 0) -> list[Activity]:
    query = select(Activity).where(Activity.user_id == user_id)
    if group != "Semua":
        names = [a for a, (_, g) in ACTIONS.items() if g == group]
        query = query.where(Activity.action.in_(names))
    return list(db.scalars(query.order_by(Activity.created_at.desc()).offset(offset).limit(limit)))


def _file_names(db: Session, ids) -> str:
    names = [f.name for f in (db.get(File, i) for i in ids) if f is not None]
    if not names:
        return ""
    shown = ", ".join(names[:3])
    return shown + (f" dan {len(names) - 3} lainnya" if len(names) > 3 else "")


def _install_audit() -> None:
    """Bungkus fungsi layanan agar tiap aksi tercatat, tanpa mengubah isi fungsinya."""
    import functools
    import inspect

    def audit(name, action, before=None, detail=None):
        fn = globals()[name]
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            arg = bound.arguments
            db = arg["db"]
            try:
                ctx = before(db, arg) if before else None
            except Exception:
                ctx = None
            result = fn(*args, **kwargs)
            try:
                user_id = arg["user_id"] if "user_id" in arg else arg["user"].id
                text = detail(db, arg, ctx, result) if detail else ""
                for line in ([text] if isinstance(text, str) else text):
                    if line is not None:
                        log_activity(db, user_id, action, line)
            except Exception:
                pass
            return result

        globals()[name] = inner

    def fname(db, arg):
        item = db.get(File, arg["file_id"])
        return item.name if item else ""

    audit("create_folder", "folder.create", detail=lambda db, a, c, r: r.name)
    audit("update_folder", "folder.update", detail=lambda db, a, c, r: getattr(r, "name", ""))
    audit("delete_folder", "folder.delete", before=lambda db, a: (db.get(Folder, a["folder_id"]) or Folder(name="")).name, detail=lambda db, a, c, r: c)
    audit("rename_file", "file.rename", before=fname, detail=lambda db, a, c, r: f"{c} → {a['new_name']}")
    audit("trash_files", "file.trash", before=lambda db, a: _file_names(db, a["file_ids"]), detail=lambda db, a, c, r: c if r else None)
    audit("restore_files", "file.restore", before=lambda db, a: _file_names(db, a["file_ids"]), detail=lambda db, a, c, r: c if r else None)
    audit("delete_forever", "file.delete", before=lambda db, a: _file_names(db, a["file_ids"]), detail=lambda db, a, c, r: c)
    audit(
        "move_files",
        "file.move",
        before=lambda db, a: _file_names(db, a["file_ids"]),
        detail=lambda db, a, c, r: f"{c} → {(db.get(Folder, a['folder_id']).name if a['folder_id'] else 'Beranda')}" if r else None,
    )
    audit("create_share", "file.share", detail=lambda db, a, c, r: (db.get(File, a["file_id"]) or File(name="")).name)
    audit(
        "disable_share",
        "file.unshare",
        before=lambda db, a: (lambda sh: db.get(File, sh.file_id).name if sh else "")(db.get(Share, a["share_id"])),
        detail=lambda db, a, c, r: c,
    )
    audit("create_gallery", "gallery.create", detail=lambda db, a, c, r: f"{r.title} ({len(r.items)} foto)")
    audit("delete_gallery", "gallery.delete", before=lambda db, a: (db.get(Gallery, a["gallery_id"]) or Gallery(title="")).title, detail=lambda db, a, c, r: c)
    audit("upsert_account", "account.connect", detail=lambda db, a, c, r: f"{r.email} ({'Yandex' if r.provider == 'yandex' else 'Google'})")
    audit("remove_account", "account.remove", before=lambda db, a: (db.get(Account, a["account_id"]) or Account(email="")).email, detail=lambda db, a, c, r: c)
    audit("add_import_source", "import.add", detail=lambda db, a, c, r: r.name)
    audit("remove_import_source", "import.remove", before=lambda db, a: (db.get(ImportSource, a["source_id"]) or ImportSource(name="")).name, detail=lambda db, a, c, r: f"{c} ({r} catatan)")
    audit(
        "sync_import_source",
        "import.sync",
        before=lambda db, a: (db.get(ImportSource, a["source_id"]) or ImportSource(name="")).name,
        detail=lambda db, a, c, r: f"{c}: {r['dibuat']} baru, {r['dihapus']} hilang" if (r["dibuat"] or r["dihapus"]) else None,
    )
    audit(
        "move_to_account",
        "file.move_drive",
        before=lambda db, a: (db.get(Account, a["target_id"]) or Account(email="")).email,
        detail=lambda db, a, c, r: f"{r[0]} berkas → {c}" if r[0] else None,
    )
    audit(
        "upload_batch",
        "file.upload",
        detail=lambda db, a, c, r: [f"{x.name} → {x.account_email}" for x in r if x.ok],
    )


_install_audit()


# ---------------------------------------------------------------- Penggunaan per jenis

USAGE_KINDS = (("foto", "Foto"), ("video", "Video"), ("dokumen", "Dokumen"), ("lainnya", "Lainnya"))
_KIND_TO_USAGE = {"image": "foto", "video": "video", "pdf": "dokumen", "doc": "dokumen", "sheet": "dokumen", "code": "dokumen"}


def usage_by_kind(db: Session, user_id: str) -> dict[str, int]:
    """Jumlah byte berkas aktif yang tercatat di aplikasi, dikelompokkan jadi foto/video/dokumen/lainnya."""
    from .ui_assets import file_kind

    sums = {key: 0 for key, _ in USAGE_KINDS}
    rows = db.execute(
        select(File.mime_type, File.name, File.size_bytes).where(File.user_id == user_id, File.status == "active")
    )
    for mime, name, size in rows:
        sums[_KIND_TO_USAGE.get(file_kind(mime, name), "lainnya")] += size or 0
    return sums


def gallery_zip(settings: Settings, gallery_id: str) -> tuple[bytes, str, list[str]]:
    """ZIP semua foto galeri sesuai mode unduh. Mengembalikan (zip, nama_zip, foto_gagal)."""
    import re
    import zipfile

    factory = get_session_factory(settings.database_url)
    db = factory()
    try:
        gallery = db.get(Gallery, gallery_id)
        if gallery is None:
            raise AppError("Galeri tidak ditemukan.")
        mode = gallery_mode(gallery)
        if mode == "none":
            raise AppError("Galeri ini tidak mengizinkan unduhan.")
        found = resolve_gallery(db, gallery.token)
        if not found:
            raise AppError("Galeri tidak berlaku.")
        files = found[1]
        title = gallery.title
        if mode == "original":
            total = sum(f.size_bytes or 0 for f in files)
            limit = settings.max_download_mb * 1024 * 1024
            if total > limit:
                raise AppError(
                    f"Total {total // (1024 * 1024)} MB melebihi batas {settings.max_download_mb} MB. "
                    "Unduh per foto, atau minta pemilik memakai mode ukuran kecil."
                )
        ids = [f.id for f in files]
    finally:
        db.close()

    def fetch(fid: str):
        session = factory()
        try:
            return fid, gallery_download(session, settings, session.get(Gallery, gallery_id), fid)
        except Exception:
            return fid, None
        finally:
            session.close()

    buffer = io.BytesIO()
    failed: list[str] = []
    used: set[str] = set()
    names = {f.id: f.name for f in files}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive, ThreadPoolExecutor(max_workers=4) as pool:
        for fid, result in pool.map(fetch, ids):
            if result is None:
                failed.append(names[fid])
                continue
            data, name, _ = result
            stem, dot, ext = name.rpartition(".")
            if not dot:
                stem, ext = name, ""
            candidate, n = name, 1
            while candidate in used:
                n += 1
                candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
            used.add(candidate)
            archive.writestr(candidate, data)
    if len(failed) == len(ids):
        raise AppError("Tidak ada foto yang berhasil diambil. Coba lagi nanti.")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-") or "galeri"
    return buffer.getvalue(), f"{slug}.zip", failed
