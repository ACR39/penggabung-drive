"""Penggabung Drive: gabungkan beberapa akun Google Drive menjadi satu penyimpanan virtual.

Terinspirasi dari konsep 9Drive (github.com/zenhosta/9drive, Apache-2.0).
Kode ini ditulis ulang dari nol dalam Python/Streamlit.
"""
from __future__ import annotations

import secrets

import pandas as pd
import streamlit as st

from core import drive, service
from core.config import Settings, load_settings
from core.db import get_session_factory
from core.service import AppError, NeedsConsent
from core.utils import file_icon, fmt_bytes, fmt_dt

st.set_page_config(page_title="Penggabung Drive", page_icon="🗂️", layout="wide")

ACCENT = "#16a34a"
st.markdown(
    f"""
<style>
[data-testid="stSidebar"] {{ background:#0f172a; }}
[data-testid="stSidebar"] * {{ color:#e2e8f0; }}
[data-testid="stSidebar"] [role="radiogroup"] label {{ padding:.35rem .5rem; border-radius:.5rem; }}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{ background:{ACCENT}33; }}
div[data-testid="stMetric"] {{ background:#fff; border:1px solid #e5e7eb; border-radius:.75rem; padding:.75rem 1rem; }}
.chip {{ display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.78rem; background:#dcfce7; color:#166534; }}
.chip.warn {{ background:#fee2e2; color:#991b1b; }}
</style>
""",
    unsafe_allow_html=True,
)

MENU = ["🏠 Beranda", "🗂️ Berkas", "⬆️ Unggah", "🔗 Dibagikan", "🗑️ Sampah", "⚙️ Pengaturan"]


# ------------------------------------------------------------------ pembantu


def flash(kind: str, text: str) -> None:
    """Simpan pesan agar tetap tampil setelah st.rerun()."""
    st.session_state.setdefault("_flash", []).append((kind, text))


def show_flash() -> None:
    for kind, text in st.session_state.pop("_flash", []):
        {"ok": st.success, "err": st.error, "info": st.info}.get(kind, st.info)(text)


def login_url(settings: Settings, db, purpose: str, user_id: str | None, consent: bool) -> str:
    state = secrets.token_urlsafe(24)
    url, verifier = drive.authorization_url(settings, state, force_consent=consent)
    service.save_oauth_state(db, state, purpose, user_id, verifier)
    return url


def current_user(db):
    uid = st.session_state.get("user_id")
    if not uid:
        return None
    from core.db import User

    user = db.get(User, uid)
    if user is None:
        st.session_state.pop("user_id", None)
    return user


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
    pending = service.pop_oauth_state(db, state or "")
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
    flash("ok", f"Akun {profile.get('email')} terhubung.")


def login_page(settings: Settings, db) -> None:
    left, mid, right = st.columns([1, 2, 1])
    with mid:
        st.title("🗂️ Penggabung Drive")
        st.write(
            "Gabungkan beberapa akun Google Drive menjadi **satu penyimpanan virtual**. "
            "Unggahan dipilihkan akun yang masih punya ruang."
        )
        show_flash()
        consent = st.session_state.get("need_consent", False)
        if "login_url" not in st.session_state or st.session_state.get("login_consent") != consent:
            st.session_state["login_url"] = login_url(settings, db, "login", None, consent)
            st.session_state["login_consent"] = consent
        st.link_button(
            "Masuk dengan Google", st.session_state["login_url"], type="primary", width="stretch"
        )
        st.caption(
            "Masuklah dengan akun Google utama. Akun lain bisa ditambahkan setelah masuk "
            "(Pengaturan → Hubungkan akun). Halaman Google terbuka di tab baru."
        )


# ------------------------------------------------------------------ halaman utama


