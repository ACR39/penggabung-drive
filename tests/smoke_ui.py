"""Uji asap UI: setiap halaman harus tampil tanpa galat. Jalankan: python tests/smoke_ui.py"""
import os, sys, tempfile, io
from datetime import timedelta

tmp = tempfile.mkdtemp()
os.environ.update(
    GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret", APP_URL="http://localhost:8501",
    TOKEN_ENCRYPTION_KEY="k" * 40, DATABASE_URL=f"sqlite:///{tmp}/t.db",
)
sys.path.insert(0, os.getcwd())
from streamlit.testing.v1 import AppTest
APP = os.path.join(os.getcwd(), "app.py")
from core import service
from core.config import load_settings
from core.db import Account, File, get_session_factory, utcnow

s = load_settings()
db = get_session_factory(s.database_url)()
user = service.get_or_create_user(db, "saya@x.com", "Saya")
for n, (t, u) in enumerate([(15 * 2**30, 3 * 2**30), (15 * 2**30, 14 * 2**30)]):
    db.add(Account(user_id=user.id, google_sub=f"s{n}", email=f"akun{n}@gmail.com", total_bytes=t,
                   used_bytes=u, last_synced_at=utcnow(), refresh_token_enc="x"))
db.commit()
acc = service.list_accounts(db, user.id)[0]
fold = service.create_folder(db, user.id, None, "Dokumen")
db.add(File(user_id=user.id, account_id=acc.id, drive_file_id="d1", name="laporan.pdf",
            mime_type="application/pdf", size_bytes=1234567, folder_id=fold.id))
db.add(File(user_id=user.id, account_id=acc.id, drive_file_id="d2", name="foto.jpg",
            mime_type="image/jpeg", size_bytes=2048))
db.add(File(user_id=user.id, account_id=acc.id, drive_file_id="d3", name="lama.txt",
            mime_type="text/plain", size_bytes=5, status="trashed", deleted_at=utcnow()))
db.commit()
share = None
item = service.list_files(db, user.id)[0][0]
share = service.create_share(db, user.id, item.id, 7)

def run(setup=None):
    at = AppTest.from_file(APP, default_timeout=30)
    if setup: setup(at)
    return at.run()

def check(name, at):
    errs = [e.value for e in at.exception]
    print(("OK   " if not errs else "GAGAL"), name, errs[:1])
    return not errs

ok = True
# belum masuk
at = run(); ok &= check("login", at)
assert any("Masuk dengan Google" in (b.label or "") for b in at.get("link_button")), "tombol login hilang"
# tautan publik valid & tidak valid
at = AppTest.from_file(APP, default_timeout=30); at.query_params["s"] = share.token; at.run()
ok &= check("publik valid", at); assert any("Siapkan unduhan" in b.label for b in at.button)
at = AppTest.from_file(APP, default_timeout=30); at.query_params["s"] = "salah"; at.run()
ok &= check("publik salah", at); assert any("tidak berlaku" in e.value for e in at.error)
# callback dengan state palsu -> pesan galat, tidak crash
at = AppTest.from_file(APP, default_timeout=30); at.query_params["code"] = "x"; at.query_params["state"] = "palsu"; at.run()
ok &= check("callback palsu", at)
# semua halaman setelah masuk
for page in ["🏠 Beranda", "🗂️ Berkas", "⬆️ Unggah", "🔗 Dibagikan", "🗑️ Sampah", "⚙️ Pengaturan"]:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["user_id"] = user.id
    at.session_state["menu"] = page
    at.run()
    ok &= check(page, at)
# masuk ke folder
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "🗂️ Berkas"; at.session_state["folder_id"] = fold.id
at.run(); ok &= check("berkas dalam folder", at)
# pencarian
at.text_input(key="search").set_value("foto").run(); ok &= check("pencarian", at)
# buat folder
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "🗂️ Berkas"; at.run()
at.text_input(key="new_folder").set_value("Baru")
[b for b in at.button if b.label == "Buat folder"][0].click().run()
ok &= check("buat folder", at)
print("folder:", [f.name for f in service.list_subfolders(get_session_factory(s.database_url)(), user.id, None)])
sys.exit(0 if ok else 1)
