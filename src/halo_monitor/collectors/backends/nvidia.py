"""nvidia ``GpuBackend`` stub (DESIGN §2.2 B, O7 — YAGNI, not implemented yet).

Placeholder only: no NVML/``nvidia-smi`` calls. ``detect()`` always returns
``False`` so ``select_backend`` falls through to this stub without ever
mistaking a non-amdgpu box for one. Real implementation is deferred to
whoever picks up GitHub issue #7 (shas, on the 4060 box) — until then this
keeps ``GpuBackend`` a satisfiable Protocol for any code that wants an
"always present" backend object.
"""

from __future__ import annotations

from ...model import ClockStats, MemoryStats, RawPower


class NvidiaBackend:
    """Stub ``GpuBackend`` for NVIDIA GPUs. See issue #7 for the real implementation."""

    name = "nvidia"

    def __init__(self, root: str) -> None:
        self.root = root

    def detect(self) -> bool:
        return False

    def mem_info(self) -> MemoryStats:
        return MemoryStats()

    def power(self) -> RawPower:
        return RawPower()

    def clocks(self) -> ClockStats:
        return ClockStats()
