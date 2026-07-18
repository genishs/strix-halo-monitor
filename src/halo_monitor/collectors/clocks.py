"""GPU clock collector (DESIGN §2.2 B).

Thin delegate to the ``GpuBackend`` (which owns the sysfs ``pp_dpm_sclk`` vs
``rocm-smi`` fallback decision — see ``backends/amdgpu.py``). Kept as its own
collector, rather than folded into memory/power, so ``loop.py`` only ever
talks to the uniform ``Collector`` protocol and never calls a backend
directly.
"""

from __future__ import annotations

from ..model import ClockStats
from .base import CollectContext


class ClockCollector:
    """Collector for the current GPU shader clock (``sclk_mhz``)."""

    name = "clocks"

    def available(self, ctx: CollectContext) -> bool:
        return ctx.backend is not None

    def collect(self, ctx: CollectContext) -> ClockStats:
        if ctx.backend is None:
            return ClockStats()
        return ctx.backend.clocks()
