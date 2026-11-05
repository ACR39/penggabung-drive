"""Uji asap UI: setiap halaman harus tampil tanpa galat. Jalankan: python tests/smoke_ui.py"""
import os, sys, tempfile, io
from datetime import timedelta

tmp = tempfile.mkdtemp()
os.environ.update(
    GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret", APP_URL="http://localhost:8501",
    TOKEN_ENCRYPTION_KEY="k" * 40, YANDEX_CLIENT_ID="yid", YANDEX_CLIENT_SECRET="ysec", DATABASE_URL=f"sqlite:///{tmp}/t.db",
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
for page in ["Kuota", "Berkas", "Dibagikan", "Galeri", "Sampah", "Aktivitas", "Pengaturan"]:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["user_id"] = user.id
    at.session_state["menu"] = page
    at.run()
    ok &= check(page, at)
# masuk ke folder
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.session_state["folder_id"] = fold.id
at.run(); ok &= check("berkas dalam folder", at)
# pencarian
at.text_input(key="search").set_value("foto").run(); ok &= check("pencarian", at)
# buat folder (popover)
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.run()
at.text_input(key="nf_name").set_value("Baru")
at.button(key="nf_make").click().run()
ok &= check("buat folder", at)
# mode, tampilan, ukuran, pilih berkas
for key, val in [("files_mode", "Terbaru"), ("files_mode", "Berbintang"), ("files_view", "Daftar"), ("files_size", "LG")]:
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.session_state[key] = val
    at.run(); ok &= check(f"berkas {key}={val}", at)
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.run()
boxes = [c for c in at.checkbox if str(c.key).startswith("sel")]
if boxes:
    boxes[0].check().run(); ok &= check("pilih berkas", at)
print("folder:", [f.name for f in service.list_subfolders(get_session_factory(s.database_url)(), user.id, None)])
# galeri: publik valid / salah, dan membuat galeri lewat halaman
img = [f for f, _ in service.list_files(db, user.id, recent=True) if f.mime_type.startswith("image/")][0]
gal = service.create_gallery(db, user.id, "Uji", [img.id], 7)
at = AppTest.from_file(APP, default_timeout=60); at.query_params["g"] = gal.token; at.run()
ok &= check("galeri publik valid", at); assert any("Uji" in t.value for t in at.title)
at = AppTest.from_file(APP, default_timeout=30); at.query_params["g"] = "salah"; at.run()
ok &= check("galeri publik salah", at); assert any("tidak berlaku" in e.value for e in at.error)
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Galeri"; at.run()
at.text_input(key="g_title").set_value("Baru"); at.multiselect(key="g_files").set_value([img.id]).run()
at.button(key="g_make").click().run(); ok &= check("buat galeri", at)
# pengaturan menampilkan tombol Yandex
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Pengaturan"; at.run()
assert any("Yandex" in (b.label or "") for b in at.get("link_button")), "tombol Yandex hilang"
ok &= check("tombol yandex", at)
# panel impor di Pengaturan (Drive dipalsukan)
from core import service as _svc
_svc.drive.list_child_folders = lambda *_: [{"id": "F9", "name": "Foto lama"}]
_svc.service_for = lambda *a: object()
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Pengaturan"; at.run()
ok &= check("panel impor", at)
# halaman Kuota dengan panel seimbangkan (2 akun terhubung) + galeri dengan mode unduh
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Kuota"; at.run()
ok &= check("kuota + seimbangkan", at)
at.button(key="bal_plan").click().run(); ok &= check("rencana seimbang", at)
import io as _io
from PIL import Image as _Image
_buf = _io.BytesIO(); _Image.new("RGB", (40, 30), "green").save(_buf, "JPEG")
_svc.gallery_thumbs = lambda settings, gid, ids, **k: {i: _buf.getvalue() for i in ids}
gal2 = service.create_gallery(db, user.id, "Unduh", [img.id], 7, "original")
at = AppTest.from_file(APP, default_timeout=60); at.query_params["g"] = gal2.token; at.run()
ok &= check("galeri mode asli", at); assert any("Unduh" == b.label for b in at.button), "tombol unduh kartu hilang"
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.run()
cb = [c for c in at.checkbox if str(c.key).startswith("sel")]
if cb:
    cb[0].check().run(); ok &= check("pilih + pindah drive", at)
    assert any(b.key == "mv_drive_go" for b in at.button), "kontrol pindah drive hilang"
# dialog Unggah di Berkas
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.run()
[b for b in at.button if b.label == "Unggah"][0].click().run(); ok &= check("dialog unggah", at)
# halaman Aktivitas berisi catatan
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Aktivitas"; at.run()
ok &= check("aktivitas berisi", at); assert any("act-tag" in m.value for m in at.markdown), "daftar aktivitas kosong"
# pilihan folder unggah harus memuat Beranda (akar)
at = AppTest.from_file(APP, default_timeout=30)
at.session_state["user_id"] = user.id; at.session_state["menu"] = "Berkas"; at.run()
[b for b in at.button if b.label == "Unggah"][0].click().run()
assert any(sb.key == "up_dest" and sb.value is None for sb in at.selectbox), "folder akar tidak tersedia"
ok &= check("folder akar di dialog", at)
sys.exit(0 if ok else 1)
