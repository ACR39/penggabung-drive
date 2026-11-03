"""Fungsi kecil untuk tampilan."""
from __future__ import annotations

from datetime import datetime


def fmt_bytes(value: int | None) -> str:
    if value is None:
        return "Tak terbatas"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def fmt_dt(value: datetime | None) -> str:
    return value.strftime("%d %b %Y %H:%M") if value else "-"


def file_icon(mime: str) -> str:
    if mime.startswith("image/"):
        return "🖼️"
    if mime.startswith("video/"):
        return "🎬"
    if mime.startswith("audio/"):
        return "🎵"
    if mime == "application/pdf":
        return "📕"
    if "zip" in mime or "compressed" in mime or "tar" in mime:
        return "🗜️"
    if "sheet" in mime or "excel" in mime or mime == "text/csv":
        return "📊"
    if "word" in mime or "document" in mime or mime.startswith("text/"):
        return "📄"
    if "presentation" in mime or "powerpoint" in mime:
        return "📽️"
    return "📦"
