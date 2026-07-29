"""Update loop / scheduler (DESIGN §2.2 F).

The ONE place that owns time and mutable state. Every tick it calls the collectors
and the job provider, derives the delta-based metrics (GTT rate, RAPL watts), assembles
a :class:`Snapshot`, and hands it to the renderer. Everything else stays pure/stateless.

Resilience: a collector or the job provider raising must NOT kill the loop — the
offending part is left blank for that tick (the bash tool's ``2>/dev/null`` tolerance).

Read-only (C2): the loop only reads; it never writes to sysfs/systemd or touches units.

The loop takes its collaborators by dependency injection (assembled in ``app.py``), so
``tick()`` is unit-testable with fakes and injected clocks — no hardware required.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass
from typing import Callable

from .collectors.base import CollectContext, Collector
from .collectors.backends.base import GpuBackend
from .config import Config
from .model import (
    ClockStats, DiskStat, Flags, JobState, MemoryStats, PowerStats, RawPower, Snapshot,
)

# A job provider hides systemd detection + parsing behind one call so the loop stays
# HW/systemd-independent. Wired in app.py to detect.find_active_unit + jobs.parse_job.
JobProvider = Callable[[float], "JobState | None"]
Renderer = Callable[[Snapshot], None]

_RAM_LOW_GB = 3.0  # monitor.sh: ram < 3 -> ⚠️ LOW


def _safe(fn, default):
    """Call ``fn()`` returning ``default`` on any exception (loop resilience)."""
    try:
        return fn()
    except Exception:
        return default


class UpdateLoop:
    def __init__(
        self,
        cfg: Config,
        *,
        backend: GpuBackend | None,
        memory: Collector,
        power: Collector,
        clocks: Collector,
        disk: Collector,
        job_provider: JobProvider,
        renderer: Renderer,
    ) -> None:
        self.cfg = cfg
        self.ctx = CollectContext(cfg=cfg, backend=backend, root=cfg.sysfs_root)
        self.memory = memory
        self.power = power
        self.clocks = clocks
        self.disk = disk
        self.job_provider = job_provider
        self.renderer = renderer

        # --- mutable delta state (owned here, nowhere else) ---
        self._prev_mono: float | None = None
        self._prev_gtt: int | None = None
        self._prev_pkg_uj: int | None = None
        self._prev_core_uj: int | None = None

        self._stop = False
        self._resized = False

    # -- derived-metric helpers ------------------------------------------- #
    def _gtt_rate_mb_s(self, gtt_used: int | None, dt: float) -> float | None:
        prev, self._prev_gtt = self._prev_gtt, gtt_used
        if prev is None or gtt_used is None or dt <= 0:
            return None
        return (gtt_used - prev) / 1048576.0 / dt

    def _watts(self, raw: RawPower, dt: float) -> PowerStats:
        """RAPL energy delta -> watts (monitor.sh: cpu=core, total=pkg, gpu=total-cpu).

        Negative delta = counter wraparound -> skip this cycle (leave None), matching
        the bash tool. amdgpu hwmon reading is the total-power fallback.
        """
        prev_pkg, prev_core = self._prev_pkg_uj, self._prev_core_uj
        self._prev_pkg_uj, self._prev_core_uj = raw.pkg_uj, raw.core_uj

        ps = PowerStats(total_w=raw.amdgpu_w)  # fallback if RAPL unavailable
        if (
            prev_pkg is not None and prev_core is not None
            and raw.pkg_uj is not None and raw.core_uj is not None
            and dt > 0
        ):
            dpkg = raw.pkg_uj - prev_pkg
            dcore = raw.core_uj - prev_core
            if dpkg >= 0 and dcore >= 0:  # skip on wraparound (parity with bash)
                ps.total_w = dpkg / 1e6 / dt
                ps.cpu_w = dcore / 1e6 / dt
                gpu = ps.total_w - ps.cpu_w
                ps.gpu_w = gpu if gpu > 0 else 0.0
        return ps

    # -- one tick --------------------------------------------------------- #
    def tick(self, now_mono: float, now_wall: float) -> Snapshot:
        dt = 0.0 if self._prev_mono is None else max(0.0, now_mono - self._prev_mono)
        self._prev_mono = now_mono

        mem: MemoryStats = _safe(lambda: self.memory.collect(self.ctx), MemoryStats())
        mem.gtt_rate_mb_s = self._gtt_rate_mb_s(mem.gtt_used_bytes, dt)

        raw: RawPower = _safe(lambda: self.power.collect(self.ctx), RawPower())
        power = self._watts(raw, dt)

        clk: ClockStats = _safe(lambda: self.clocks.collect(self.ctx), ClockStats())
        disks: list[DiskStat] = _safe(lambda: self.disk.collect(self.ctx), [])
        job: JobState | None = _safe(lambda: self.job_provider(now_wall), None)

        flags = Flags(
            ram_low=(mem.ram_free_gb is not None and mem.ram_free_gb < _RAM_LOW_GB),
            has_error=bool(job and job.error_count),
            disk_low=any(d.low for d in disks),
        )
        return Snapshot(
            ts=now_wall,
            title=self.cfg.title,
            job=job,
            memory=mem,
            power=power,
            clocks=clk,
            disks=disks,
            flags=flags,
        )

    # -- signal handling -------------------------------------------------- #
    def _on_sigint(self, *_):
        self._stop = True

    def _on_sigwinch(self, *_):
        self._resized = True

    def run(self, *, clock_mono, clock_wall, sleep) -> None:
        """Blocking main loop. Clocks/sleep injected for testability.

        SIGINT -> graceful stop; SIGWINCH -> re-layout flag (consumed by renderer).
        """
        signal.signal(signal.SIGINT, self._on_sigint)
        if hasattr(signal, "SIGWINCH"):
            signal.signal(signal.SIGWINCH, self._on_sigwinch)
        while not self._stop:
            snap = self.tick(clock_mono(), clock_wall())
            _safe(lambda: self.renderer(snap), None)
            self._resized = False
            sleep(self.cfg.interval_s)
