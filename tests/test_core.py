"""Uji logika inti tanpa menghubungi Google. Jalankan: python -m pytest -q"""
import io
from datetime import datetime, timedelta

import pytest

from core import service
from core.config import Settings
from core.crypto import decrypt, encrypt
from core.db import Account, File, get_session_factory, utcnow
from core.routing import Candidate, pick_account

T0 = datetime(2026, 1, 1)


def cands(*rows):
    return [Candidate(i, T0 + timedelta(days=n), a) for n, (i, a) in enumerate(rows)]


# ------------------------------------------------------------ routing


def test_most_available_picks_largest_free_space():
    c = cands(("a", 100), ("b", 500), ("c", 300))
    assert pick_account(c, 50, "most_available")[0] == "b"


def test_most_available_prefers_finite_over_unlimited_and_skips_too_small():
    c = cands(("a", None), ("b", 10), ("c", 400))
    assert pick_account(c, 50, "most_available")[0] == "c"
    assert pick_account(c, 5000, "most_available")[0] == "a"


def test_nothing_fits_returns_none():
    assert pick_account(cands(("a", 10)), 50)[0] is None


def test_reserved_bytes_are_subtracted():
    c = cands(("a", 100), ("b", 90))
    assert pick_account(c, 50, "most_available", reserved={"a": 80})[0] == "b"


def test_priority_order_and_fallback_when_full():
    c = cands(("a", 100), ("b", 100), ("c", 100))
    assert pick_account(c, 10, "priority", ["c", "a"])[0] == "c"
    c2 = cands(("a", 100), ("b", 100), ("c", 5))
    assert pick_account(c2, 10, "priority", ["c", "a"])[0] == "a"


def test_round_robin_cycles_and_advances_cursor():
    c = cands(("a", 100), ("b", 100), ("c", 100))
    picked, cursor = [], 0
    for _ in range(4):
        who, cursor = pick_account(c, 10, "round_robin", [], cursor)
        picked.append(who)
    assert picked == ["a", "b", "c", "a"]


# ------------------------------------------------------------ crypto


def test_encrypt_roundtrip_and_wrong_key():
    key = "k" * 40
    token = encrypt("rahasia", key)
    assert token != "rahasia"
    assert decrypt(token, key) == "rahasia"
    with pytest.raises(ValueError):
        decrypt(token, "x" * 40)


# ------------------------------------------------------------ service


@pytest.fixture()
def env(monkeypatch):
    settings = Settings("id", "secret", "http://localhost:8501", "k" * 40, "sqlite://", (), 1)
    from core import db as dbmod

    dbmod._session_factories.clear()  # database memori baru untuk setiap uji
    db = get_session_factory("sqlite://")()
    user = service.get_or_create_user(db, "Saya@Example.com", "Saya")
    accounts = []
    for n, (total, used) in enumerate([(100, 10), (100, 60)]):
        acc = Account(
            user_id=user.id,
            google_sub=f"s{n}",
            email=f"a{n}@x.com",
            total_bytes=total,
            used_bytes=used,
            last_synced_at=utcnow(),
            created_at=T0 + timedelta(days=n),
        )
        db.add(acc)
        accounts.append(acc)
    db.commit()

    store = {}

    def fake_upload(service_, folder, name, mime, fileobj, size, cb=None):
        fid = f"drv{len(store)}"
        store[fid] = fileobj.read()
        return {"id": fid, "name": name, "mimeType": mime, "size": str(size)}

    monkeypatch.setattr(service, "service_for", lambda *_: object())
    monkeypatch.setattr(service.drive, "ensure_app_folder", lambda *_: "folder1")
    monkeypatch.setattr(service.drive, "upload_file", fake_upload)
    monkeypatch.setattr(service.drive, "delete_file", lambda *_: None)
    yield db, settings, user, accounts, store
    db.close()


def test_email_is_normalised(env):
    db, _, user, *_ = env
    assert user.email == "saya@example.com"
    assert service.get_or_create_user(db, "SAYA@example.com", None).id == user.id


def test_upload_routes_to_account_with_most_space_and_records_file(env):
    db, settings, user, (a0, a1), store = env
    res = service.upload_batch(
        db, settings, user.id, [("a.txt", "text/plain", 20, io.BytesIO(b"x" * 20))], None
    )
    assert res[0].ok and res[0].account_email == "a0@x.com"
    assert a0.used_bytes == 30
    rows = service.list_files(db, user.id)
    assert [f.name for f, _ in rows] == ["a.txt"]


def test_upload_too_big_for_every_account_fails_cleanly(env):
    db, settings, user, *_ = env
    res = service.upload_batch(
        db, settings, user.id, [("big.bin", "x/y", 500, io.BytesIO(b"1"))], None
    )
    assert not res[0].ok and "cukup" in res[0].message


def test_second_upload_sees_updated_usage(env):
    db, settings, user, (a0, a1), _ = env
    items = [
        ("1.bin", "x/y", 85, io.BytesIO(b"1")),  # a0 punya 90 sisa -> muat, tinggal 5
        ("2.bin", "x/y", 30, io.BytesIO(b"2")),  # a0 tinggal 5, a1 sisa 40 -> a1
    ]
    res = service.upload_batch(db, settings, user.id, items, None)
    assert [r.account_email for r in res] == ["a0@x.com", "a1@x.com"]


