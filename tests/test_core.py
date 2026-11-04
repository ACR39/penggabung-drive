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


# ------------------------------------------------------------ kecepatan: refresh paralel & latar belakang


@pytest.fixture()
def filedb(tmp_path, monkeypatch):
    """Database SQLite berkas (bukan memori) supaya bisa dipakai beberapa thread."""
    from core import db as dbmod

    dbmod._session_factories.clear()
    url = f"sqlite:///{tmp_path}/t.db"
    settings = Settings("id", "secret", "http://localhost:8501", "k" * 40, url, (), 1)
    db = get_session_factory(url)()
    user = service.get_or_create_user(db, "a@x.com", None)
    ids = []
    for n in range(3):
        acc = Account(user_id=user.id, google_sub=f"s{n}", email=f"a{n}@x.com", refresh_token_enc="x")
        db.add(acc)
        db.commit()
        ids.append(acc.id)
    return db, settings, user, ids


def test_refresh_many_runs_in_parallel_and_reports_errors(filedb, monkeypatch):
    import threading
    import time

    db, settings, user, ids = filedb
    seen = set()

    def fake(db_, settings_, acc):
        seen.add(threading.get_ident())
        time.sleep(0.3)
        if acc.email == "a1@x.com":
            raise service.AppError("token dicabut")
        acc.used_bytes, acc.total_bytes, acc.last_synced_at = 5, 100, utcnow()
        db_.commit()

    monkeypatch.setattr(service, "refresh_quota", fake)
    t0 = time.perf_counter()
    errors = service.refresh_many(settings, ids)
    took = time.perf_counter() - t0
    assert errors == ["a1@x.com: token dicabut"]
    assert took < 0.8, f"harus paralel (3 x 0,3 dtk), tetapi {took:.2f} dtk"
    assert len(seen) == 3
    db.expire_all()
    assert sorted(a.used_bytes for a in service.list_accounts(db, user.id)) == [0, 5, 5]


def test_refresh_in_background_returns_immediately_and_does_not_duplicate(filedb, monkeypatch):
    import threading
    import time

    db, settings, user, ids = filedb
    service._bg_inflight.clear()
    service._bg_last_try.clear()
    gate, calls = threading.Event(), []

    def fake(db_, settings_, acc):
        calls.append(acc.id)
        gate.wait(2)

    monkeypatch.setattr(service, "refresh_quota", fake)
    t0 = time.perf_counter()
    assert service.refresh_in_background(settings, ids) is True
    assert time.perf_counter() - t0 < 0.1  # tidak menunggu Google
    assert service.refresh_in_background(settings, ids) is False  # sudah berjalan / baru dicoba
    gate.set()
    for _ in range(50):
        if not service._bg_inflight:
            break
        time.sleep(0.05)
    assert sorted(calls) == sorted(ids)
    assert not service._bg_inflight


def test_stale_ids_and_accounts_list_is_cached_per_session(filedb):
    from datetime import timedelta

    db, settings, user, ids = filedb
    first = service.list_accounts(db, user.id)
    first[0].last_synced_at = utcnow()
    first[1].last_synced_at = utcnow() - timedelta(hours=1)
    assert service.stale_ids(first) == [first[1].id, first[2].id]
    from core.db import STATS

    STATS.reset()
    service.list_accounts(db, user.id)
    service.list_accounts(db, user.id, False)
    assert STATS.snapshot()[0] == 0  # pemanggilan berikutnya tanpa kueri


# ------------------------------------------------------------ status OAuth tanpa database


def test_signed_state_roundtrip_and_pkce_verifier():
    from core import oauthstate as o

    key = "k" * 40
    state, verifier = o.make_state(key, "connect", "user-1", now=1000)
    got = o.read_state(key, state, now=1100)
    assert got and got.purpose == "connect" and got.user_id == "user-1"
    assert got.code_verifier == verifier and 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    login, _ = o.make_state(key, "login", None, now=1000)
    assert o.read_state(key, login, now=1000).user_id is None
    assert o.make_state(key, "login", None)[0] != o.make_state(key, "login", None)[0]  # acak


def test_signed_state_rejects_tampering_expiry_and_wrong_key():
    from core import oauthstate as o

    key = "k" * 40
    state, _ = o.make_state(key, "login", None, now=1000)
    assert o.read_state(key, state, now=1000 + o.TTL_SECONDS + 1) is None  # kedaluwarsa
    assert o.read_state("x" * 40, state, now=1000) is None  # kunci lain
    payload, sig = state.split(".")
    assert o.read_state(key, payload + "." + sig[:-2] + "AA", now=1000) is None  # tanda tangan diubah
    forged, _ = o.make_state(key, "login", None, now=1000)
    other, _ = o.make_state(key, "connect", "korban", now=1000)
    assert o.read_state(key, forged.split(".")[0] + "." + other.split(".")[1], now=1000) is None
    for junk in ["", "abc", "a.b", "....", "palsu"]:
        assert o.read_state(key, junk) is None


