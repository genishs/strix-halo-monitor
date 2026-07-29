"""Disk-usage collector: per-mount capacity / free space (DESIGN §2.2 B, §3, Phase 5).

C2 INVARIANT (training must not be disturbed): this collector reads free-space
information via ``os.statvfs`` **only** — the kernel's cached filesystem block
counters. It performs NO directory recursion, NO ``du``, and NO file-content
reads, so a tick costs effectively zero disk I/O and never competes with a
running training/scoring job for storage bandwidth. Do not add any path-walking
here.

Like every other collector it is read-only, stateless, and non-raising: a mount
that cannot be stat'd (unmounted, absent removable drive, permission) comes back
as ``present=False`` with empty numbers rather than an exception.

``os.statvfs`` is injected (default the real one) so tests exercise the threshold
logic deterministically without touching real mounts.
"""

from __future__ import annotations

import os
from typing import Callable

from ..config import Config, DiskTarget
from ..model import DiskStat
from .base import CollectContext

_GIB = 1073741824  # binary GiB, consistent with ui/widgets.gb1 / the GTT widget


def is_low(
    free_bytes: int | None,
    total_bytes: int | None,
    warn_free_gb: float,
    warn_free_pct: float,
) -> bool:
    """Warning verdict: free space below the GiB floor OR below the percent floor.

    Pure and side-effect-free so the threshold logic is unit-testable on its own.
    Unknown inputs (missing mount) are never "low" — absence is reported via
    ``DiskStat.present``, not as a false alarm.
    """
    if free_bytes is None or total_bytes is None or total_bytes <= 0:
        return False
    free_gb = free_bytes / _GIB
    free_pct = free_bytes / total_bytes * 100.0
    return free_gb < warn_free_gb or free_pct < warn_free_pct


class DiskCollector:
    """Collector for per-mount usage/free space via ``os.statvfs`` (statvfs-only)."""

    name = "disk"

    def __init__(self, statvfs: Callable[[str], os.statvfs_result] = os.statvfs) -> None:
        self._statvfs = statvfs

    def available(self, ctx: CollectContext) -> bool:
        return bool(ctx.cfg.disk_mounts)

    def collect(self, ctx: CollectContext) -> list[DiskStat]:
        return [self._probe(target, ctx.cfg) for target in ctx.cfg.disk_mounts]

    def _probe(self, target: DiskTarget, cfg: Config) -> DiskStat:
        label = target.label or target.path
        try:
            st = self._statvfs(target.path)
        except OSError:
            # Unmounted / absent removable drive / no permission — report unavailable.
            return DiskStat(path=target.path, label=label, present=False)

        frsize = st.f_frsize or st.f_bsize
        total = st.f_blocks * frsize
        free = st.f_bavail * frsize                 # usable by an unprivileged user
        used = total - st.f_bfree * frsize          # incl. reserved blocks
        used_pct = int(f"{used / total * 100:.0f}") if total > 0 else None
        low = is_low(free, total, cfg.disk_warn_free_gb, cfg.disk_warn_free_pct)
        return DiskStat(
            path=target.path,
            label=label,
            total_bytes=total,
            free_bytes=free,
            used_bytes=used,
            used_pct=used_pct,
            low=low,
            present=True,
        )
