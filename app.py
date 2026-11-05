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

from core import drive, oauthstate, service, yandex
from core import ui_assets as ui
from core.config import Settings, load_settings
from sqlalchemy.exc import InterfaceError, OperationalError

from core.db import STATS, get_session_factory, ping_db
from core.service import AppError, NeedsConsent
from core.utils import file_icon, fmt_bytes, fmt_dt

st.set_page_config(page_title="Penggabung Drive", page_icon="🗂️", layout="wide", initial_sidebar_state="collapsed")

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
.stButton > button[kind="secondary"], .stDownloadButton > button[kind="secondary"] { border:1px solid #e5e7f2; background:#fff; box-shadow:0 1px 2px rgba(30,41,90,.06); }
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
.fold { text-align:center; padding:.5rem .2rem .1rem; }
.fold-ico { display:grid; place-items:center; height:64px; }
.fold-name { font-weight:600; color:#111827; margin-top:.45rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fold-sub { color:#6b7280; font-size:.8rem; margin-bottom:.45rem; }
.fcard { text-align:center; padding:.1rem .1rem .3rem; }
.ftype { width:58px; height:58px; border-radius:16px; display:grid; place-items:center; margin:.2rem auto .65rem; box-shadow:0 6px 14px rgba(30,41,90,.14); }
.fname { font-weight:600; color:#111827; font-size:.92rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fstar { color:#f59e0b; margin-left:.3rem; }
.fsub { color:#6b7280; font-size:.78rem; margin:.15rem 0 .55rem; }
.fchips { display:flex; flex-direction:column; align-items:center; gap:.3rem; }
.fchip { background:#f1f3fb; color:#374151; border-radius:999px; padding:.12rem .65rem; font-size:.78rem; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
@media (max-width:640px) {
  .block-container { padding:1rem .8rem 3rem !important; }
  [data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:.5rem !important; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width:calc(50% - .5rem) !important; flex:1 1 calc(50% - .5rem) !important; }
  .page-title { font-size:1.35rem !important; }
  [data-testid="stHorizontalBlock"]:has(.login-mark) > [data-testid="stColumn"]:not(:has(.login-mark)) { display:none !important; }
  [data-testid="stHorizontalBlock"]:has(.login-mark) > [data-testid="stColumn"] { flex:1 1 100% !important; min-width:100% !important; }
}
@media (max-width:820px) { .stat-grid { grid-template-columns:repeat(2,1fr); } .acc-grid { grid-template-columns:1fr; } }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

def hide_sidebar() -> None:
    """Halaman tanpa menu (login, tautan publik) tidak perlu sidebar sama sekali."""
    st.markdown(
        "<style>[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl'],"
        "[data-testid='collapsedControl'],[data-testid='stSidebarCollapseButton']{display:none !important}</style>",
        unsafe_allow_html=True,
    )


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


MENU = ["Kuota", "Berkas", "Unggah", "Dibagikan", "Galeri", "Sampah", "Pengaturan"]


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
    hide_sidebar()
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
    st.download_button("Unduh berkas", data, file_name=name, mime=mime, type="primary", icon=":material/download:")


# ------------------------------------------------------------------ login & callback


@dataclass
class _YandexCreds:
    """Bentuk sama dengan kredensial Google yang dipakai service.upsert_account."""

    token: str
    refresh_token: str | None
    expiry: object


def handle_yandex_callback(settings: Settings, db, code: str, pending) -> None:
    from core.db import User

    user = db.get(User, pending.user_id or "")
    if user is None:
        flash("err", "Pengguna tidak ditemukan. Masuk dulu.")
        return
    try:
        token, profile = yandex.exchange_code(settings, code)
        creds = _YandexCreds(token["access_token"], token.get("refresh_token"), yandex.expiry_from(token))
        acc = service.upsert_account(db, settings, user, profile, creds, provider="yandex")
        service.refresh_quota(db, settings, acc)
    except NeedsConsent as exc:
        flash("err", str(exc))
        return
    except AppError as exc:
        flash("err", str(exc))
    except Exception as exc:
        flash("err", f"Gagal menghubungkan Yandex: {drive.friendly_error(exc)}")
        return
    flash("ok", f"Akun Yandex {profile.get('email')} terhubung.")


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
    if pending.purpose == "connect_yandex":
        handle_yandex_callback(settings, db, code or "", pending)
        return
    try:
        creds, profile = drive.exchange_code(settings, code or "", pending.code_verifier)
    except drive.MissingScope as exc:
        st.session_state["need_consent"] = True  # login berikutnya memaksa layar izin tampil lagi
        flash("err", str(exc))
        return
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
    hide_sidebar()
    left, mid, right = st.columns([1, 1.6, 1])  # CSS membuatnya satu kolom di hp
    with mid:
        st.markdown('<span class="login-mark"></span>', unsafe_allow_html=True)
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
    if len(connected) >= 2:
        balance_panel(settings, db, user, connected)


def balance_panel(settings: Settings, db, user, connected) -> None:
    with st.expander("Seimbangkan penyimpanan"):
        st.caption(
            "Pindahkan berkas terbesar dari akun yang hampir penuh ke akun yang masih lega. "
            "Hanya berkas yang tercatat di aplikasi yang dipindah. Berkas asal dihapus setelah salinan di tujuan berhasil."
        )
        c = st.columns(2)
        threshold = c[0].slider("Mulai seimbangkan bila terpakai lebih dari (%)", 50, 99, 90, key="bal_hi")
        target = c[1].slider("Turunkan sampai (%)", 30, 95, 80, key="bal_lo")
        if target >= threshold:
            st.warning("Batas tujuan harus lebih rendah dari batas mulai.")
            return
        if st.button("Lihat rencana", key="bal_plan"):
            files = [f for f, _ in service.list_files(db, user.id, recent=True, limit=5000)]
            plan = service.balance_plan(
                connected, files, settings.max_download_mb * 1024 * 1024, threshold / 100, target / 100
            )
            st.session_state["bal_result"] = [(m.file_id, m.name, m.size, m.from_id, m.to_id) for m in plan]
        plan = st.session_state.get("bal_result")
        if plan is None:
            return
        names = {a.id: a.email for a in connected}
        if not plan:
            st.success("Tidak ada yang perlu dipindah dengan batas ini.")
            return
        total = sum(p[2] for p in plan)
        st.write(f"{len(plan)} berkas ({fmt_bytes(total)}) akan dipindah:")
        st.dataframe(
            pd.DataFrame(
                [{"Berkas": p[1], "Ukuran": fmt_bytes(p[2]), "Dari": names.get(p[3], "?"), "Ke": names.get(p[4], "?")} for p in plan]
            ),
            hide_index=True, width="stretch",
        )
        if st.button("Jalankan pemindahan", type="primary", key="bal_run"):
            bar = st.progress(0.0, text="Memindahkan…")
            moved, notes, done = 0, [], 0
            for fid, _, _, _, to_id in plan:
                m, n = service.move_to_account(db, settings, user.id, [fid], to_id)
                moved, notes, done = moved + m, notes + n, done + 1
                bar.progress(done / len(plan), text=f"Memindahkan {done} dari {len(plan)}…")
            bar.empty()
            st.session_state.pop("bal_result", None)
            flash("ok", f"{moved} dari {len(plan)} berkas dipindah.")
            for note in notes[:5]:
                flash("err", note)
            st.rerun()


def folder_picker(db, user, label: str, key: str, exclude: str | None = None) -> str | None:
    options = [o for o in service.folder_options(db, user.id) if o[0] != exclude]
    labels = {fid: text for fid, text in options}
    return st.selectbox(label, [fid for fid, _ in options], format_func=lambda x: labels[x], key=key)


SIZE_COLS = {"XS": 6, "SM": 5, "MD": 4, "LG": 3}
PAGE_STEP = 24
FILE_MODES = ["Folder ini", "Terbaru", "Berbintang"]


def go_to(menu: str) -> None:
    """Dipanggil lewat on_click, jadi boleh mengubah status menu sebelum widget dibuat ulang."""
    st.session_state["menu"] = menu


def clear_selection() -> None:
    st.session_state["sel_round"] = st.session_state.get("sel_round", 0) + 1


def folder_card_html(sub) -> str:
    when = fmt_dt(sub.updated_at or sub.created_at)
    return (
        f'<div class="fold"><div class="fold-ico">{ui.folder_icon_html(sub.icon, sub.color)}</div>'
        f'<div class="fold-name" title="{esc(sub.name)}">{esc(sub.name)}</div>'
        f'<div class="fold-sub">Diperbarui {esc(when)}</div></div>'
    )


def file_card_html(item, email) -> str:
    kind = ui.file_kind(item.mime_type, item.name)
    star = '<span class="fstar">★</span>' if item.starred else ""
    return (
        f'<div class="fcard">{ui.file_icon_html(kind)}'
        f'<div class="fname" title="{esc(item.name)}">{esc(item.name)}{star}</div>'
        f'<div class="fsub">{esc(fmt_dt(item.created_at))}</div>'
        f'<div class="fchips"><span class="fchip">{esc(fmt_bytes(item.size_bytes))}</span>'
        f'<span class="fchip" title="{esc(email)}">{esc(email)}</span></div></div>'
    )


def style_pickers(prefix: str, color: str, icon: str) -> tuple[str, str]:
    colors = list(ui.COLORS)
    icons = list(ui.FOLDER_ICONS)
    c = st.selectbox(
        "Warna", colors, index=colors.index(color) if color in colors else 0,
        format_func=lambda k: ui.COLORS[k][1], key=f"{prefix}_color",
    )
    i = st.selectbox(
        "Ikon", icons, index=icons.index(icon) if icon in icons else 0,
        format_func=lambda k: ui.FOLDER_ICONS[k], key=f"{prefix}_icon",
    )
    return c, i


def move_drive_controls(settings: Settings, db, user, picked) -> None:
    accounts = [a for a in service.list_accounts(db, user.id, connected_only=True)]
    if len(accounts) < 2:
        return
    st.divider()
    labels = {
        a.id: f"{a.email} ({'Yandex' if a.provider == 'yandex' else 'Google'}, "
        f"{fmt_bytes(a.available_bytes) if a.available_bytes is not None else 'tanpa batas'} kosong)"
        for a in accounts
    }
    c = st.columns([4, 2])
    target = c[0].selectbox("Pindahkan ke drive", list(labels), format_func=labels.get, key="mv_drive")
    c[1].write("")
    c[1].write("")
    if c[1].button("Pindah drive", icon=":material/drive_file_move:", width="stretch", key="mv_drive_go"):
        bar = st.progress(0.0, text="Memindahkan…")
        moved, notes = service.move_to_account(
            db, settings, user.id, [f.id for f in picked], target,
            on_progress=lambda i, n: bar.progress(i / max(n, 1), text=f"Memindahkan {min(i + 1, n)} dari {n}…"),
        )
        bar.empty()
        if moved:
            flash("ok", f"{moved} berkas dipindah ke {labels[target].split(' (')[0]}.")
        for note in notes[:5]:
            flash("err", note)
        clear_selection()
        st.rerun()


def selection_bar(settings: Settings, db, user, picked) -> None:
    if not picked:
        return
    ids = [f.id for f in picked]
    with st.container(border=True):
        st.markdown(f"**{len(picked)} dipilih**")
        a = st.columns([3, 1.3, 1.6, 1.5, 1.1])
        with a[0]:
            dest = folder_picker(db, user, "Pindahkan ke", "mv_dest")
        a[1].write("")
        a[1].write("")
        if a[1].button("Pindahkan", width="stretch"):
            flash("ok", f"{service.move_files(db, user.id, ids, dest)} berkas dipindahkan.")
            clear_selection()
            st.rerun()
        all_starred = all(f.starred for f in picked)
        a[2].write("")
        a[2].write("")
        if a[2].button("Hapus bintang" if all_starred else "Beri bintang", icon=":material/star:", width="stretch"):
            service.set_starred(db, user.id, ids, not all_starred)
            clear_selection()
            st.rerun()
        a[3].write("")
        a[3].write("")
        if a[3].button("Buang", icon=":material/delete:", width="stretch"):
            flash("ok", f"{service.trash_files(db, user.id, ids)} berkas dibuang ke sampah.")
            clear_selection()
            st.rerun()
        a[4].write("")
        a[4].write("")
        if a[4].button("Batal", width="stretch"):
            clear_selection()
            st.rerun()
        move_drive_controls(settings, db, user, picked)
        if len(picked) != 1:
            return
        item = picked[0]
        st.divider()
        c1, c2, c3 = st.columns(3)
        with c1:
            new_name = st.text_input("Ganti nama", value=item.name, key=f"ren_{item.id}")
            if st.button("Simpan nama"):
                try:
                    with st.spinner("Mengganti nama di Google Drive…"):
                        service.rename_file(db, settings, user.id, item.id, new_name)
                    flash("ok", "Nama berkas diganti.")
                except AppError as exc:
                    flash("err", str(exc))
                st.rerun()
        with c2:
            st.write("Unduh")
            dl_key = f"dl_{item.id}"
            if dl_key in st.session_state:
                data, name, mime = st.session_state[dl_key]
                st.download_button("Unduh sekarang", data, file_name=name, mime=mime, type="primary", icon=":material/download:")
            elif st.button("Siapkan unduhan"):
                try:
                    with st.spinner("Mengambil dari Google Drive…"):
                        st.session_state[dl_key] = service.download_file(db, settings, item)
                    st.rerun()
                except AppError as exc:
                    st.error(str(exc))
        with c3:
            label = st.selectbox("Masa berlaku tautan", list(service.EXPIRY_CHOICES), key=f"exp_{item.id}")
            if st.button("Buat tautan berbagi", icon=":material/link:"):
                try:
                    share = service.create_share(db, user.id, item.id, service.EXPIRY_CHOICES[label])
                    st.session_state["new_share"] = share.token
                except AppError as exc:
                    st.error(str(exc))
            if st.session_state.get("new_share"):
                st.code(f"{settings.app_url}/?s={st.session_state['new_share']}", language=None)
            for share, shared_file in service.list_shares(db, user.id):
                if shared_file.id != item.id:
                    continue
                st.caption(
                    "Tautan aktif · " + ("tanpa batas waktu" if share.expires_at is None else f"sampai {fmt_dt(share.expires_at)} UTC")
                )
                if st.button("Matikan tautan ini", key=f"off_sel_{share.id}", icon=":material/link_off:"):
                    service.disable_share(db, user.id, share.id)
                    if st.session_state.get("new_share") == share.token:
                        st.session_state.pop("new_share")
                    flash("ok", "Tautan dimatikan. Orang yang punya link tidak bisa membukanya lagi.")
                    st.rerun()


def sync_all(db, settings: Settings, user) -> None:
    created = updated = removed = 0
    with st.spinner("Membaca folder PenggabungDrive di tiap akun…"):
        for acc in service.list_accounts(db, user.id, connected_only=True):
            try:
                out = service.sync_account_files(db, settings, acc)
                created, updated, removed = created + out["dibuat"], updated + out["diperbarui"], removed + out["dihapus"]
            except AppError as exc:
                flash("err", f"{acc.email}: {exc}")
        for src, _ in service.list_import_sources(db, user.id):
            try:
                out = service.sync_import_source(db, settings, user.id, src.id)
                created, updated, removed = created + out["dibuat"], updated + out["diperbarui"], removed + out["dihapus"]
            except AppError as exc:
                flash("err", f"{src.name}: {exc}")
    flash("ok", f"Sinkron selesai: {created} baru, {updated} diperbarui, {removed} hilang.")


def page_files(settings: Settings, db, user) -> None:
    page_header("Berkas", "Semua berkas dari semua akun, dalam satu tempat.")
    show_flash()
    folder_id = st.session_state.get("folder_id")
    crumbs = service.breadcrumb(db, user.id, folder_id)
    if folder_id and not crumbs:
        folder_id = st.session_state["folder_id"] = None

    # ---- bilah alat
    tools = st.columns([4, 2.3, 1.4, 1.8, 1.3])
    search = tools[0].text_input(
        "Cari", key="search", placeholder="Cari berkas di semua akun…", label_visibility="collapsed"
    )
    size = (
        tools[1].segmented_control("Ukuran kartu", list(SIZE_COLS), default="MD", key="files_size", label_visibility="collapsed")
        or "MD"
    )
    tools[2].button("Unggah", icon=":material/upload:", type="primary", width="stretch", on_click=go_to, args=("Unggah",))
    with tools[3].popover("Folder baru", icon=":material/create_new_folder:", width="stretch"):
        nf_name = st.text_input("Nama folder", key="nf_name")
        nf_color, nf_icon = style_pickers("nf", ui.DEFAULT_COLOR, ui.DEFAULT_ICON)
        if st.button("Buat folder", type="primary", key="nf_make"):
            try:
                service.create_folder(db, user.id, folder_id, nf_name, nf_color, nf_icon)
                flash("ok", f"Folder “{nf_name.strip()}” dibuat.")
            except AppError as exc:
                flash("err", str(exc))
            st.rerun()
    if tools[4].button("Sinkron", icon=":material/sync:", width="stretch"):
        sync_all(db, settings, user)
        st.rerun()

    row = st.columns([5, 1.7])
    mode = (
        row[0].segmented_control("Mode", FILE_MODES, default=FILE_MODES[0], key="files_mode", label_visibility="collapsed")
        or FILE_MODES[0]
    )
    view = (
        row[1].segmented_control("Tampilan", ["Kisi", "Daftar"], default="Kisi", key="files_view", label_visibility="collapsed")
        or "Kisi"
    )
    searching = bool(search.strip())
    browsing = mode == FILE_MODES[0] and not searching

    # ---- folder (hanya saat menjelajah)
    if browsing:
        path = st.columns([1] * min(len(crumbs) + 1, 6) + [max(6 - len(crumbs), 1)])
        if path[0].button("Beranda", icon=":material/home:", key="bc_root"):
            st.session_state["folder_id"] = None
            st.rerun()
        for i, crumb in enumerate(crumbs[-5:], start=1):
            if path[i].button(crumb.name, key=f"bc_{crumb.id}"):
                st.session_state["folder_id"] = crumb.id
                st.rerun()

        subs = service.list_subfolders(db, user.id, folder_id)
        for start in range(0, len(subs), 4):
            cols = st.columns(4)
            for col, sub in zip(cols, subs[start:start + 4]):
                with col, st.container(border=True):
                    st.markdown(folder_card_html(sub), unsafe_allow_html=True)
                    if st.button("Buka", key=f"open_{sub.id}", width="stretch"):
                        st.session_state["folder_id"] = sub.id
                        st.rerun()
        if folder_id:
            with st.expander("Ubah folder ini"):
                current = crumbs[-1]
                ren = st.text_input("Nama", value=current.name, key=f"ren_f_{folder_id}")
                e_color, e_icon = style_pickers(f"ef_{folder_id}", current.color, current.icon)
                b = st.columns(2)
                if b[0].button("Simpan perubahan", key=f"ef_save_{folder_id}"):
                    try:
                        service.update_folder(db, user.id, folder_id, ren, e_color, e_icon)
                        flash("ok", "Folder diperbarui.")
                    except AppError as exc:
                        flash("err", str(exc))
                    st.rerun()
                if b[1].button("Hapus folder (isi naik ke induk)", key=f"ef_del_{folder_id}"):
                    st.session_state["folder_id"] = service.delete_folder(db, user.id, folder_id)
                    flash("ok", "Folder dihapus. Isinya dipindah ke folder induk.")
                    st.rerun()

    # ---- berkas
    sig = (folder_id, mode, search.strip())
    if st.session_state.get("_files_sig") != sig:
        st.session_state["_files_sig"] = sig
        st.session_state["files_limit"] = PAGE_STEP
    limit = st.session_state.get("files_limit", PAGE_STEP)
    rows = service.list_files(
        db, user.id, folder_id, search if searching else None, limit=limit + 1,
        starred_only=(mode == "Berbintang" and not searching),
        recent=(mode == "Terbaru" and not searching),
    )
    more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        st.info(
            "Tidak ada berkas yang cocok." if searching
            else "Belum ada berkas berbintang." if mode == "Berbintang"
            else "Belum ada berkas. Tekan Unggah untuk menambahkan." if mode == "Terbaru" or folder_id is None
            else "Folder ini masih kosong."
        )
        return

    rnd = st.session_state.get("sel_round", 0)
    if view == "Kisi":
        picked = [f for f, _ in rows if st.session_state.get(f"sel{rnd}_{f.id}")]
        selection_bar(settings, db, user, picked)
        ncols = SIZE_COLS[size]
        for start in range(0, len(rows), ncols):
            cols = st.columns(ncols)
            for col, (item, email) in zip(cols, rows[start:start + ncols]):
                with col, st.container(border=True):
                    st.checkbox("Pilih", key=f"sel{rnd}_{item.id}", label_visibility="collapsed")
                    st.markdown(file_card_html(item, email), unsafe_allow_html=True)
    else:
        frame = pd.DataFrame(
            [
                {
                    "": "★" if f.starred else "",
                    "Nama": f"{file_icon(f.mime_type)} {f.name}",
                    "Ukuran": fmt_bytes(f.size_bytes),
                    "Akun": email,
                    "Tanggal": fmt_dt(f.created_at),
                }
                for f, email in rows
            ]
        )
        event = st.dataframe(
            frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key=f"tbl{rnd}_{folder_id}_{mode}_{search}",
        )
        picked = [rows[i][0] for i in event.selection.rows if i < len(rows)]
        selection_bar(settings, db, user, picked)

    if more:
        if st.button("Tampilkan lebih banyak"):
            st.session_state["files_limit"] = limit + PAGE_STEP
            st.rerun()
    else:
        st.caption(f"{len(rows)} berkas.")


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


GALLERY_STEP = 12


@st.dialog("Foto", width="large")
def show_photo(settings: Settings, gallery_id: str, file_id: str, name: str, mode: str) -> None:
    from core.db import Gallery, get_session_factory as factory

    db = factory(settings.database_url)()
    try:
        gallery = db.get(Gallery, gallery_id)
        try:
            data = service.gallery_image(db, settings, gallery, file_id, service.VIEW_PX)
        except AppError as exc:
            st.error(str(exc))
            return
    finally:
        db.close()
    st.image(data, width="stretch")
    if mode != "none":
        st.caption("Tekan Unduh di kartu foto untuk mengunduh." if mode == "original" else "")


@st.dialog("Unduh foto")
def download_photo(settings: Settings, gallery_id: str, file_id: str) -> None:
    from core.db import Gallery, get_session_factory as factory

    db = factory(settings.database_url)()
    try:
        gallery = db.get(Gallery, gallery_id)
        mode = service.gallery_mode(gallery)
        with st.spinner("Menyiapkan berkas…"):
            try:
                data, name, mime = service.gallery_download(db, settings, gallery, file_id)
            except AppError as exc:
                st.error(str(exc))
                return
    finally:
        db.close()
    st.caption("Berkas asli." if mode == "original" else "Ukuran diperkecil (JPEG, maks. 1600 px).")
    st.download_button("Simpan ke perangkat", data, file_name=name, mime=mime, type="primary", icon=":material/download:")


def public_gallery(settings: Settings, db, token: str) -> None:
    hide_sidebar()
    found = service.resolve_gallery(db, token)
    if not found:
        st.title("🖼️ Galeri")
        st.error("Galeri ini tidak berlaku. Mungkin sudah kedaluwarsa atau dimatikan pemiliknya.")
        return
    gallery, files = found
    st.title(f"🖼️ {gallery.title}")
    st.caption(f"{len(files)} foto")
    limit = st.session_state.get(f"glim_{token}", GALLERY_STEP)
    shown = files[:limit]
    with st.spinner("Memuat foto…"):
        thumbs = service.gallery_thumbs(settings, gallery.id, [f.id for f in shown])
    ncols = 3
    for start in range(0, len(shown), ncols):
        cols = st.columns(ncols)
        for col, item in zip(cols, shown[start:start + ncols]):
            with col:
                data = thumbs.get(item.id)
                if data is None:
                    st.caption("Foto tidak bisa dimuat.")
                    continue
                st.image(data, width="stretch")
                mode = service.gallery_mode(gallery)
                if st.button("Perbesar", key=f"zoom_{item.id}", width="stretch"):
                    show_photo(settings, gallery.id, item.id, item.name, mode)
                if mode != "none" and st.button("Unduh", key=f"dl_{item.id}", width="stretch", icon=":material/download:"):
                    download_photo(settings, gallery.id, item.id)
    if len(files) > limit:
        if st.button("Tampilkan lebih banyak"):
            st.session_state[f"glim_{token}"] = limit + GALLERY_STEP
            st.rerun()


def page_gallery(settings: Settings, db, user) -> None:
    page_header("Galeri", "Bagikan kumpulan foto tanpa memperlihatkan alamat penyimpanan aslinya.")
    show_flash()
    rows = [(f, e) for f, e in service.list_files(db, user.id, recent=True, limit=300) if service.is_image(f)]
    with st.container(border=True):
        st.markdown("**Buat galeri baru**")
        if not rows:
            st.info("Belum ada foto. Unggah gambar lewat menu Unggah, lalu kembali ke sini.")
        else:
            title = st.text_input("Judul galeri", key="g_title")
            by_id = {f.id: f for f, _ in rows}
            picked = st.multiselect(
                f"Pilih foto (maksimal {service.MAX_GALLERY_ITEMS})", list(by_id), format_func=lambda i: by_id[i].name, key="g_files"
            )
            c1, c2 = st.columns(2)
            label = c1.selectbox("Masa berlaku", list(service.EXPIRY_CHOICES), key="g_exp")
            dl_labels = {"none": "Tanpa unduhan", "small": "Unduh ukuran kecil (JPEG)", "original": "Unduh berkas asli"}
            mode = c2.selectbox("Unduhan untuk pengunjung", list(dl_labels), format_func=dl_labels.get, key="g_dl")
            if st.button("Buat galeri", type="primary", key="g_make"):
                try:
                    gallery = service.create_gallery(db, user.id, title, picked, service.EXPIRY_CHOICES[label], mode)
                    flash("ok", f"Galeri “{gallery.title}” dibuat.")
                except AppError as exc:
                    flash("err", str(exc))
                st.rerun()
    for gallery in service.list_galleries(db, user.id):
        with st.container(border=True):
            cols = st.columns([5, 2, 1])
            cols[0].markdown(
                f"**{esc(gallery.title)}** · {len(gallery.items)} foto · unduhan: "
                + {"none": "mati", "small": "ukuran kecil", "original": "berkas asli"}[service.gallery_mode(gallery)]
            )
            cols[0].code(f"{settings.app_url}/?g={gallery.token}", language=None)
            cols[1].caption(
                "Tanpa batas waktu" if gallery.expires_at is None else f"Berlaku sampai {fmt_dt(gallery.expires_at)} UTC"
            )
            if cols[2].button("Hapus", key=f"gdel_{gallery.id}"):
                service.delete_gallery(db, user.id, gallery.id)
                flash("ok", "Galeri dihapus.")
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


def import_panel(settings: Settings, db, user, acc) -> None:
    """Pilih folder Drive lama untuk dicatat di dashboard (berkas tidak dipindah)."""
    stack_key = f"imp_{acc.id}"
    stack = st.session_state.setdefault(stack_key, [("root", "Drive Saya")])
    here_id, here_name = stack[-1]
    st.caption("Pilih folder di Google Drive-mu. Isinya (termasuk sub-folder) dicatat di Berkas, tanpa dipindah atau diubah.")
    st.markdown("**Lokasi:** " + " › ".join(esc(n) for _, n in stack), unsafe_allow_html=True)
    top = st.columns([1, 1.6, 3])
    if len(stack) > 1 and top[0].button("Naik", key=f"imp_up_{acc.id}", icon=":material/arrow_upward:"):
        stack.pop()
        st.rerun()
    if here_id != "root" and top[1].button("Impor folder ini", key=f"imp_add_{acc.id}", type="primary"):
        try:
            src = service.add_import_source(db, user.id, acc.id, here_id, here_name)
            with st.spinner("Membaca isi folder…"):
                out = service.sync_import_source(db, settings, user.id, src.id)
            note = " (daftar dipotong di 5.000 berkas)" if out["terpotong"] else ""
            flash("ok", f"“{here_name}”: {out['dibuat']} berkas dicatat{note}.")
        except AppError as exc:
            flash("err", str(exc))
        st.rerun()
    try:
        folders = service.browse_drive_folders(db, settings, user.id, acc.id, here_id)
    except AppError as exc:
        st.error(str(exc))
        folders = []
    if not folders:
        st.caption("Tidak ada sub-folder di sini.")
    for fid, name in folders[:60]:
        if st.button(name, key=f"imp_open_{acc.id}_{fid}", icon=":material/folder:", width="stretch"):
            stack.append((fid, name))
            st.rerun()
    if len(folders) > 60:
        st.caption(f"Menampilkan 60 dari {len(folders)} folder.")
    mine = [(src, e) for src, e in service.list_import_sources(db, user.id) if src.account_id == acc.id]
    if mine:
        st.divider()
        st.markdown("**Folder yang sudah diimpor**")
    for src, _ in mine:
        c = st.columns([4, 1.4, 1.2])
        c[0].markdown(f"📁 {esc(src.name)}")
        c[0].caption("Belum disinkronkan" if not src.last_synced_at else f"Sinkron terakhir {fmt_dt(src.last_synced_at)} UTC")
        if c[1].button("Sinkronkan", key=f"imp_sync_{src.id}"):
            try:
                with st.spinner("Membaca isi folder…"):
                    out = service.sync_import_source(db, settings, user.id, src.id)
                flash("ok", f"{src.name}: {out['dibuat']} baru, {out['diperbarui']} diperbarui, {out['dihapus']} hilang.")
            except AppError as exc:
                flash("err", str(exc))
            st.rerun()
        if c[2].button("Lepas", key=f"imp_rm_{src.id}"):
            n = service.remove_import_source(db, user.id, src.id)
            flash("ok", f"Folder dilepas. {n} catatan berkas dihapus dari aplikasi; berkas di Drive aman.")
            st.rerun()


def page_settings(settings: Settings, db, user) -> None:
    page_header("Pengaturan", "Kelola akun penyimpanan yang terhubung.")
    show_flash()
    accounts = service.list_accounts(db, user.id)

    if "connect_url" not in st.session_state:
        st.session_state["connect_url"] = login_url(settings, "connect", user.id, True)
    st.link_button("Hubungkan akun Google lain", st.session_state["connect_url"], icon=":material/add_link:", type="primary")
    st.caption("Pilih akun lain saat Google bertanya. Untuk menghubungkan ulang akun yang kedaluwarsa, pilih akun yang sama.")
    if settings.yandex_enabled:
        if "connect_yandex_url" not in st.session_state:
            state, _ = oauthstate.make_state(settings.encryption_key, "connect_yandex", user.id)
            st.session_state["connect_yandex_url"] = yandex.authorization_url(settings, state)
        st.link_button("Hubungkan Yandex Disk", st.session_state["connect_yandex_url"], icon=":material/add_link:")
        st.caption("Yandex hanya mengizinkan aplikasi ini memakai folder khususnya di Yandex Disk.")
    else:
        st.caption("Yandex Disk belum aktif. Isi YANDEX_CLIENT_ID dan YANDEX_CLIENT_SECRET di Secrets untuk menyalakannya.")
    for acc in accounts:
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 2])
            cols[0].markdown(f'<div class="row-name">{esc(acc.email)}</div>', unsafe_allow_html=True)
            cols[0].caption(
                ("Yandex Disk · " if acc.provider == "yandex" else "Google Drive · ")
                + ("Perlu dihubungkan ulang" if acc.status != "connected" else f"{fmt_bytes(acc.used_bytes)} terpakai")
            )
            if cols[2].button("Sinkronkan berkas", key=f"sync_{acc.id}", disabled=acc.status != "connected"):
                try:
                    with st.spinner("Membaca folder aplikasi…"):
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
            if acc.provider == "google" and acc.status == "connected":
                with st.expander("Impor dari Drive"):
                    import_panel(settings, db, user, acc)
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
        gallery_token = st.query_params.get("g")
        callback = "code" in st.query_params or "error" in st.query_params
        if not share_token and not gallery_token and not callback and not st.session_state.get("user_id"):
            login_page(settings)  # halaman login tidak menyentuh database, jadi langsung tampil
            return

        db = get_session_factory(settings.database_url)()
        if share_token:
            public_page(settings, db, share_token)
            return
        if gallery_token:
            public_gallery(settings, db, gallery_token)
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
            "Kuota": page_home,
            "Berkas": page_files,
            "Unggah": page_upload,
            "Dibagikan": page_shared,
            "Galeri": page_gallery,
            "Sampah": page_trash,
            "Pengaturan": page_settings,
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
            note = f"⏱ {elapsed * 1000:.0f} ms total · {queries} kueri ({seconds * 1000:.0f} ms di database)"
            (st.sidebar if st.session_state.get("user_id") else st).caption(note)


main()
