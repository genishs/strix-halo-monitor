"""RAPL + amdgpu power collector (DESIGN §2.2 B).

Reads the Intel RAPL package (PPT) and core (CPU) energy counters — plus each
one's ``max_energy_range_uj`` wraparound bound — directly from sysfs under the
injected root, and merges in the backend's instantaneous amdgpu hwmon power
reading. **Returns raw, stateless readings only**: turning energy-counter
deltas into watts, and handling counter wraparound, is ``loop.py``'s job
(DESIGN §2.2 F) — this collector must never compute a rate or keep a
previous-sample reference.
"""

from __future__ import annotations

import os

from ..model import RawPower
from .base import CollectContext

_PKG_DIR = "sys/class/powercap/intel-rapl:0"
_CORE_DIR = "sys/class/powercap/intel-rapl:0:0"


def _read_int(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class PowerCollector:
    """Collector for RAPL pkg/core energy counters + amdgpu instantaneous power."""

    name = "power"

    def available(self, ctx: CollectContext) -> bool:
        return (
            os.path.isfile(os.path.join(ctx.root, _PKG_DIR, "energy_uj"))
            or ctx.backend is not None
        )

    def collect(self, ctx: CollectContext) -> RawPower:
        pkg_uj = _read_int(os.path.join(ctx.root, _PKG_DIR, "energy_uj"))
        pkg_max_uj = _read_int(os.path.join(ctx.root, _PKG_DIR, "max_energy_range_uj"))
        core_uj = _read_int(os.path.join(ctx.root, _CORE_DIR, "energy_uj"))
        core_max_uj = _read_int(os.path.join(ctx.root, _CORE_DIR, "max_energy_range_uj"))

        amdgpu_w = ctx.backend.power().amdgpu_w if ctx.backend is not None else None

        return RawPower(
            pkg_uj=pkg_uj,
            core_uj=core_uj,
            pkg_max_uj=pkg_max_uj,
            core_max_uj=core_max_uj,
            amdgpu_w=amdgpu_w,
        )
