"""Unified-memory collector: GPU GTT/VRAM + host RAM/swap (DESIGN §2.2 B).

Combines the backend's GPU-side memory (GTT/VRAM, bytes) with host RAM/swap
read directly from ``/proc/meminfo`` under the injected root — the Python
replacement for the bash tool's ``free -m`` calls. Read-only, stateless,
non-raising. ``gtt_rate_mb_s`` is always left ``None`` here: it's a delta over
the tick interval and is owned by ``loop.py``, never by a collector.
"""

from __future__ import annotations

import os
import re

from ..model import MemoryStats
from .base import CollectContext

_RE_KB = re.compile(r"^(\w+):\s*(\d+)\s*kB", re.MULTILINE)


def _read_meminfo_kb(root: str) -> dict[str, int]:
    """Parse ``<root>/proc/meminfo`` into a ``{Field: kB}`` dict. ``{}`` on error."""
    path = os.path.join(root, "proc/meminfo")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    return {k: int(v) for k, v in _RE_KB.findall(text)}


class MemoryCollector:
    """Collector for GTT/VRAM (via the GPU backend) and host RAM/swap."""

    name = "memory"

    def available(self, ctx: CollectContext) -> bool:
        return ctx.backend is not None or os.path.isfile(
            os.path.join(ctx.root, "proc/meminfo")
        )

    def collect(self, ctx: CollectContext) -> MemoryStats:
        gpu_mem = ctx.backend.mem_info() if ctx.backend is not None else MemoryStats()

        info = _read_meminfo_kb(ctx.root)
        mem_available_kb = info.get("MemAvailable")
        swap_total_kb = info.get("SwapTotal")
        swap_free_kb = info.get("SwapFree")

        ram_free_gb = (
            mem_available_kb / 1024 / 1024 if mem_available_kb is not None else None
        )
        swap_used_gb = None
        if swap_total_kb is not None and swap_free_kb is not None:
            swap_used_gb = (swap_total_kb - swap_free_kb) / 1024 / 1024

        return MemoryStats(
            gtt_used_bytes=gpu_mem.gtt_used_bytes,
            gtt_total_bytes=gpu_mem.gtt_total_bytes,
            vram_used_bytes=gpu_mem.vram_used_bytes,
            gpu_busy_pct=gpu_mem.gpu_busy_pct,   # amdgpu utilization %, from the backend
            ram_free_gb=ram_free_gb,
            swap_used_gb=swap_used_gb,
            gtt_rate_mb_s=None,  # loop-owned delta; never set here
        )
