"""Aturan memilih akun tujuan upload (fungsi murni, mudah diuji).

Tiga mode:
- most_available : akun dengan sisa ruang terbesar (akun tanpa batas paling akhir)
- round_robin    : bergantian mengikuti urutan prioritas lalu tanggal dibuat
- priority       : akun pertama pada urutan prioritas yang masih muat
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

MODES = ("most_available", "round_robin", "priority")


@dataclass(frozen=True)
class Candidate:
    id: str
    created_at: datetime
    available: int | None  # None = tidak terbatas / tidak diketahui


def _by_priority(items: list[Candidate], priority_ids: Iterable[str]) -> list[Candidate]:
    order = {account_id: i for i, account_id in enumerate(priority_ids)}

    def key(c: Candidate):
        if c.id in order:
            return (0, order[c.id], c.created_at)
        return (1, 0, c.created_at)

    return sorted(items, key=key)


def pick_account(
    candidates: Iterable[Candidate],
    size: int,
    mode: str = "most_available",
    priority_ids: Iterable[str] = (),
    cursor: int = 0,
    reserved: Mapping[str, int] | None = None,
) -> tuple[str | None, int]:
    """Kembalikan (id_akun, kursor_berikutnya). id_akun None bila tidak ada yang muat."""
    reserved = reserved or {}
    eligible: list[tuple[Candidate, int | None]] = []
    for c in candidates:
        left = None if c.available is None else c.available - reserved.get(c.id, 0)
        if left is None or left >= size:
            eligible.append((c, left))
    if not eligible:
        return None, cursor

    if mode == "priority":
        ordered = _by_priority([c for c, _ in eligible], priority_ids)
        return ordered[0].id, cursor

    if mode == "round_robin":
        ordered = _by_priority([c for c, _ in eligible], priority_ids)
        return ordered[cursor % len(ordered)].id, cursor + 1

    finite = sorted(
        [(c, left) for c, left in eligible if left is not None],
        key=lambda pair: (-pair[1], pair[0].created_at),
    )
    unlimited = sorted(
        [(c, left) for c, left in eligible if left is None],
        key=lambda pair: pair[0].created_at,
    )
    return (finite + unlimited)[0][0].id, cursor
