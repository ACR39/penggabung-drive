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
:root { --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --canvas:#f6f7f9; --accent:#2447c9; --accent-soft:#e8edfb; }
.stApp { background:var(--canvas); }
header[data-testid="stHeader"] { background:transparent; }
.stAppDeployButton, footer { display:none !important; }
.block-container { padding-top:2rem; max-width:1180px; }
h1, h2, h3 { letter-spacing:-.01em; }

/* sidebar */
[data-testid="stSidebar"] { background:#fff; border-right:1px solid var(--line); }
[data-testid="stSidebar"] [role="radiogroup"] { gap:.15rem; }
[data-testid="stSidebar"] [role="radiogroup"] label { padding:.5rem .8rem; border-radius:8px; width:100%; cursor:pointer; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background:#f1f5f9; }
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child { display:none; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background:var(--accent-soft); }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p { color:var(--accent); font-weight:600; }
.brand { display:flex; align-items:center; gap:.65rem; font-weight:650; font-size:1.1rem; color:var(--ink); margin:.2rem 0 1.1rem; }
.logo { width:32px; height:32px; border-radius:8px; background:var(--accent); display:grid; place-items:center; }
.userbox { display:flex; align-items:center; gap:.65rem; padding:.5rem 0 1rem; border-bottom:1px solid var(--line); margin-bottom:.9rem; }
.avatar { width:34px; height:34px; border-radius:50%; background:var(--accent-soft); color:var(--accent); display:grid; place-items:center; font-weight:650; }
.u-name { font-weight:600; color:var(--ink); font-size:.93rem; line-height:1.2; }
.u-mail { color:var(--muted); font-size:.8rem; word-break:break-all; }
.side-storage { margin-top:1.2rem; padding-top:1rem; border-top:1px solid var(--line); }
.side-storage .row { display:flex; justify-content:space-between; font-size:.84rem; color:#334155; margin-bottom:.45rem; }

/* kartu, tombol, kolom isian: datar, garis tipis, sudut kecil */
[class*="st-key-card_"] { border-radius:10px !important; border:1px solid var(--line) !important; background:#fff !important; box-shadow:none; }
.stButton > button, .stDownloadButton > button, [data-testid="stBaseLinkButton-secondary"], [data-testid="stBaseLinkButton-primary"] { border-radius:8px; font-weight:500; box-shadow:none; }
.stButton > button[kind="secondary"], .stDownloadButton > button[kind="secondary"] { border:1px solid var(--line); background:#fff; }
.stButton > button[kind="secondary"]:hover { border-color:#cbd5e1; background:#f8fafc; color:var(--ink); }
.stButton > button[kind="tertiary"] { color:var(--accent); padding-left:.2rem; padding-right:.4rem; }
.stButton > button[kind="tertiary"]:hover { background:var(--accent-soft); color:var(--accent); }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="base-input"] { border-radius:8px !important; }
[data-testid="stButtonGroup"] button { border-radius:8px; }

/* judul */
.page-title { font-size:1.55rem; font-weight:650; color:var(--ink); line-height:1.2; letter-spacing:-.01em; }
.page-sub { color:var(--muted); margin:.3rem 0 1.2rem; font-size:.95rem; }

/* kapasitas gabungan (halaman Kuota) */
.cap { background:#fff; border:1px solid var(--line); border-radius:10px; padding:1.25rem 1.4rem 1.1rem; margin:.4rem 0 1.2rem; }
.cap-head { display:flex; justify-content:space-between; align-items:baseline; gap:1rem; flex-wrap:wrap; margin-bottom:.9rem; }
.cap-big { font-size:1.75rem; font-weight:650; color:var(--ink); letter-spacing:-.02em; }
.cap-of { color:var(--muted); margin-left:.45rem; font-size:.95rem; }
.cap-free { color:#334155; font-size:.95rem; }
.cap-bar { display:flex; gap:2px; height:12px; border-radius:6px; overflow:hidden; background:#eaeef3; }
.cap-bar > span { display:block; height:100%; }
.cap-legend { display:flex; flex-wrap:wrap; gap:.5rem 1.4rem; margin-top:.95rem; font-size:.86rem; color:#334155; }
.cap-legend .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:.45rem; }
.cap-legend small { color:var(--muted); margin-left:.35rem; }

/* kartu akun */
.acc-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:.9rem; margin-top:.6rem; }
.acc { background:#fff; border-radius:10px; padding:1rem 1.15rem; border:1px solid var(--line); }
.acc-top { display:flex; align-items:center; gap:.75rem; margin-bottom:.85rem; }
.acc-ico { width:34px; height:34px; border-radius:8px; display:grid; place-items:center; font-size:.9rem; font-weight:650; flex:none; }
.acc-name { font-weight:600; color:var(--ink); word-break:break-all; }
.acc-sub { color:var(--muted); font-size:.84rem; }
.acc-top .chip { margin-left:auto; flex:none; }
.bar { height:6px; border-radius:3px; background:#eaeef3; overflow:hidden; margin:.2rem 0 .55rem; }
.bar > span { display:block; height:100%; border-radius:3px; background:var(--accent); }
.bar.warn > span { background:#c2410c; }
.chip { display:inline-block; padding:.12rem .55rem; border-radius:6px; font-size:.76rem; font-weight:500; background:#e7f5ec; color:#166534; }
.chip.warn { background:#fdeaea; color:#991b1b; }
.row-name { font-weight:600; color:var(--ink); word-break:break-all; }
.login-card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:2rem; margin-top:3rem; }

/* folder dan berkas */
.fold { display:flex; align-items:center; gap:.8rem; padding:.2rem .1rem .1rem; }
.fold svg { flex:none; }
.fold-name { font-weight:600; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fold-sub { color:var(--muted); font-size:.8rem; }
.fold > div { min-width:0; }
.fcard { padding:.15rem .1rem .2rem; }
.ftype { width:40px; height:40px; border-radius:10px; display:grid; place-items:center; margin:.1rem 0 .8rem; }
.fname { font-weight:600; color:var(--ink); font-size:.92rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fstar { color:#d97706; margin-left:.3rem; }
.fsub { color:var(--muted); font-size:.8rem; margin:.15rem 0 .6rem; }
.fchip { display:inline-block; background:#f1f5f9; color:#334155; border-radius:6px; padding:.1rem .5rem; font-size:.76rem; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:top; }
[class*="st-key-card_"]:has(.fcard) { position:relative; }
[class*="st-key-card_"]:has(.fcard) [data-testid="stElementContainer"]:has([data-testid="stCheckbox"]) { position:absolute; top:.7rem; right:.7rem; width:auto; height:auto; z-index:3; }
/* aktivitas dan rincian penyimpanan */
.act-list { background:#fff; border:1px solid var(--line); border-radius:10px; }
.act { display:flex; align-items:center; gap:.9rem; padding:.7rem 1.1rem; border-top:1px solid #eef1f5; }
.act:first-child { border-top:0; }
.act-tag { flex:none; min-width:8.5rem; text-align:center; padding:.15rem .6rem; border-radius:6px; font-size:.78rem; font-weight:600; }
.act-detail { flex:1; color:var(--ink); font-size:.92rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.act-time { flex:none; color:var(--muted); font-size:.8rem; }
.usage { margin-top:1.1rem; padding-top:1rem; border-top:1px solid var(--line); }
.usage .u-row { display:flex; justify-content:space-between; align-items:center; font-size:.86rem; color:#334155; margin-bottom:.4rem; }
.usage .u-row .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:.55rem; }
.usage .u-row b { font-weight:600; color:var(--ink); }
.usage .u-stack { display:flex; gap:2px; height:8px; border-radius:4px; overflow:hidden; background:#eaeef3; margin:.7rem 0 .55rem; }
.usage .u-stack > span { display:block; height:100%; }
.usage .u-total { display:flex; justify-content:space-between; font-size:.86rem; color:#334155; }
@media (max-width:640px) {
  .act { flex-wrap:wrap; gap:.3rem .7rem; }
  .act-detail { flex-basis:100%; order:3; white-space:normal; }
  .act-tag { min-width:0; }
  .block-container { padding:3.2rem .8rem 3rem !important; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has([data-testid="stButtonGroup"]) { flex:1 1 100% !important; min-width:100% !important; width:100% !important; }
  [data-testid="stButtonGroup"] { width:100%; }
  [data-testid="stButtonGroup"] button { flex:1 1 0; padding-left:.4rem; padding-right:.4rem; }
  [data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:.5rem !important; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width:calc(50% - .5rem) !important; flex:1 1 calc(50% - .5rem) !important; }
  .page-title { font-size:1.35rem !important; }
  [data-testid="stHorizontalBlock"]:has(.login-mark) > [data-testid="stColumn"]:not(:has(.login-mark)) { display:none !important; }
  [data-testid="stHorizontalBlock"]:has(.login-mark) > [data-testid="stColumn"] { flex:1 1 100% !important; min-width:100% !important; }
}
@media (max-width:820px) { .acc-grid { grid-template-columns:1fr; } }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

_card_n = [0]


def card():
    """Kontainer berbingkai dengan kunci unik per proses jalan, supaya CSS bisa menyasarnya (class st-key-card_N)."""
    _card_n[0] += 1
    return st.container(border=True, key=f"card_{_card_n[0]}")


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


ACC_COLORS = ["#2447c9", "#0e9f8e", "#d9822b", "#8b5cf6", "#c2417c", "#64748b"]


def acc_colors(accounts) -> dict:
    """Satu warna tetap per akun (urut pembuatan), dipakai di bilah kapasitas dan kartu akun."""
    ordered = sorted(accounts, key=lambda a: a.created_at or 0)
    return {a.id: ACC_COLORS[i % len(ACC_COLORS)] for i, a in enumerate(ordered)}


def provider_name(acc) -> str:
    return "Yandex Disk" if acc.provider == "yandex" else "Google Drive"


def capacity_panel(accounts, totals) -> None:
    """Satu bilah untuk seluruh ruang: tiap akun mendapat potongan sebesar pemakaiannya."""
    colors = acc_colors(accounts)
    live = [a for a in accounts if a.status == "connected" and a.total_bytes]
    total = totals.total or 0
    segments = "".join(
        f'<span style="width:{(a.used_bytes or 0) / total * 100:.2f}%;background:{colors[a.id]}" '
        f'title="{esc(a.email)}: {esc(fmt_bytes(a.used_bytes))}"></span>'
        for a in live
        if total and (a.used_bytes or 0) > 0
    )
    legend = "".join(
        f'<span><span class="dot" style="background:{colors[a.id]}"></span>{esc(a.email)}'
        f"<small>{esc(fmt_bytes(a.used_bytes))}</small></span>"
        for a in live
    )
    st.markdown(
        f'<div class="cap"><div class="cap-head"><div><span class="cap-big">{esc(fmt_bytes(totals.used))}</span>'
        f'<span class="cap-of">terpakai dari {esc(fmt_bytes(totals.total))}</span></div>'
        f'<div class="cap-free">{esc(fmt_bytes(totals.free))} tersedia</div></div>'
        f'<div class="cap-bar">{segments}</div><div class="cap-legend">{legend}</div></div>',
        unsafe_allow_html=True,
    )


def bar_html(used: int, total: int | None, color: str | None = None) -> str:
    pct = 0 if not total else min(used / total * 100, 100)
    warn = " warn" if pct >= 90 else ""
    fill = f";background:{color}" if color and not warn else ""
    return f'<div class="bar{warn}"><span style="width:{pct:.1f}%{fill}"></span></div>'


USAGE_COLORS = {"foto": "#0e9f8e", "video": "#d9822b", "dokumen": "#2447c9", "lainnya": "#8b5cf6", "kosong": "#cbd5e1"}


def usage_html(usage: dict, totals) -> str:
    """Rincian ruang per jenis berkas. 'Lainnya' juga memuat pemakaian di luar berkas yang tercatat aplikasi."""
    tracked = sum(usage.values())
    parts = {k: usage.get(k, 0) for k in ("foto", "video", "dokumen")}
    parts["lainnya"] = usage.get("lainnya", 0) + max((totals.used or 0) - tracked, 0)
    names = {"foto": "Foto", "video": "Video", "dokumen": "Dokumen", "lainnya": "Lainnya", "kosong": "Kosong"}
    parts["kosong"] = totals.free or 0
    rows = "".join(
        f'<div class="u-row"><span><span class="dot" style="background:{USAGE_COLORS[k]}"></span>{names[k]}</span>'
        f"<b>{esc(fmt_bytes(v))}</b></div>"
        for k, v in parts.items()
    )
    total = totals.total or 0
    stack = "".join(
        f'<span style="width:{v / total * 100:.2f}%;background:{USAGE_COLORS[k]}"></span>'
        for k, v in parts.items()
        if total and v > 0 and k != "kosong"
    )
    return (
        f'<div class="usage">{rows}<div class="u-stack">{stack}</div>'
        f'<div class="u-total"><b>{esc(fmt_bytes(totals.used))} terpakai</b><span>{esc(fmt_bytes(totals.total))}</span></div></div>'
    )


def account_card_html(acc, color: str = "#2447c9") -> str:
    if acc.status == "connected":
        chip = '<span class="chip">Terhubung</span>'
        body = (
            bar_html(acc.used_bytes or 0, acc.total_bytes, color)
            + f'<div class="acc-sub">{esc(fmt_bytes(acc.used_bytes))} terpakai · '
            f'{esc(fmt_bytes(acc.available_bytes))} tersedia</div>'
        )
    else:
        chip = '<span class="chip warn">Hubungkan ulang</span>'
        body = f'<div class="acc-sub">{esc(acc.last_error or "Izin akun perlu diperbarui.")}</div>'
    letter = "Y" if acc.provider == "yandex" else "G"
    return (
        f'<div class="acc"><div class="acc-top"><div class="acc-ico" style="background:{color}1f;color:{color}">{letter}</div><div>'
        f'<div class="acc-name">{esc(acc.email)}</div><div class="acc-sub">{provider_name(acc)}</div></div>{chip}</div>{body}</div>'
    )


MENU = ["Kuota", "Berkas", "Dibagikan", "Galeri", "Sampah", "Aktivitas", "Pengaturan"]


# ------------------------------------------------------------------ pembantu


def flash(kind: str, text: str) -> None:
    """Simpan pesan agar tetap tampil setelah st.rerun()."""
    st.session_state.pop("_usage", None)
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
        with card():
            st.markdown(
                f'<div class="brand"><div class="logo">{LOGO_SVG}</div>Penggabung Drive</div>'
                '<div class="page-title" style="font-size:1.6rem">Satu penyimpanan, banyak akun</div>'
                '<div class="page-sub">Gabungkan beberapa akun penyimpanan. Unggahan otomatis masuk ke akun yang masih punya ruang.</div>',
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
    capacity_panel(accounts, totals)
    colors = acc_colors(accounts)
    if totals.unlimited_accounts:
        st.caption(f"{totals.unlimited_accounts} akun berkuota tak terbatas tidak dihitung ke total.")

    connected = [a for a in accounts if a.status == "connected"]
    if connected:
        policy = service.get_policy(db, user.id)
        order = ordered_accounts(connected, policy.priority_ids)
        with card():
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
                    f"{provider_name(acc)} · {fmt_bytes(acc.used_bytes)} terpakai · {fmt_bytes(acc.available_bytes)} tersedia"
                )
                for col, label, icon, delta in (
                    (row[1], "Naik", ":material/arrow_upward:", -1),
                    (row[2], "Turun", ":material/arrow_downward:", 1),
                ):
                    j = i + delta
                    if col.button(icon, key=f"{label}_{acc.id}", help=label, disabled=not 0 <= j < len(order), width="stretch"):
                        ids = [a.id for a in order]
                        ids[i], ids[j] = ids[j], ids[i]
                        service.set_policy(db, user.id, policy.mode, ids)
                        st.rerun()
            st.caption("Urutan ini dipakai oleh mode Bergantian dan Urutan prioritas.")

    st.markdown(
        '<div class="acc-grid">' + "".join(account_card_html(a, colors[a.id]) for a in accounts) + "</div>",
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


def folder_picker(db, user, label: str, key: str, exclude: str | None = None, default: str | None = None) -> str | None:
    options = [o for o in service.folder_options(db, user.id) if exclude is None or o[0] != exclude]
    labels = {fid: text for fid, text in options}
    ids = [fid for fid, _ in options]
    index = ids.index(default) if default in ids else 0
    return st.selectbox(label, ids, index=index, format_func=lambda x: labels[x], key=key)


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
        f'<div class="fold">{ui.folder_icon_html(sub.icon, sub.color, 36)}<div>'
        f'<div class="fold-name" title="{esc(sub.name)}">{esc(sub.name)}</div>'
        f'<div class="fold-sub">Diperbarui {esc(when)}</div></div></div>'
    )


def file_card_html(item, email) -> str:
    kind = ui.file_kind(item.mime_type, item.name)
    star = '<span class="fstar">★</span>' if item.starred else ""
    return (
        f'<div class="fcard">{ui.file_icon_html(kind, 20)}'
        f'<div class="fname" title="{esc(item.name)}">{esc(item.name)}{star}</div>'
        f'<div class="fsub">{esc(fmt_bytes(item.size_bytes))} · {esc(fmt_dt(item.created_at))}</div>'
        f'<span class="fchip" title="{esc(email)}">{esc(email)}</span></div>'
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
    with card():
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
            st.text_input(
                "Nama tautan (opsional)", key=f"slug_{item.id}", placeholder="mis. laporan-2026",
                help="Huruf kecil, angka, dan tanda hubung. Kosongkan untuk kode acak. Nama kustom lebih mudah ditebak orang.",
            )
            if st.button("Buat tautan berbagi", icon=":material/link:"):
                try:
                    share = service.create_share(db, user.id, item.id, service.EXPIRY_CHOICES[label], st.session_state.get(f"slug_{item.id}"))
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
    tools = st.columns([5, 1.4, 1.9, 1.4])
    search = tools[0].text_input(
        "Cari", key="search", placeholder="Cari berkas di semua akun…", label_visibility="collapsed"
    )
    if tools[1].button("Unggah", icon=":material/upload:", type="primary", width="stretch"):
        upload_dialog(settings, user, folder_id)
    with tools[2].popover("Folder baru", icon=":material/create_new_folder:", width="stretch"):
        nf_name = st.text_input("Nama folder", key="nf_name")
        nf_color, nf_icon = style_pickers("nf", ui.DEFAULT_COLOR, ui.DEFAULT_ICON)
        if st.button("Buat folder", type="primary", key="nf_make"):
            try:
                service.create_folder(db, user.id, folder_id, nf_name, nf_color, nf_icon)
                flash("ok", f"Folder “{nf_name.strip()}” dibuat.")
            except AppError as exc:
                flash("err", str(exc))
            st.rerun()
    if tools[3].button("Sinkron", icon=":material/sync:", width="stretch"):
        sync_all(db, settings, user)
        st.rerun()

    row = st.columns([4, 2.4, 1.9])
    mode = (
        row[0].segmented_control("Mode", FILE_MODES, default=FILE_MODES[0], key="files_mode", label_visibility="collapsed")
        or FILE_MODES[0]
    )
    size = (
        row[1].segmented_control("Ukuran kartu", list(SIZE_COLS), default="MD", key="files_size", label_visibility="collapsed")
        or "MD"
    )
    view = (
        row[2].segmented_control("Tampilan", ["Kisi", "Daftar"], default="Kisi", key="files_view", label_visibility="collapsed")
        or "Kisi"
    )
    searching = bool(search.strip())
    browsing = mode == FILE_MODES[0] and not searching

    # ---- folder (hanya saat menjelajah)
    if browsing:
        path = st.columns([1.1] + [1.4] * min(len(crumbs), 5) + [max(6 - len(crumbs), 1) * 1.4])
        if path[0].button("Beranda", icon=":material/home:", key="bc_root", type="tertiary"):
            st.session_state["folder_id"] = None
            st.rerun()
        for i, crumb in enumerate(crumbs[-5:], start=1):
            if path[i].button(crumb.name, icon=":material/chevron_right:", key=f"bc_{crumb.id}", type="tertiary"):
                st.session_state["folder_id"] = crumb.id
                st.rerun()

        subs = service.list_subfolders(db, user.id, folder_id)
        for start in range(0, len(subs), 4):
            cols = st.columns(4)
            for col, sub in zip(cols, subs[start:start + 4]):
                with col, card():
                    st.markdown(folder_card_html(sub), unsafe_allow_html=True)
                    if st.button("Buka", key=f"open_{sub.id}", type="tertiary", icon=":material/arrow_forward:"):
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
                with col, card():
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


@st.dialog("Unggah berkas", width="large")
def upload_dialog(settings: Settings, user, folder_id: str | None) -> None:
    """Unggah dari halaman Berkas. Memakai sesi database sendiri karena dialog dijalankan ulang terpisah."""
    db = get_session_factory(settings.database_url)()
    try:
        accounts = service.list_accounts(db, user.id, connected_only=True)
        if not accounts:
            st.info("Hubungkan minimal satu akun di Pengaturan.")
            return
        policy = service.get_policy(db, user.id)
        mode_names = {"most_available": "ruang terbanyak", "round_robin": "bergantian", "priority": "urutan prioritas"}
        st.caption(f"Akun dipilih otomatis dengan aturan **{mode_names.get(policy.mode, policy.mode)}** (ubah di Kuota).")
        c = st.columns(2)
        with c[0]:
            dest = folder_picker(db, user, "Simpan ke folder", "up_dest", default=folder_id)
        emails = {a.id: a.email for a in accounts}
        target = c[1].selectbox(
            "Akun tujuan",
            [None] + [a.id for a in accounts],
            format_func=lambda x: "Otomatis (ikuti aturan)" if x is None else emails[x],
            key="up_target",
        )
        files = st.file_uploader("Pilih berkas", accept_multiple_files=True, key=f"up_files_{st.session_state.get('up_round', 0)}")
        if not files:
            return
        st.write(f"{len(files)} berkas · {fmt_bytes(sum(f.size for f in files))}")
        if st.button("Mulai unggah", type="primary", key="up_go"):
            bar = st.progress(0.0, text="Menyiapkan…")

            def on_progress(index: int, total: int, fraction: float) -> None:
                bar.progress((index + fraction) / total, text=f"Berkas {index + 1} dari {total}")

            items = [(f.name, f.type or "application/octet-stream", f.size, f) for f in files]
            results = service.upload_batch(db, settings, user.id, items, dest, target, on_progress)
            bar.empty()
            for r in results:
                flash("ok" if r.ok else "err", f"{r.name} → {r.account_email}" if r.ok else f"{r.name}: {r.message}")
            st.session_state["up_round"] = st.session_state.get("up_round", 0) + 1
            st.rerun()
    finally:
        db.close()


ACTION_STYLE = {
    "Berkas": ("#2447c9", "#e8edfb"),
    "Berbagi": ("#0e9f8e", "#e2f5f2"),
    "Akun": ("#8b5cf6", "#efe9fe"),
}
ACTIVITY_STEP = 30


def page_activity(settings: Settings, db, user) -> None:
    page_header("Aktivitas", "Jejak unggahan, perpindahan, tautan, dan akun selama 90 hari terakhir.")
    show_flash()
    group = st.segmented_control("Kelompok", list(service.ACTIVITY_GROUPS), default="Semua", key="act_group", label_visibility="collapsed") or "Semua"
    limit = st.session_state.get("act_limit", ACTIVITY_STEP)
    if st.session_state.get("_act_sig") != group:
        st.session_state["_act_sig"] = group
        limit = st.session_state["act_limit"] = ACTIVITY_STEP
    rows = service.list_activity(db, user.id, group, limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        st.info("Belum ada aktivitas. Unggah berkas atau hubungkan akun, nanti riwayatnya muncul di sini.")
        return
    html_rows = []
    for act in rows:
        label, grp = service.ACTIONS.get(act.action, (act.action, "Berkas"))
        fg, bg = ACTION_STYLE.get(grp, ACTION_STYLE["Berkas"])
        html_rows.append(
            f'<div class="act"><span class="act-tag" style="color:{fg};background:{bg}">{esc(label)}</span>'
            f'<span class="act-detail">{esc(act.detail)}</span><span class="act-time">{esc(fmt_dt(act.created_at))} UTC</span></div>'
        )
    st.markdown('<div class="act-list">' + "".join(html_rows) + "</div>", unsafe_allow_html=True)
    if more and st.button("Tampilkan lebih banyak", key="act_more"):
        st.session_state["act_limit"] = limit + ACTIVITY_STEP
        st.rerun()


def page_shared(settings: Settings, db, user) -> None:
    page_header("Dibagikan", "Tautan publik yang sedang aktif.")
    show_flash()
    shares = service.list_shares(db, user.id)
    if not shares:
        st.info("Belum ada tautan aktif. Buat dari menu Berkas dengan memilih satu berkas.")
        return
    for share, item in shares:
        with card():
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


@st.dialog("Unduh semua foto")
def download_all_photos(settings: Settings, gallery_id: str) -> None:
    with st.spinner("Mengumpulkan foto dan membuat ZIP…"):
        try:
            data, name, failed = service.gallery_zip(settings, gallery_id)
        except AppError as exc:
            st.error(str(exc))
            return
    st.caption(f"{fmt_bytes(len(data))} · {name}")
    if failed:
        st.warning(f"{len(failed)} foto gagal diambil dan tidak ikut: " + ", ".join(failed[:5]))
    st.download_button("Simpan ZIP ke perangkat", data, file_name=name, mime="application/zip", type="primary", icon=":material/folder_zip:")


def public_gallery(settings: Settings, db, token: str) -> None:
    hide_sidebar()
    found = service.resolve_gallery(db, token)
    if not found:
        st.title("🖼️ Galeri")
        st.error("Galeri ini tidak berlaku. Mungkin sudah kedaluwarsa atau dimatikan pemiliknya.")
        return
    gallery, files = found
    st.title(f"🖼️ {gallery.title}")
    bar = st.columns([4, 2])
    bar[0].caption(f"{len(files)} foto")
    if service.gallery_mode(gallery) != "none" and bar[1].button("Unduh semua (ZIP)", icon=":material/folder_zip:", width="stretch", key="dl_all"):
        download_all_photos(settings, gallery.id)
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
    with card():
        st.markdown("**Buat galeri baru**")
        if not rows:
            st.info("Belum ada foto. Unggah gambar lewat tombol Unggah di Berkas, lalu kembali ke sini.")
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
            slug = st.text_input(
                "Nama tautan (opsional)", key="g_slug", placeholder="mis. liburan-bali",
                help="Huruf kecil, angka, dan tanda hubung. Kosongkan untuk kode acak. Nama kustom lebih mudah ditebak orang.",
            )
            if slug.strip():
                st.caption(f"Tautan: {settings.app_url}/?g={slug.strip().lower()}")
            if st.button("Buat galeri", type="primary", key="g_make"):
                try:
                    gallery = service.create_gallery(db, user.id, title, picked, service.EXPIRY_CHOICES[label], mode, slug)
                    flash("ok", f"Galeri “{gallery.title}” dibuat.")
                except AppError as exc:
                    flash("err", str(exc))
                st.rerun()
    for gallery in service.list_galleries(db, user.id):
        with card():
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
        with card():
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
            "Dibagikan": page_shared,
            "Galeri": page_gallery,
            "Sampah": page_trash,
            "Aktivitas": page_activity,
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
        cached_usage = st.session_state.get("_usage")
        if cached_usage and time.time() - cached_usage[0] < 60:
            usage = cached_usage[1]
        else:
            usage = service.usage_by_kind(db, user.id)
            st.session_state["_usage"] = (time.time(), usage)
        with st.sidebar:
            st.markdown(usage_html(usage, totals), unsafe_allow_html=True)
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
