"""Model database (SQLAlchemy). Default SQLite; untuk hosting gratis pakai DATABASE_URL Postgres."""
from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import (
    event,
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def utcnow() -> datetime:
    """Waktu UTC tanpa zona (naif), seragam untuk SQLite dan Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(191), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Account(Base):
    """Satu akun Google Drive yang terhubung, termasuk kuotanya."""

    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    google_sub: Mapped[str] = mapped_column(String(191))
    email: Mapped[str] = mapped_column(String(191))
    name: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    picture: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str] = mapped_column(String(16), default="google")  # google | yandex
    status: Mapped[str] = mapped_column(String(32), default="connected")  # connected | reauth
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    app_folder_id: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    trash_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    files: Mapped[list["File"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    @property
    def available_bytes(self) -> Optional[int]:
        if self.total_bytes is None:
            return None
        return max(self.total_bytes - (self.used_bytes or 0), 0)


class Folder(Base):
    """Folder virtual: hanya ada di database, file tetap rata di Drive."""

    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("folders.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(16), default="blue")
    icon: Mapped[str] = mapped_column(String(16), default="folder")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=utcnow)


class ImportSource(Base):
    """Folder Drive yang dicatat ke dashboard tanpa memindahkan berkasnya."""

    __tablename__ = "import_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    drive_folder_id: Mapped[str] = mapped_column(String(191))
    name: Mapped[str] = mapped_column(String(255))
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class File(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    folder_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("folders.id"), nullable=True, index=True
    )
    drive_file_id: Mapped[str] = mapped_column(String(191), index=True)
    name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(191), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | trashed | deleted
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)  # ImportSource
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    account: Mapped[Account] = relationship(back_populates="files")
    shares: Mapped[list["Share"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )
    gallery_items: Mapped[list["GalleryItem"]] = relationship(cascade="all, delete-orphan")


class Gallery(Base):
    """Galeri foto yang dibagikan lewat halaman aplikasi (alamat Drive tidak pernah tampil)."""

    __tablename__ = "galleries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(191), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(191))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_download: Mapped[bool] = mapped_column(Boolean, default=False)
    download_mode: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # none | small | original
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["GalleryItem"]] = relationship(
        back_populates="gallery", cascade="all, delete-orphan", order_by="GalleryItem.position"
    )


class GalleryItem(Base):
    __tablename__ = "gallery_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    gallery_id: Mapped[str] = mapped_column(ForeignKey("galleries.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    gallery: Mapped[Gallery] = relationship(back_populates="items")


class Activity(Base):
    """Jejak aktivitas pengguna (unggah, pindah, bagikan, hubungkan akun, dst.)."""

    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Share(Base):
    __tablename__ = "shares"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(191), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    file: Mapped[File] = relationship(back_populates="shares")


class RoutingPolicy(Base):
    __tablename__ = "routing_policies"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), default="most_available")
    priority_ids: Mapped[list] = mapped_column(JSON, default=list)
    cursor: Mapped[int] = mapped_column(Integer, default=0)


class _Stats:
    """Penghitung kueri per thread (dipakai mode DEBUG untuk mencari bagian yang lambat)."""

    def __init__(self) -> None:
        self._local = threading.local()

    def reset(self) -> None:
        self._local.queries = 0
        self._local.seconds = 0.0

    def add(self, seconds: float) -> None:
        self._local.queries = getattr(self._local, "queries", 0) + 1
        self._local.seconds = getattr(self._local, "seconds", 0.0) + seconds

    def snapshot(self) -> tuple[int, float]:
        return getattr(self._local, "queries", 0), getattr(self._local, "seconds", 0.0)


STATS = _Stats()


def _attach_timers(engine) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info["_t0"] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        STATS.add(time.perf_counter() - conn.info.pop("_t0", time.perf_counter()))


_session_factories: dict[str, sessionmaker] = {}


def _normalize_url(url: str) -> str:
    """Pastikan alamat Postgres memakai driver psycopg2.

    SQLAlchemy 2.1 ke atas memakai psycopg (v3) bila hanya tertulis "postgresql://",
    sedangkan requirements.txt memasang psycopg2-binary.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


# Kolom yang ditambahkan setelah rilis awal: (tabel, kolom, definisi SQL).
# create_all tidak menambah kolom pada tabel yang sudah ada, jadi ditambahkan di sini bila belum ada.
_MIGRATIONS = [
    ("folders", "color", "VARCHAR(16) DEFAULT 'blue'"),
    ("folders", "icon", "VARCHAR(16) DEFAULT 'folder'"),
    ("folders", "updated_at", "TIMESTAMP"),
    ("files", "starred", "BOOLEAN DEFAULT FALSE"),
    ("files", "source_id", "VARCHAR(36)"),
    ("galleries", "download_mode", "VARCHAR(8)"),
    ("accounts", "provider", "VARCHAR(16) DEFAULT 'google'"),
]


def _migrate(engine) -> None:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    cache: dict[str, set[str]] = {}
    for table, column, ddl in _MIGRATIONS:
        if table not in existing:
            continue
        if table not in cache:
            cache[table] = {c["name"] for c in inspector.get_columns(table)}
        if column not in cache[table]:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def _ensure_schema(engine) -> None:
    """Buat tabel yang belum ada, lalu tambahkan kolom baru pada tabel lama (sekali per proses)."""
    existing = set(inspect(engine).get_table_names())
    if not set(Base.metadata.tables) <= existing:
        Base.metadata.create_all(engine)
    _migrate(engine)


def ping_db(url: str, times: int = 3) -> tuple[float, float]:
    """Ukur (ms untuk menyambung, ms ping tercepat) dari server aplikasi ke database."""
    url = _normalize_url(url)
    kwargs: dict = {"poolclass": NullPool}
    if make_url(url).get_backend_name() != "sqlite":
        kwargs["connect_args"] = {"connect_timeout": 10}
    engine = create_engine(url, **kwargs)
    try:
        t0 = time.perf_counter()
        with engine.connect() as conn:
            connect_ms = (time.perf_counter() - t0) * 1000
            best = float("inf")
            for _ in range(times):
                t1 = time.perf_counter()
                conn.execute(text("SELECT 1"))
                best = min(best, (time.perf_counter() - t1) * 1000)
        return connect_ms, best
    finally:
        engine.dispose()


def get_session_factory(url: str) -> sessionmaker:
    url = _normalize_url(url)
    if url not in _session_factories:
        kwargs: dict = {}
        parsed = make_url(url)
        if parsed.get_backend_name() == "sqlite":
            if parsed.database and parsed.database != ":memory:":
                folder = os.path.dirname(os.path.abspath(parsed.database))
                os.makedirs(folder, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True
            kwargs["connect_args"] = {"connect_timeout": 10}  # gagal cepat, jangan menggantung
        engine = create_engine(url, **kwargs)
        _attach_timers(engine)
        _ensure_schema(engine)
        _session_factories[url] = sessionmaker(engine, expire_on_commit=False)
    return _session_factories[url]


@contextmanager
def session_scope(url: str) -> Iterator[Session]:
    """Buka sesi database. Jangan panggil st.rerun() di dalam blok ini."""
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