def test_virtual_folders_and_move(env):
    db, settings, user, *_ = env
    docs = service.create_folder(db, user.id, None, "Dokumen")
    sub = service.create_folder(db, user.id, docs.id, "Pajak")
    with pytest.raises(service.AppError):
        service.create_folder(db, user.id, docs.id, "Pajak")
    with pytest.raises(service.AppError):
        service.create_folder(db, user.id, None, "a/b")
    service.upload_batch(
        db, settings, user.id, [("f.txt", "text/plain", 1, io.BytesIO(b"1"))], sub.id
    )
    assert [f.name for f, _ in service.list_files(db, user.id, sub.id)] == ["f.txt"]
    assert service.list_files(db, user.id, None) == []
    assert [f.name for f in service.breadcrumb(db, user.id, sub.id)] == ["Dokumen", "Pajak"]
    # hapus folder -> isi naik ke induk
    service.delete_folder(db, user.id, sub.id)
    assert [f.name for f, _ in service.list_files(db, user.id, docs.id)] == ["f.txt"]


def test_search_ignores_folder_and_escapes_wildcards(env):
    db, settings, user, *_ = env
    folder = service.create_folder(db, user.id, None, "X")
    items = [
        ("laporan_2026.pdf", "application/pdf", 1, io.BytesIO(b"1")),
        ("laporanA2026.pdf", "application/pdf", 1, io.BytesIO(b"1")),
    ]
    service.upload_batch(db, settings, user.id, items, folder.id)
    found = service.list_files(db, user.id, None, query="laporan_2026")
    assert [f.name for f, _ in found] == ["laporan_2026.pdf"]  # "_" bukan wildcard
    assert len(service.list_files(db, user.id, None, query="LAPORAN")) == 2


def test_trash_restore_and_delete_forever_updates_usage(env):
    db, settings, user, (a0, _), _ = env
    service.upload_batch(db, settings, user.id, [("t.txt", "t/p", 20, io.BytesIO(b"1"))], None)
    fid = service.list_files(db, user.id)[0][0].id
    assert service.trash_files(db, user.id, [fid]) == 1
    assert service.list_files(db, user.id) == []
    assert len(service.list_files(db, user.id, status="trashed")) == 1
    assert service.restore_files(db, user.id, [fid]) == 1
    service.trash_files(db, user.id, [fid])
    done, errors = service.delete_forever(db, settings, user.id, [fid])
    assert (done, errors) == (1, [])
    assert a0.used_bytes == 10


def test_other_users_cannot_touch_my_files(env):
    db, settings, user, *_ = env
    service.upload_batch(db, settings, user.id, [("p.txt", "t/p", 1, io.BytesIO(b"1"))], None)
    fid = service.list_files(db, user.id)[0][0].id
    other = service.get_or_create_user(db, "lain@x.com", None)
    assert service.trash_files(db, other.id, [fid]) == 0
    assert service.delete_forever(db, settings, other.id, [fid]) == (0, [])
    with pytest.raises(service.AppError):
        service.create_share(db, other.id, fid, None)


def test_share_lifecycle(env):
    db, settings, user, *_ = env
    service.upload_batch(db, settings, user.id, [("s.txt", "t/p", 1, io.BytesIO(b"1"))], None)
    item = service.list_files(db, user.id)[0][0]
    share = service.create_share(db, user.id, item.id, 7)
    assert len(share.token) >= 30
    assert service.resolve_public_share(db, share.token)[1].id == item.id
    assert service.resolve_public_share(db, "salah") is None
    share.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert service.resolve_public_share(db, share.token) is None  # kedaluwarsa
    share.expires_at = None
    db.commit()
    service.trash_files(db, user.id, [item.id])
    assert service.resolve_public_share(db, share.token) is None  # file di sampah
    service.restore_files(db, user.id, [item.id])
    service.disable_share(db, user.id, share.id)
    assert service.resolve_public_share(db, share.token) is None  # dimatikan


def test_oauth_state_is_single_use_and_expires(env):
    db, *_ = env
    service.save_oauth_state(db, "st1", "login", None, "ver")
    first = service.pop_oauth_state(db, "st1")
    assert first and first.purpose == "login" and first.code_verifier == "ver"
    assert service.pop_oauth_state(db, "st1") is None
    service.save_oauth_state(db, "st2", "connect", "u1", None)
    from core.db import OauthState

    row = db.get(OauthState, "st2")
    row.created_at = utcnow() - timedelta(hours=2)
    db.commit()
    assert service.pop_oauth_state(db, "st2") is None


def test_sync_account_files(env, monkeypatch):
    db, settings, user, (a0, _), _ = env
    monkeypatch.setattr(
        service.drive,
        "list_folder_files",
        lambda *_: [
            {"id": "n1", "name": "baru.txt", "mimeType": "text/plain", "size": "5"},
        ],
    )
    monkeypatch.setattr(service, "refresh_quota", lambda *_: None)
    db.add(
        File(user_id=user.id, account_id=a0.id, drive_file_id="lama", name="lama.txt", size_bytes=1)
    )
    db.commit()
    out = service.sync_account_files(db, settings, a0)
    assert out == {"dibuat": 1, "diperbarui": 0, "dihapus": 1}
    names = {f.name: f.status for f in db.query(File).all()}
    assert names == {"baru.txt": "active", "lama.txt": "deleted"}


def test_quota_totals_skip_unlimited_and_disconnected(env):
    db, _, user, (a0, a1), _ = env
    t = service.quota_totals([a0, a1])
    assert (t.total, t.used, t.free) == (200, 70, 130)
    a1.status = "reauth"
    assert service.quota_totals([a0, a1]).total == 100
    a0.total_bytes = None
    assert service.quota_totals([a0]).unlimited_accounts == 1
