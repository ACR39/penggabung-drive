"""Penggabung Drive: gabungkan beberapa akun Google Drive menjadi satu penyimpanan virtual.

Terinspirasi dari konsep 9Drive (github.com/zenhosta/9drive, Apache-2.0).
Kode ini ditulis ulang dari nol dalam Python/Streamlit.
"""
from __future__ import annotations

import html
import secrets
import time
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core import drive, oauthstate, service
from core.config import Settings, load_settings
from sqlalchemy.exc import InterfaceError, OperationalError

from core.db import STATS, get_session_factory, ping_db
from core.service import AppError, NeedsConsent
from core.utils import file_icon, fmt_bytes, fmt_dt

st.set_page_config(page_title="Penggabung Drive", page_icon="🗂️", layout="wide")

CSS = """
<style>
.stApp { background:linear-gradient(180deg,#f8f9fe 0%,#f2f3fa 100%); }
header[data-testid="stHeader"] { background:transparent; }
.stAppDeployButton, footer { display:none !important; }
.block-container { padding-top:2.2rem; max-width:1180px; }

/* sidebar */
[data-testid="stSidebar"] { background:#fff; border-right:1px solid #eceef6; }
[data-testid="stSidebar"] [role="radiogroup"] { gap:.2rem; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding:.6rem .9rem; border-radius:12px; width:100%; cursor:pointer; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:#f3f4fb; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child { display:none; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background:#e0e7ff; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p { color:#4338ca; font-weight:600; }
.brand { display:flex; align-items:center; gap:.7rem; font-weight:700; font-size:1.2rem; color:#111827; margin:.2rem 0 1.1rem; }
.logo { width:38px; height:38px; border-radius:12px; background:linear-gradient(135deg,#818cf8,#4f46e5); display:grid; place-items:center; box-shadow:0 6px 14px rgba(79,70,229,.35); }
.userbox { display:flex; align-items:center; gap:.7rem; padding:.6rem 0 1rem; border-bottom:1px solid #eceef6; margin-bottom:.9rem; }
.avatar { width:38px; height:38px; border-radius:50%; background:#e0e7ff; color:#4338ca; display:grid; place-items:center; font-weight:700; }
.u-name { font-weight:600; color:#111827; font-size:.95rem; line-height:1.2; }
.u-mail { color:#6b7280; font-size:.8rem; word-break:break-all; }
.side-storage { margin-top:1.2rem; padding-top:1rem; border-top:1px solid #eceef6; }
.side-storage .row { display:flex; justify-content:space-between; font-size:.85rem; color:#374151; margin-bottom:.45rem; }

/* kartu & tombol */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius:16px; border:1px solid #eceef6; background:#fff !important; box-shadow:0 4px 18px rgba(30,41,90,.05); }
.stButton > button, .stDownloadButton > button, [data-testid="stBaseLinkButton-secondary"], [data-testid="stBaseLinkButton-primary"] { border-radius:12px; font-weight:500; }
.stButton > button[kind="secondary"], .stDownloadButton > button { border:1px solid #e5e7f2; background:#fff; box-shadow:0 1px 2px rgba(30,41,90,.06); }
[data-testid="stBaseButton-primary"], [data-testid="stBaseLinkButton-primary"] { box-shadow:0 8px 18px rgba(79,70,229,.30); }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="base-input"] { border-radius:12px !important; }

/* judul, statistik, akun */
.page-title { font-size:2rem; font-weight:700; color:#111827; line-height:1.15; }
.page-sub { color:#6b7280; margin:.25rem 0 1.1rem; }
.stat-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin:.4rem 0 1.4rem; }
.stat { background:#fff; border-radius:16px; padding:1rem 1.25rem; border:1px solid #f0f1f8; box-shadow:0 4px 18px rgba(30,41,90,.06); }
.stat .l { color:#6b7280; font-size:.88rem; }
.stat .v { font-size:1.65rem; font-weight:700; color:#111827; margin-top:.25rem; }
.acc-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:1rem; margin-top:.6rem; }
.acc { background:#fff; border-radius:16px; padding:1.1rem 1.25rem; border:1px solid #f0f1f8; box-shadow:0 4px 18px rgba(30,41,90,.06); }
.acc-top { display:flex; align-items:center; gap:.75rem; margin-bottom:.9rem; }
.acc-ico { width:40px; height:40px; border-radius:12px; background:linear-gradient(135deg,#818cf8,#4f46e5); color:#fff; display:grid; place-items:center; font-size:1.1rem; flex:none; }
.acc-name { font-weight:600; color:#111827; word-break:break-all; }
.acc-sub { color:#6b7280; font-size:.84rem; }
.acc-top .chip { margin-left:auto; flex:none; }
.bar { height:8px; border-radius:999px; background:#eef0f8; overflow:hidden; margin:.2rem 0 .55rem; }
.bar > span { display:block; height:100%; border-radius:999px; background:linear-gradient(90deg,#818cf8,#4f46e5); }
.bar.warn > span { background:linear-gradient(90deg,#f59e0b,#ef4444); }
.chip { display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.76rem; font-weight:500; background:#dcfce7; color:#166534; }
.chip.warn { background:#fee2e2; color:#991b1b; }
.row-name { font-weight:600; color:#111827; word-break:break-all; }
.login-card { background:#fff; border:1px solid #eceef6; border-radius:20px; padding:2.2rem 2rem 1.6rem; box-shadow:0 12px 40px rgba(30,41,90,.10); margin-top:3rem; }
@media (max-width:820px) { .stat-grid { grid-template-columns:repeat(2,1fr); } .acc-grid { grid-template-columns:1fr; } }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

LOGO_SVG = (
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 3 8l9 5 9-5-9-5Z"/>'
    '<path d="m3 13 9 5 9-5"/></svg>'
)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def page_header(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="page-title">{esc(title)}</div><div class="page-sub">{esc(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def stat_cards(items) -> None:
    cells = "".join(
        f'<div class="stat"><div class="l">{esc(label)}</div><div class="v">{esc(value)}</div></div>'
        for label, value in items
    )
    st.markdown(f'<div class="stat-grid">{cells}</div>', unsafe_allow_html=True)


def bar_html(used: int, total: int | None) -> str:
    pct = 0 if not total else min(used / total * 100, 100)
    warn = " warn" if pct >= 90 else ""
    return f'<div class="bar{warn}"><span style="width:{pct:.1f}%"></span></div>'


def account_card_html(acc) -> str:
    if acc.status == "connected":
        chip = '<span class="chip">Terhubung</span>'
        body = (
            bar_html(acc.used_bytes or 0, acc.total_bytes)
            + f'<div class="acc-sub">{esc(fmt_bytes(acc.used_bytes))} terpakai · '
            f'{esc(fmt_bytes(acc.available_bytes))} tersedia</div>'
        )
    else:
        chip = '<span class="chip warn">Hubungkan ulang</span>'
        body = f'<div class="acc-sub">{esc(acc.last_error or "Izin Google perlu diperbarui.")}</div>'
    return (
        f'<div class="acc"><div class="acc-top"><div class="acc-ico">☁</div><div>'
        f'<div class="acc-name">{esc(acc.email)}</div><div class="acc-sub">Google Drive</div></div>{chip}</div>{body}</div>'
    )


MENU = ["Kuota", "Berkas", "Unggah", "Dibagikan", "Sampah", "Pengaturan"]


# ------------------------------------------------------------------ pembantu


def flash(kind: str, text: str) -> None:
    """Simpan pesan agar tetap tampil setelah st.rerun()."""
    st.session_state.pop("_totals", None)  # ada perubahan -> hitung ulang ringkasan di sidebar
    st.session_state.setdefault("_flash", []).append((kind, text))


def show_flash() -> None:
    for kind, text in st.session_state.pop("_flash", []):
        {"ok": st.success, "err": st.error, "info": st.info}.get(kind, st.info)(text)


def login_url(settings: Settings, purpose: str, user_id: str | None, consent: bool) -> str:
    state, verifier = oauthstate.make_state(settings.encryption_key, purpose, user_id)
    return drive.authorization_url(settings, state, consent, verifier)


@dataclass
class SessionUser:
    id: str
    email: str
    name: str | None


def current_user(db):
    """Pengguna dari sesi (tanpa kueri database). Kueri hanya dilakukan sekali bila data sesi belum ada."""
    uid = st.session_state.get("user_id")
    if not uid:
        return None
    if "user_email" in st.session_state:
        return SessionUser(uid, st.session_state["user_email"], st.session_state.get("user_name"))
    from core.db import User

    user = db.get(User, uid)
    if user is None:
        st.session_state.pop("user_id", None)
        return None
    st.session_state["user_email"], st.session_state["user_name"] = user.email, user.name
    return SessionUser(user.id, user.email, user.name)


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.query_params.clear()
    st.rerun()


# ------------------------------------------------------------------ halaman publik


def public_page(settings: Settings, db, token: str) -> None:
    st.title("🔗 Berkas dibagikan")
    found = service.resolve_public_share(db, token)
    if not found:
        st.error("Tautan ini tidak berlaku. Mungkin sudah kedaluwarsa atau dimatikan pemiliknya.")
        return
    _, item = found
    st.subheader(f"{file_icon(item.mime_type)} {item.name}")
    st.caption(f"{fmt_bytes(item.size_bytes)} · {item.mime_type}")
    key = f"pub_{token}"
    if key not in st.session_state:
        if st.button("Siapkan unduhan", type="primary"):
            with st.spinner("Mengambil berkas dari Google Drive…"):
                try:
                    st.session_state[key] = service.download_file(db, settings, item)
                except AppError as exc:
                    st.error(str(exc))
                    return
            st.rerun()
        return
    data, name, mime = st.session_state[key]
    st.download_button("⬇️ Unduh berkas", data, file_name=name, mime=mime, type="primary")


# ------------------------------------------------------------------ login & callback


def handle_callback(settings: Settings, db) -> None:
    params = st.query_params
    code, state, err = params.get("code"), params.get("state"), params.get("error")
    st.query_params.clear()
    if err:
        flash("err", f"Google membatalkan proses masuk ({err}).")
        return
    pending = oauthstate.read_state(settings.encryption_key, state or "")
    if not pending:
        flash("err", "Sesi masuk sudah habis atau tidak dikenal. Silakan coba lagi.")
        return
    try:
        creds, profile = drive.exchange_code(settings, code or "", pending.code_verifier)
    except Exception as exc:
        flash("err", f"Gagal menukar kode Google: {drive.friendly_error(exc)}")
        return

    if pending.purpose == "connect":
        from core.db import User

        user = db.get(User, pending.user_id or "")
        if user is None:
            flash("err", "Pengguna tidak ditemukan. Masuk dulu.")
            return
    else:
        user = service.resolve_login_user(db, profile)
        if settings.allowed_emails and user.email not in settings.allowed_emails:
            flash("err", f"{user.email} tidak diizinkan memakai aplikasi ini.")
            return
    try:
        acc = service.upsert_account(db, settings, user, profile, creds)
        service.refresh_quota(db, settings, acc)
    except NeedsConsent:
        st.session_state["need_consent"] = True
        flash(
            "info",
            "Google belum memberi izin penyimpanan jangka panjang. "
            "Klik tombol masuk lagi, lalu setujui semua izin.",
        )
        return
    except AppError as exc:
        flash("err", str(exc))
    st.session_state["user_id"] = user.id
    st.session_state["user_email"], st.session_state["user_name"] = user.email, user.name
    flash("ok", f"Akun {profile.get('email')} terhubung.")


def show_db_error(settings: Settings, exc: Exception) -> None:
    st.error("Tidak bisa terhubung ke database.")
    st.markdown(
        "- Periksa `DATABASE_URL` di Secrets (password benar dan sudah di-URL-encode).\n"
        "- Di Supabase pakai alamat **Session pooler** (host `...pooler.supabase.com`), bukan `db.xxx.supabase.co` "
        "yang hanya IPv6.\n"
        "- Pastikan proyek Supabase tidak sedang dijeda (paused).\n"
        "- Isi `DEBUG = \"1\"` di Secrets untuk melihat detail dan ping database."
    )
    if settings.debug:
        st.code(str(getattr(exc, "orig", exc))[:600], language=None)


def db_ping_caption(settings: Settings) -> None:
    try:
        connect_ms, ping_ms = ping_db(settings.database_url)
    except Exception as exc:
        st.caption(f"🔌 Database tidak terjangkau: {str(getattr(exc, 'orig', exc))[:300]}")
        return
    verdict = "bagus" if ping_ms < 50 else "cukup" if ping_ms < 150 else "jauh, pertimbangkan pindah region"
    st.caption(f"🔌 Database: sambung {connect_ms:.0f} ms · ping {ping_ms:.0f} ms ({verdict})")


def login_page(settings: Settings) -> None:
    left, mid, right = st.columns([1, 1.6, 1])
    with mid:
        st.write("")
        st.write("")
        with st.container(border=True):
            st.markdown(
                f'<div class="brand"><div class="logo">{LOGO_SVG}</div>Penggabung Drive</div>'
                '<div class="page-title" style="font-size:1.6rem">Satu penyimpanan, banyak akun</div>'
                '<div class="page-sub">Gabungkan beberapa Google Drive. Unggahan otomatis dipilihkan akun yang masih punya ruang.</div>',
                unsafe_allow_html=True,
            )
            show_flash()
            consent = st.session_state.get("need_consent", False)
            if "login_url" not in st.session_state or st.session_state.get("login_consent") != consent:
                st.session_state["login_url"] = login_url(settings, "login", None, consent)
                st.session_state["login_consent"] = consent
            st.link_button(
                "Masuk dengan Google", st.session_state["login_url"], type="primary", width="stretch"
            )
            st.caption(
                "Masuklah dengan akun Google utama. Akun lain ditambahkan setelah masuk. "
                "Halaman Google terbuka di tab baru."
            )
            if settings.debug:
                db_ping_caption(settings)


# ------------------------------------------------------------------ halaman utama


MODE_LABELS = {
    "most_available": "Ruang terbanyak",
    "round_robin": "Bergantian",
    "priority": "Urutan prioritas",
}


def ordered_accounts(accounts, priority_ids):
    """Akun terhubung diurutkan: yang ada di daftar prioritas dulu, sisanya menurut tanggal dibuat."""
    rank = {i: n for n, i in enumerate(priority_ids or [])}
    return sorted(accounts, key=lambda a: (0, rank[a.id], a.created_at) if a.id in rank else (1, 0, a.created_at))


def page_home(settings: Settings, db, user) -> None:
    accounts = service.list_accounts(db, user.id)
    head = st.columns([5, 1.4])
    with head[0]:
        page_header("Pelacak Kuota", "Pantau batas penyimpanan semua akun yang terhubung.")
    with head[1]:
        st.write("")
        if st.button("Segarkan", icon=":material/refresh:", width="stretch"):
            with st.spinner("Menghubungi Google…"):
                ids = [a.id for a in accounts if a.status == "connected"]
                for msg in service.refresh_many(settings, ids):
                    flash("err", msg)
            flash("ok", "Kuota diperbarui.")
            st.rerun()
    show_flash()
    for acc in accounts:
        if acc.status == "connected" and acc.last_error:
            st.warning(f"{acc.email}: {acc.last_error}")
    if not accounts:
        st.info("Belum ada akun. Buka Pengaturan untuk menghubungkan Google Drive.")
        return
    totals = service.quota_totals(accounts)
    stat_cards(
        [
            ("Total ruang", fmt_bytes(totals.total)),
            ("Terpakai", fmt_bytes(totals.used)),
            ("Tersedia", fmt_bytes(totals.free)),
            ("Akun", sum(1 for a in accounts if a.status == "connected")),
        ]
    )
    if totals.unlimited_accounts:
        st.caption(f"{totals.unlimited_accounts} akun berkuota tak terbatas tidak dihitung ke total.")

    connected = [a for a in accounts if a.status == "connected"]
    if connected:
        policy = service.get_policy(db, user.id)
        order = ordered_accounts(connected, policy.priority_ids)
        with st.container(border=True):
            top = st.columns([3, 2])
            top[0].markdown("**Aturan unggah**")
            top[0].caption("Pilih bagaimana unggahan baru memilih akun penyimpanan.")
            mode = top[1].selectbox(
                "Mode",
                list(MODE_LABELS),
                index=list(MODE_LABELS).index(policy.mode) if policy.mode in MODE_LABELS else 0,
                format_func=lambda m: MODE_LABELS[m],
                key="route_mode",
                label_visibility="collapsed",
            )
            if mode != policy.mode:
                service.set_policy(db, user.id, mode, [a.id for a in order])
                flash("ok", f"Aturan diubah: {MODE_LABELS[mode]}.")
                st.rerun()
            for i, acc in enumerate(order):
                row = st.columns([6, 1.1, 1.1])
                row[0].markdown(f'<div class="row-name">{esc(acc.email)}</div>', unsafe_allow_html=True)
                row[0].caption(
                    f"Google Drive · {fmt_bytes(acc.used_bytes)} terpakai · {fmt_bytes(acc.available_bytes)} tersedia"
                )
                for col, label, delta in ((row[1], "Naik", -1), (row[2], "Turun", 1)):
                    j = i + delta
                    if col.button(label, key=f"{label}_{acc.id}", disabled=not 0 <= j < len(order), width="stretch"):
                        ids = [a.id for a in order]
                        ids[i], ids[j] = ids[j], ids[i]
                        service.set_policy(db, user.id, policy.mode, ids)
                        st.rerun()
            st.caption("Urutan ini dipakai oleh mode Bergantian dan Urutan prioritas.")

    st.markdown(
        '<div class="acc-grid">' + "".join(account_card_html(a) for a in accounts) + "</div>",
        unsafe_allow_html=True,
    )
    st.caption("Kuota terakhir disinkronkan: " + ", ".join(f"{a.email.split('@')[0]} {fmt_dt(a.last_synced_at)} UTC" for a in connected))


def folder_picker(db, user, label: str, key: str, exclude: str | None = None) -> str | None:
    options = [o for o in service.folder_options(db, user.id) if o[0] != exclude]
    labels = {fid: text for fid, text in options}
    return st.selectbox(label, [fid for fid, _ in options], format_func=lambda x: labels[x], key=key)


def page_files(settings: Settings, db, user) -> None:
    page_header("Berkas", "Cari, atur, dan bagikan berkas dari semua akun.")
    show_flash()
    folder_id = st.session_state.get("folder_id")
    crumbs = service.breadcrumb(db, user.id, folder_id)
    if folder_id and not crumbs:
        folder_id = st.session_state["folder_id"] = None

    # cari + navigasi
    search = st.text_input("🔍 Cari berkas di semua akun", key="search", placeholder="ketik sebagian nama…")
    searching = bool(search.strip())

    if not searching:
        path = st.columns([1] * min(len(crumbs) + 1, 6) + [max(6 - len(crumbs), 1)])
        if path[0].button("🏠 Beranda", key="bc_root"):
            st.session_state["folder_id"] = None
            st.rerun()
        for i, crumb in enumerate(crumbs[-5:], start=1):
            if path[i].button(crumb.name, key=f"bc_{crumb.id}"):
                st.session_state["folder_id"] = crumb.id
                st.rerun()

        subs = service.list_subfolders(db, user.id, folder_id)
        if subs:
            grid = st.columns(4)
            for i, sub in enumerate(subs):
                if grid[i % 4].button(f"📁 {sub.name}", key=f"open_{sub.id}", width="stretch"):
                    st.session_state["folder_id"] = sub.id
                    st.rerun()
        with st.expander("Kelola folder"):
            new_name = st.text_input("Folder baru di sini", key="new_folder")
            if st.button("Buat folder"):
                try:
                    service.create_folder(db, user.id, folder_id, new_name)
                    flash("ok", f"Folder “{new_name.strip()}” dibuat.")
                except AppError as exc:
                    flash("err", str(exc))
                st.rerun()
            if folder_id:
                ren = st.text_input("Ganti nama folder ini", value=crumbs[-1].name, key=f"ren_f_{folder_id}")
                cols = st.columns(2)
                if cols[0].button("Simpan nama"):
                    try:
                        service.rename_folder(db, user.id, folder_id, ren)
                        flash("ok", "Nama folder diganti.")
                    except AppError as exc:
                        flash("err", str(exc))
                    st.rerun()
                if cols[1].button("Hapus folder ini (isi naik ke induk)"):
                    st.session_state["folder_id"] = service.delete_folder(db, user.id, folder_id)
                    flash("ok", "Folder dihapus. Isinya dipindah ke folder induk.")
                    st.rerun()

    rows = service.list_files(db, user.id, folder_id, search if searching else None)
    if not rows:
        st.info("Tidak ada berkas." if searching else "Folder ini masih kosong. Unggah berkas lewat menu Unggah.")
        return
    frame = pd.DataFrame(
        [
            {
                "Nama": f"{file_icon(f.mime_type)} {f.name}",
                "Ukuran": fmt_bytes(f.size_bytes),
                "Akun": email,
                "Tanggal": fmt_dt(f.created_at),
            }
            for f, email in rows
        ]
    )
    event = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key=f"tbl_{folder_id}_{search}",
    )
    picked = [rows[i][0] for i in event.selection.rows if i < len(rows)]
    if not picked:
        st.caption(f"{len(rows)} berkas. Pilih baris untuk melihat aksi.")
        return
    st.markdown(f"**{len(picked)} dipilih**")
    ids = [f.id for f in picked]
    act = st.columns(3)
    with act[0]:
        dest = folder_picker(db, user, "Pindahkan ke", f"mv_{folder_id}")
        if st.button("Pindahkan"):
            n = service.move_files(db, user.id, ids, dest)
            flash("ok", f"{n} berkas dipindahkan.")
            st.rerun()
    with act[1]:
        st.write("")
        st.write("")
        if st.button("🗑️ Buang ke sampah"):
            n = service.trash_files(db, user.id, ids)
            flash("ok", f"{n} berkas dibuang ke sampah.")
            st.rerun()
    if len(picked) == 1:
        item = picked[0]
        with act[2]:
            new_name = st.text_input("Ganti nama", value=item.name, key=f"ren_{item.id}")
            if st.button("Simpan nama berkas"):
                try:
                    with st.spinner("Mengganti nama di Google Drive…"):
                        service.rename_file(db, settings, user.id, item.id, new_name)
                    flash("ok", "Nama berkas diganti.")
                except AppError as exc:
                    flash("err", str(exc))
                st.rerun()
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            dl_key = f"dl_{item.id}"
            if dl_key in st.session_state:
                data, name, mime = st.session_state[dl_key]
                st.download_button("⬇️ Unduh sekarang", data, file_name=name, mime=mime, type="primary")
            elif st.button("Siapkan unduhan"):
                try:
                    with st.spinner("Mengambil dari Google Drive…"):
                        st.session_state[dl_key] = service.download_file(db, settings, item)
                    st.rerun()
                except AppError as exc:
                    st.error(str(exc))
        with c2:
            label = st.selectbox("Masa berlaku tautan", list(service.EXPIRY_CHOICES), key=f"exp_{item.id}")
            if st.button("Buat tautan berbagi"):
                try:
                    share = service.create_share(db, user.id, item.id, service.EXPIRY_CHOICES[label])
                    st.session_state["new_share"] = share.token
                except AppError as exc:
                    st.error(str(exc))
            if st.session_state.get("new_share"):
                st.code(f"{settings.app_url}/?s={st.session_state['new_share']}", language=None)


def page_upload(settings: Settings, db, user) -> None:
    page_header("Unggah", "Kirim berkas ke akun yang paling sesuai.")
    accounts = service.list_accounts(db, user.id, connected_only=True)
    if not accounts:
        st.info("Hubungkan minimal satu akun Google Drive di Pengaturan.")
        return
    show_flash()
    policy = service.get_policy(db, user.id)
    mode_names = {"most_available": "ruang terbanyak", "round_robin": "bergantian", "priority": "urutan prioritas"}
    st.caption(f"Aturan pemilihan akun: **{mode_names.get(policy.mode, policy.mode)}** (ubah di Pengaturan).")
    dest = folder_picker(db, user, "Simpan ke folder virtual", "up_dest")
    emails = {a.id: a.email for a in accounts}
    target = st.selectbox(
        "Akun tujuan",
        [None] + [a.id for a in accounts],
        format_func=lambda x: "Otomatis (ikuti aturan)" if x is None else emails[x],
        key="up_target",
    )
    files = st.file_uploader("Pilih berkas", accept_multiple_files=True, key=f"up_files_{st.session_state.get('up_round', 0)}")
    if not files:
        return
    st.write(f"{len(files)} berkas · {fmt_bytes(sum(f.size for f in files))}")
    if st.button("Mulai unggah", type="primary"):
        bar = st.progress(0.0, text="Menyiapkan…")

        def on_progress(index: int, total: int, fraction: float) -> None:
            bar.progress((index + fraction) / total, text=f"Berkas {index + 1} dari {total}")

        items = [(f.name, f.type or "application/octet-stream", f.size, f) for f in files]
        results = service.upload_batch(db, settings, user.id, items, dest, target, on_progress)
        bar.empty()
        for r in results:
            if r.ok:
                flash("ok", f"✅ {r.name} → {r.account_email}")
            else:
                flash("err", f"❌ {r.name}: {r.message}")
        st.session_state["up_round"] = st.session_state.get("up_round", 0) + 1  # kosongkan uploader
        st.rerun()


def page_shared(settings: Settings, db, user) -> None:
    page_header("Dibagikan", "Tautan publik yang sedang aktif.")
    show_flash()
    shares = service.list_shares(db, user.id)
    if not shares:
        st.info("Belum ada tautan aktif. Buat dari menu Berkas dengan memilih satu berkas.")
        return
    for share, item in shares:
        with st.container(border=True):
            cols = st.columns([5, 2, 1])
            cols[0].markdown(f"**{file_icon(item.mime_type)} {item.name}**")
            cols[0].code(f"{settings.app_url}/?s={share.token}", language=None)
            cols[1].caption(
                "Tanpa batas waktu" if share.expires_at is None else f"Berlaku sampai {fmt_dt(share.expires_at)} UTC"
            )
            if cols[2].button("Matikan", key=f"off_{share.id}"):
                service.disable_share(db, user.id, share.id)
                flash("ok", "Tautan dimatikan.")
                st.rerun()


def page_trash(settings: Settings, db, user) -> None:
    page_header("Sampah", "Pulihkan atau hapus permanen berkas.")
    show_flash()
    rows = service.list_files(db, user.id, status="trashed")
    if not rows:
        st.info("Sampah kosong.")
        return
    st.caption("Berkas di sini masih ada di Google Drive sampai dihapus permanen.")
    frame = pd.DataFrame(
        [{"Nama": f.name, "Ukuran": fmt_bytes(f.size_bytes), "Akun": e, "Dibuang": fmt_dt(f.deleted_at)} for f, e in rows]
    )
    event = st.dataframe(
        frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="tbl_trash"
    )
    ids = [rows[i][0].id for i in event.selection.rows if i < len(rows)]
    if not ids:
        return
    c1, c2 = st.columns(2)
    if c1.button(f"↩️ Pulihkan ({len(ids)})"):
        flash("ok", f"{service.restore_files(db, user.id, ids)} berkas dipulihkan.")
        st.rerun()
    confirm = c2.checkbox("Saya yakin hapus permanen", key="confirm_del")
    if c2.button(f"❌ Hapus permanen ({len(ids)})", disabled=not confirm):
        done, errors = service.delete_forever(db, settings, user.id, ids)
        flash("ok", f"{done} berkas dihapus permanen.")
        for e in errors:
            flash("err", e)
        st.rerun()


def page_settings(settings: Settings, db, user) -> None:
    page_header("Pengaturan", "Kelola akun Google Drive yang terhubung.")
    show_flash()
    accounts = service.list_accounts(db, user.id)

    if "connect_url" not in st.session_state:
        st.session_state["connect_url"] = login_url(settings, "connect", user.id, True)
    st.link_button("Hubungkan akun Google lain", st.session_state["connect_url"], icon=":material/add_link:", type="primary")
    st.caption("Pilih akun lain saat Google bertanya. Untuk menghubungkan ulang akun yang kedaluwarsa, pilih akun yang sama.")
    for acc in accounts:
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 2])
            cols[0].markdown(f'<div class="row-name">{esc(acc.email)}</div>', unsafe_allow_html=True)
            cols[0].caption("Perlu dihubungkan ulang" if acc.status != "connected" else f"{fmt_bytes(acc.used_bytes)} terpakai")
            if cols[2].button("Sinkronkan berkas", key=f"sync_{acc.id}", disabled=acc.status != "connected"):
                try:
                    with st.spinner("Membaca folder PenggabungDrive…"):
                        out = service.sync_account_files(db, settings, acc)
                    flash("ok", f"{acc.email}: {out['dibuat']} baru, {out['diperbarui']} diperbarui, {out['dihapus']} hilang.")
                except AppError as exc:
                    flash("err", str(exc))
                st.rerun()
            if cols[3].button("Lepas akun", key=f"rm_{acc.id}"):
                st.session_state["confirm_rm"] = acc.id
            if st.session_state.get("confirm_rm") == acc.id:
                st.warning("Berkas di Drive tetap aman, tetapi catatan dan tautan berbagi akun ini dihapus dari aplikasi.")
                if st.button("Ya, lepas akun ini", key=f"rm_yes_{acc.id}"):
                    service.remove_account(db, settings, user.id, acc.id)
                    st.session_state.pop("confirm_rm", None)
                    flash("ok", "Akun dilepas.")
                    st.rerun()
            if acc.status != "connected" and acc.last_error:
                st.caption(acc.last_error)
    if not accounts:
        st.info("Belum ada akun.")


# ------------------------------------------------------------------ titik masuk


def main() -> None:
    settings = load_settings()
    problems = settings.missing()
    if problems:
        st.title("🗂️ Penggabung Drive")
        st.error("Pengaturan belum lengkap: " + ", ".join(problems))
        st.markdown("Isi lewat `.streamlit/secrets.toml` atau variabel lingkungan. Lihat README.")
        return

    started = time.perf_counter()
    STATS.reset()
    db = None
    try:
        share_token = st.query_params.get("s")
        callback = "code" in st.query_params or "error" in st.query_params
        if not share_token and not callback and not st.session_state.get("user_id"):
            login_page(settings)  # halaman login tidak menyentuh database, jadi langsung tampil
            return

        db = get_session_factory(settings.database_url)()
        if share_token:
            public_page(settings, db, share_token)
            return
        if callback:
            handle_callback(settings, db)
            st.rerun()

        user = current_user(db)
        if user is None:
            login_page(settings)
            return

        initial = esc((user.name or user.email)[:1].upper())
        with st.sidebar:
            st.markdown(
                f'<div class="brand"><div class="logo">{LOGO_SVG}</div>Penggabung Drive</div>'
                f'<div class="userbox"><div class="avatar">{initial}</div><div>'
                f'<div class="u-name">{esc(user.name or user.email.split("@")[0])}</div>'
                f'<div class="u-mail">{esc(user.email)}</div></div></div>',
                unsafe_allow_html=True,
            )
            page = st.radio("Menu", MENU, label_visibility="collapsed", key="menu")
        {
            MENU[0]: page_home,
            MENU[1]: page_files,
            MENU[2]: page_upload,
            MENU[3]: page_shared,
            MENU[4]: page_trash,
            MENU[5]: page_settings,
        }[page](settings, db, user)

        # Ringkasan sidebar dibuat setelah halaman, supaya daftar akun yang sudah dimuat halaman bisa dipakai ulang.
        loaded = db.info.get("_accounts", {}).get((user.id, False))
        cached = st.session_state.get("_totals")
        if loaded is not None:
            accounts_all = list(loaded)
            totals = service.quota_totals(accounts_all)
        elif cached and time.time() - cached[0] < 60:
            accounts_all, totals = None, cached[1]
        else:
            accounts_all = service.list_accounts(db, user.id)
            totals = service.quota_totals(accounts_all)
        st.session_state["_totals"] = (time.time(), totals)
        with st.sidebar:
            st.markdown(
                f'<div class="side-storage"><div class="row"><b>{esc(fmt_bytes(totals.used))} terpakai</b>'
                f'<span>{esc(fmt_bytes(totals.total))}</span></div>{bar_html(totals.used, totals.total)}</div>',
                unsafe_allow_html=True,
            )
            if st.button("Keluar", icon=":material/logout:", key="logout_btn", width="stretch"):
                logout()
        if accounts_all is not None:  # kuota basi disegarkan di latar belakang, halaman tidak menunggu
            service.refresh_in_background(settings, service.stale_ids(accounts_all))
    except (OperationalError, InterfaceError) as exc:
        show_db_error(settings, exc)
    finally:
        if db is not None:
            db.close()
        if settings.debug:
            queries, seconds = STATS.snapshot()
            elapsed = time.perf_counter() - started
            st.session_state["_stats"] = (queries, seconds, elapsed)
            st.sidebar.caption(
                f"⏱ {elapsed * 1000:.0f} ms total · {queries} kueri ({seconds * 1000:.0f} ms di database)"
            )


main()
