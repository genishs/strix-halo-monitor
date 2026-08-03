"""Disk-usage collector: per-mount capacity / free space (DESIGN §2.2 B, §3, Phase 5).

C2 INVARIANT (training must not be disturbed): this collector reads free-space
information via ``os.statvfs`` **only** — the kernel's cached filesystem block
counters — plus the procfs mount table (``/proc/mounts``, an in-memory kernel text
file, not storage). It performs NO directory recursion, NO ``du``, NO ``df``/``lsblk``
subprocess and NO reads of real files, so a tick costs effectively zero disk I/O and
never competes with a running training/scoring job for storage bandwidth. Do not add
any path-walking here.

Like every other collector it is read-only and non-raising: a mount that cannot be
stat'd (unmounted, absent removable drive, permission) comes back as
``present=False`` with empty numbers rather than an exception.

Mount selection: when ``cfg.disk_mounts is None`` the mounts are auto-discovered
from the mount table (see :mod:`.mounts`) rather than taken from a hardcoded list —
that list silently hid every drive it did not name, which is the bug this replaces.
Discovery re-runs every ``cfg.disk_rescan_s`` seconds, so hot-plugging an external
drive shows up without a restart. That cache is the collector's only state and is a
pure performance memo (drop it and behaviour is identical, only chattier), so the
"collectors own no state the loop needs" rule still holds: no deltas, no previous
samples.

``os.statvfs`` and the monotonic clock are injected (defaults are the real ones) so
tests exercise thresholds, discovery and hot-plug deterministically without touching
real mounts.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from ..config import Config, DiskTarget
from ..model import DiskStat
from . import mounts as mounts_mod
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


def _cap(stats: list[DiskStat], limit: int) -> list[DiskStat]:
    """Keep at most ``limit`` mounts, preferring the largest by capacity.

    Only auto-discovery is capped, and only when it overflows: a machine with a dozen
    volumes must not push the rest of the frame off-screen. Below the limit the
    display order from ``mounts.py`` is preserved untouched; above it, the surviving
    rows are the biggest disks, shown largest-first (unstat-able mounts sort last).
    """
    if limit <= 0 or len(stats) <= limit:
        return stats
    return sorted(stats, key=lambda d: -(d.total_bytes or 0))[:limit]


class DiskCollector:
    """Collector for per-mount usage/free space via ``os.statvfs`` (statvfs-only)."""

    name = "disk"

    def __init__(
        self,
        statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._statvfs = statvfs
        self._monotonic = monotonic
        self._cache: list[DiskTarget] | None = None
        self._cache_at: float = 0.0
        self._cache_root: str | None = None

    def available(self, ctx: CollectContext) -> bool:
        # Disabled only when the user explicitly cleared the mount list ("()").
        # None (auto-discover) and a non-empty explicit list are both available.
        return ctx.cfg.disk_mounts != ()

    def collect(self, ctx: CollectContext) -> list[DiskStat]:
        cfg = ctx.cfg
        if cfg.disk_mounts is not None:
            # Explicit list: honour it exactly — order and count included.
            return [self._probe(t, cfg) for t in cfg.disk_mounts]
        stats = [self._probe(t, cfg) for t in self._discover(ctx)]
        return _cap(stats, cfg.disk_max_mounts)

    def _discover(self, ctx: CollectContext) -> list[DiskTarget]:
        """Auto-discovered mounts, re-read at most every ``cfg.disk_rescan_s`` seconds.

        The TTL is what makes drive hot-plug visible mid-run. A change of ``ctx.root``
        (tests pointing at a fixture tree) invalidates the cache too, so one collector
        instance cannot leak another root's mounts.
        """
        now = self._monotonic()
        fresh = (
            self._cache is not None
            and self._cache_root == ctx.root
            and (now - self._cache_at) < ctx.cfg.disk_rescan_s
        )
        if not fresh:
            self._cache = mounts_mod.discover(ctx.root)
            self._cache_at = now
            self._cache_root = ctx.root
        return self._cache

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
