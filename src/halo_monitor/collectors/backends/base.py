"""GPU backend interface (DESIGN §2.2 B, C5).

A ``GpuBackend`` encapsulates all HW-specific sysfs paths so the rest of the code is
vendor-agnostic. ``amdgpu`` is the only implementation now; ``nvidia`` is a Phase-5
stub. Backends are **read-only** (C2) and **stateless** — the loop owns deltas.

⚠️ Implementations MUST NOT raise: on a missing/unreadable path, return ``None`` for
that field (the bash tool's "graceful blank" philosophy). No writes, no subprocess in
the hot path (a low-frequency ``rocm-smi`` clock fallback is the one allowed, optional
exception — see ClockCollector).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...model import ClockStats, MemoryStats, RawPower


@runtime_checkable
class GpuBackend(Protocol):
    """Vendor-specific probe. All methods read-only, non-raising, best-effort."""

    name: str

    def detect(self) -> bool:
        """True if this backend's HW is present under the configured sysfs root."""
        ...

    def mem_info(self) -> MemoryStats:
        """GPU-side memory only: gtt_used/gtt_total/vram_used (bytes).

        RAM/swap are host-level and filled by MemoryCollector, not the backend.
        """
        ...

    def power(self) -> RawPower:
        """Instantaneous GPU power in ``amdgpu_w`` (RAPL fields are host-level and set
        by PowerCollector). ``amdgpu_w`` is None if hwmon power is unavailable."""
        ...

    def clocks(self) -> ClockStats:
        """Current GPU shader clock (sclk_mhz), preferring sysfs over rocm-smi."""
        ...
