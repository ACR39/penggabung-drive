"""Aset tampilan: ikon SVG buatan sendiri, palet warna folder, dan penentuan jenis berkas.

Semua fungsi murni (tanpa Streamlit) supaya mudah diuji.
"""
from __future__ import annotations

import html

# Warna folder: kunci -> (warna garis ikon, nama tampilan)
COLORS: dict[str, tuple[str, str]] = {
    "blue": ("#3b82f6", "Biru"),
    "teal": ("#14b8a6", "Hijau toska"),
    "purple": ("#a855f7", "Ungu"),
    "orange": ("#f97316", "Oranye"),
    "red": ("#ef4444", "Merah"),
    "pink": ("#ec4899", "Merah muda"),
    "lime": ("#84cc16", "Hijau muda"),
    "gray": ("#64748b", "Abu-abu"),
}

# Ikon garis 24x24 (isi elemen svg, tanpa tag pembuka)
_PATHS: dict[str, str] = {
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
    "briefcase": '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M9 8V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18"/>',
    "film": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    "dollar": '<circle cx="12" cy="12" r="9"/><path d="M12 6.5v11M14.7 9.4c-.5-.9-1.4-1.3-2.7-1.3-1.5 0-2.6.8-2.6 2 0 1.3 1.1 1.7 2.6 2s2.6.7 2.6 2c0 1.2-1.1 2-2.6 2-1.3 0-2.2-.4-2.7-1.3"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-9 9"/>',
    "music": '<path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2"/><circle cx="17" cy="16" r="2"/>',
    "code": '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
    "star": '<path d="m12 3 2.6 5.9 6.4.7-4.8 4.3 1.4 6.3L12 17l-5.6 3.2 1.4-6.3L3 9.6l6.4-.7L12 3Z"/>',
    "heart": '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z"/>',
    "book": '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4Z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/>',
    "play": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 9 5 3-5 3V9Z"/>',
    "cloud": '<path d="M7 18a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 8.5a4.5 4.5 0 0 1 .5 9.5H7Z"/>',
}

# Ikon yang boleh dipilih untuk folder: kunci -> nama tampilan
FOLDER_ICONS: dict[str, str] = {
    "folder": "Folder",
    "briefcase": "Koper kerja",
    "film": "Video",
    "dollar": "Keuangan",
    "image": "Gambar",
    "music": "Musik",
    "code": "Kode",
    "book": "Buku",
    "heart": "Favorit",
    "star": "Bintang",
}

DEFAULT_COLOR = "blue"
DEFAULT_ICON = "folder"


def svg(name: str, size: int = 24, color: str = "currentColor", stroke: float = 1.8, fill: str = "none") -> str:
    body = _PATHS.get(name, _PATHS["file"])
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def color_hex(key: str | None) -> str:
    return COLORS.get(key or "", COLORS[DEFAULT_COLOR])[0]


# Jenis berkas: (kunci ikon, warna ikon, warna latar kotak ikon)
_KINDS: dict[str, tuple[str, str, str]] = {
    "video": ("play", "#ffffff", "#f97316"),
    "image": ("image", "#ffffff", "#eab308"),
    "audio": ("music", "#ffffff", "#ec4899"),
    "pdf": ("file", "#ffffff", "#06b6d4"),
    "sheet": ("table", "#ffffff", "#84cc16"),
    "archive": ("archive", "#ffffff", "#8b5cf6"),
    "code": ("code", "#ffffff", "#0ea5e9"),
    "doc": ("file", "#ffffff", "#3b82f6"),
    "other": ("file", "#ffffff", "#64748b"),
}

_EXT = {
    "mp4": "video", "mkv": "video", "mov": "video", "avi": "video", "webm": "video",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "webp": "image", "svg": "image",
    "mp3": "audio", "wav": "audio", "flac": "audio", "m4a": "audio", "ogg": "audio",
    "pdf": "pdf",
    "xls": "sheet", "xlsx": "sheet", "csv": "sheet", "ods": "sheet",
    "zip": "archive", "rar": "archive", "7z": "archive", "tar": "archive", "gz": "archive",
    "py": "code", "js": "code", "ts": "code", "json": "code", "html": "code", "css": "code", "sql": "code",
    "doc": "doc", "docx": "doc", "txt": "doc", "md": "doc", "ppt": "doc", "pptx": "doc", "odt": "doc",
}


def file_kind(mime: str | None, name: str | None = None) -> str:
    """Tentukan jenis berkas dari MIME, dengan cadangan dari ekstensi nama."""
    mime = (mime or "").lower()
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if "spreadsheet" in mime or "excel" in mime or mime == "text/csv":
        return "sheet"
    if "zip" in mime or "compressed" in mime or "tar" in mime or "rar" in mime:
        return "archive"
    ext = (name or "").rsplit(".", 1)[-1].lower() if name and "." in name else ""
    if ext in _EXT:
        return _EXT[ext]
    if mime.startswith("text/") or "word" in mime or "document" in mime or "presentation" in mime:
        return "doc"
    return "other"


def file_icon_html(kind: str, size: int = 26) -> str:
    icon, fg, bg = _KINDS.get(kind, _KINDS["other"])
    return (
        f'<div class="ftype" style="background:{bg}">{svg(icon, size, fg, 1.9)}</div>'
    )


def folder_icon_html(icon: str | None, color: str | None, size: int = 52) -> str:
    key = icon if icon in FOLDER_ICONS else DEFAULT_ICON
    return svg(key, size, color_hex(color), 1.6)


def esc(value) -> str:
    return html.escape(str(value), quote=True)