def test_authorization_url_uses_given_verifier_for_challenge():
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlparse

    from core import drive
    from core import oauthstate as o

    settings = Settings("id", "secret", "https://app.example.com", "k" * 40, "sqlite://", (), 1)
    state, verifier = o.make_state(settings.encryption_key, "login", None)
    url = drive.authorization_url(settings, state, True, verifier)
    q = parse_qs(urlparse(url).query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert q["code_challenge"] == [challenge] and q["code_challenge_method"] == ["S256"]
    assert q["state"] == [state] and q["prompt"] == ["select_account consent"]
    assert q["redirect_uri"] == ["https://app.example.com"]


def test_login_page_and_errors_work_even_when_database_is_unreachable(tmp_path):
    """Halaman login tidak boleh menunggu database; galat database tampil sebagai pesan, bukan crash."""
    import os
    import subprocess
    import sys
    import textwrap

    script = tmp_path / "check.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys
        os.environ.update(GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="s", APP_URL="http://localhost:8501",
                          TOKEN_ENCRYPTION_KEY="k"*40, DATABASE_URL="postgresql://u:p@127.0.0.1:1/x")
        sys.path.insert(0, {os.getcwd()!r})
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file({os.path.join(os.getcwd(), "app.py")!r}, default_timeout=30).run()
        assert not at.exception, at.exception
        assert any("Masuk dengan Google" in b.label for b in at.get("link_button"))
        at = AppTest.from_file({os.path.join(os.getcwd(), "app.py")!r}, default_timeout=30)
        at.session_state["user_id"] = "abc"
        at.run()
        assert not at.exception, at.exception
        assert any("Tidak bisa terhubung ke database" in e.value for e in at.error), [e.value for e in at.error]
        print("OK")
    """))
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "OK" in out.stdout, out.stdout + out.stderr[-1500:]


def test_postgres_urls_are_pinned_to_psycopg2():
    from core.db import _normalize_url

    assert _normalize_url("postgresql://u:p@h:5432/db") == "postgresql+psycopg2://u:p@h:5432/db"
    assert _normalize_url("postgres://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"
    assert _normalize_url("postgresql+psycopg2://u:p@h/db").startswith("postgresql+psycopg2://")
    assert _normalize_url("sqlite:///x.db") == "sqlite:///x.db"


# ------------------------------------------------------------ izin Drive kurang (403 Insufficient Permission)


def _http_error(status, text):
    from googleapiclient.errors import HttpError

    class Resp(dict):
        reason = "x"

    resp = Resp(status=str(status))
    resp.status = status
    return HttpError(resp, text.encode())


def test_scope_helpers():
    from core import drive

    assert drive.has_drive_scope("openid https://www.googleapis.com/auth/drive email")
    assert drive.has_drive_scope(["https://www.googleapis.com/auth/drive"])
    assert not drive.has_drive_scope(["openid", "https://www.googleapis.com/auth/userinfo.email"])
    assert not drive.has_drive_scope(None)
    assert drive.is_insufficient_scope(_http_error(403, '{"error":{"message":"Insufficient Permission"}}'))
    assert not drive.is_insufficient_scope(_http_error(403, '{"error":{"message":"Drive API has not been used"}}'))
    assert not drive.is_insufficient_scope(_http_error(404, "Insufficient"))
    assert drive.friendly_error(_http_error(403, "Insufficient Permission")) == drive.SCOPE_HINT


def test_insufficient_permission_marks_account_for_reconnect(env, monkeypatch):
    db, settings, user, (a0, a1), _ = env
    err = _http_error(403, "Insufficient Permission")

    def boom(*args, **kwargs):
        raise err

    monkeypatch.setattr(service.drive, "get_quota", boom)
    with pytest.raises(service.AppError) as info:
        service.refresh_quota(db, settings, a0)
    assert "Izin Google Drive" in str(info.value)
    assert a0.status == "reauth" and "centang" in a0.last_error

    monkeypatch.setattr(service.drive, "upload_file", boom)
    res = service.upload_batch(db, settings, user.id, [("x.txt", "t/p", 5, io.BytesIO(b"1"))], None)
    assert not res[0].ok and "Izin Google Drive" in res[0].message
    assert a1.status == "reauth"  # akun yang terpilih untuk unggahan ikut ditandai
