"""Model database (SQLAlchemy). Default SQLite; untuk hosting gratis pakai DATABASE_URL Postgres."""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import (
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
from sqlalchemy.engine import make_url
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    account: Mapped[Account] = relationship(back_populates="files")
    shares: Mapped[list["Share"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


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


class OauthState(Base):
    """Status OAuth sekali pakai; callback Google bisa mendarat di sesi Streamlit baru."""

    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(191), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16))  # login | connect
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    code_verifier: Mapped[Optional[str]] = mapped_column(String(191), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


_session_factories: dict[str, sessionmaker] = {}


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


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
        engine = create_engine(url, **kwargs)
        Base.metadata.create_all(engine)
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