def page_home(settings: Settings, db, user) -> None:
    st.header("Beranda")
    accounts = service.list_accounts(db, user.id)
    top = st.columns([4, 1])
    if top[1].button("🔄 Segarkan kuota", width="stretch"):
        with st.spinner("Menghubungi Google…"):
            for msg in service.refresh_stale(db, settings, accounts, force=True):
                flash("err", msg)
        st.rerun()
    show_flash()
    errors = service.refresh_stale(db, settings, accounts)
    for msg in errors:
        st.warning(msg)
    if not accounts:
        st.info("Belum ada akun. Buka Pengaturan untuk menghubungkan Google Drive.")
        return
    totals = service.quota_totals(accounts)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total ruang", fmt_bytes(totals.total))
    c2.metric("Terpakai", fmt_bytes(totals.used))
    c3.metric("Tersisa", fmt_bytes(totals.free))
    c4.metric("Akun terhubung", sum(1 for a in accounts if a.status == "connected"))
    if totals.total:
        st.progress(min(totals.used / totals.total, 1.0), text=f"{totals.used / totals.total:.0%} terpakai")
    if totals.unlimited_accounts:
        st.caption(f"{totals.unlimited_accounts} akun berkuota tak terbatas tidak dihitung ke total.")
    st.subheader("Per akun")
    for acc in accounts:
        with st.container(border=True):
            cols = st.columns([3, 5, 2])
            cols[0].markdown(f"**{acc.email}**")
            if acc.status != "connected":
                cols[2].markdown('<span class="chip warn">Perlu hubungkan ulang</span>', unsafe_allow_html=True)
                cols[1].caption(acc.last_error or "")
                continue
            if acc.total_bytes:
                cols[1].progress(min((acc.used_bytes or 0) / acc.total_bytes, 1.0))
            cols[1].caption(
                f"{fmt_bytes(acc.used_bytes)} dari {fmt_bytes(acc.total_bytes)} · "
                f"disinkronkan {fmt_dt(acc.last_synced_at)} UTC"
            )
            cols[2].markdown('<span class="chip">Terhubung</span>', unsafe_allow_html=True)


def folder_picker(db, user, label: str, key: str, exclude: str | None = None) -> str | None:
    options = [o for o in service.folder_options(db, user.id) if o[0] != exclude]
    labels = {fid: text for fid, text in options}
    return st.selectbox(label, [fid for fid, _ in options], format_func=lambda x: labels[x], key=key)


def page_files(settings: Settings, db, user) -> None:
    st.header("Berkas")
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
    st.header("Unggah")
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
    st.header("Dibagikan")
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
    st.header("Sampah")
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
    st.header("Pengaturan")
    show_flash()
    accounts = service.list_accounts(db, user.id)

    st.subheader("Akun Google Drive")
    if "connect_url" not in st.session_state:
        st.session_state["connect_url"] = login_url(settings, db, "connect", user.id, True)
    st.link_button("➕ Hubungkan akun Google lain", st.session_state["connect_url"])
    for acc in accounts:
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 2])
            cols[0].markdown(f"**{acc.email}**" + (" ⚠️" if acc.status != "connected" else ""))
            cols[1].caption(fmt_bytes(acc.used_bytes) + " terpakai")
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
            if acc.status != "connected":
                st.caption(acc.last_error or "Perlu hubungkan ulang: tekan tombol hubungkan di atas dan pilih akun ini.")
    if len(accounts) == 0:
        st.info("Belum ada akun.")

    st.subheader("Aturan pemilihan akun saat unggah")
    policy = service.get_policy(db, user.id)
    modes = {
        "most_available": "Ruang terbanyak: ke akun yang paling lega",
        "round_robin": "Bergantian: giliran antar akun",
        "priority": "Urutan prioritas: akun pertama yang masih muat",
    }
    mode = st.radio("Mode", list(modes), format_func=lambda m: modes[m], index=list(modes).index(policy.mode), key="mode")
    connected = [a for a in accounts if a.status == "connected"]
    labels = {a.id: a.email for a in connected}
    prio = st.multiselect(
        "Urutan prioritas (dipakai mode Bergantian dan Prioritas)",
        [a.id for a in connected],
        default=[i for i in (policy.priority_ids or []) if i in labels],
        format_func=lambda i: labels[i],
    )
    if st.button("Simpan aturan"):
        service.set_policy(db, user.id, mode, prio)
        flash("ok", "Aturan disimpan.")
        st.rerun()

    st.divider()
    st.caption(f"Masuk sebagai {user.email}")
    if st.button("Keluar"):
        logout()


# ------------------------------------------------------------------ titik masuk


def main() -> None:
    settings = load_settings()
    problems = settings.missing()
    if problems:
        st.title("🗂️ Penggabung Drive")
        st.error("Pengaturan belum lengkap: " + ", ".join(problems))
        st.markdown("Isi lewat `.streamlit/secrets.toml` atau variabel lingkungan. Lihat README.")
        return

    db = get_session_factory(settings.database_url)()
    try:
        share_token = st.query_params.get("s")
        if share_token:
            public_page(settings, db, share_token)
            return
        if "code" in st.query_params or "error" in st.query_params:
            handle_callback(settings, db)
            st.rerun()

        user = current_user(db)
        if user is None:
            login_page(settings, db)
            return

        with st.sidebar:
            st.markdown("## 🗂️ Penggabung Drive")
            page = st.radio("Menu", MENU, label_visibility="collapsed", key="menu")
            st.caption(user.email)
        {
            MENU[0]: page_home,
            MENU[1]: page_files,
            MENU[2]: page_upload,
            MENU[3]: page_shared,
            MENU[4]: page_trash,
            MENU[5]: page_settings,
        }[page](settings, db, user)
    finally:
        db.close()


main()
