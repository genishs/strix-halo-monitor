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
    BatteryStat, ClockStats, DiskStat, EtaNote, EvalPhase, EvalProgress, Flags, JobState,
    JobType, MemoryStats, NetStat, Phase, PowerStats, RawNetIface, RawPower, Snapshot, TempStat,
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
        network: Collector,
        battery: Collector,
        temperature: Collector | None = None,
        job_provider: JobProvider,
        renderer: Renderer,
    ) -> None:
        self.cfg = cfg
        self.ctx = CollectContext(cfg=cfg, backend=backend, root=cfg.sysfs_root)
        self.memory = memory
        self.power = power
        self.clocks = clocks
        self.disk = disk
        self.network = network
        self.battery = battery
        self.temperature = temperature
        self.job_provider = job_provider
        self.renderer = renderer

        # --- mutable delta state (owned here, nowhere else) ---
        self._prev_mono: float | None = None
        self._prev_gtt: int | None = None
        self._prev_pkg_uj: int | None = None
        self._prev_core_uj: int | None = None
        # per-interface previous counters (for rate) and first-seen counters
        # (for session totals). Keyed by interface name.
        self._prev_net: dict[str, tuple[int, int]] = {}
        self._net_baseline: dict[str, tuple[int, int]] = {}
        # eval/grading generation observation (the eval log has no per-line
        # timestamps, so throughput/ETA are observed here across ticks).
        self._eval_unit: str | None = None       # unit whose generation we are timing
        self._eval_gen_start: float | None = None  # wall time generation first observed
        self._eval_from_zero: bool = False       # did we see generation begin at 0 tasks?

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

    def _net_stats(self, raw: list[RawNetIface], dt: float) -> list[NetStat]:
        """Per-interface RX/TX byte counters -> throughput (MB/s) + session totals.

        Rate = counter delta / elapsed, mirroring ``_gtt_rate_mb_s``. A negative
        delta (counter reset, e.g. interface down/up) yields ``None`` for that rate
        this tick — the same wraparound guard the RAPL-watts path uses. The session
        total is the counter's growth since the first tick that saw the interface.
        """
        out: list[NetStat] = []
        for r in raw:
            if not r.present or r.rx_bytes is None or r.tx_bytes is None:
                # Keep no state for an unreadable interface; drop any stale prev so a
                # re-appearance re-primes cleanly rather than spiking on a huge delta.
                self._prev_net.pop(r.name, None)
                out.append(NetStat(name=r.name, label=r.label, present=False))
                continue

            prev = self._prev_net.get(r.name)
            self._prev_net[r.name] = (r.rx_bytes, r.tx_bytes)

            rx_mb_s = tx_mb_s = None
            if prev is not None and dt > 0:
                drx, dtx = r.rx_bytes - prev[0], r.tx_bytes - prev[1]
                if drx >= 0:
                    rx_mb_s = drx / 1048576.0 / dt
                if dtx >= 0:
                    tx_mb_s = dtx / 1048576.0 / dt

            base = self._net_baseline.setdefault(r.name, (r.rx_bytes, r.tx_bytes))
            rx_session = r.rx_bytes - base[0] if r.rx_bytes >= base[0] else None
            tx_session = r.tx_bytes - base[1] if r.tx_bytes >= base[1] else None

            out.append(NetStat(
                name=r.name, label=r.label, present=True,
                rx_mb_s=rx_mb_s, tx_mb_s=tx_mb_s,
                rx_session_bytes=rx_session, tx_session_bytes=tx_session,
            ))
        return out

    def _eval_progress(self, job: JobState | None, now_wall: float) -> EvalProgress | None:
        """Assemble the Eval widget for an active eval/grading job (Phase 5).

        Only present for a SCORE job that has reached generation (or beyond) — during
        prep/quantization the main progress line already tells the story. Throughput
        and ETA are *observed* across ticks because the eval log carries no per-line
        timestamps: we time from the first tick we saw this unit generating.

        ``tok_s`` is reported only when we watched generation from 0 tasks (otherwise
        we'd divide tokens we never saw start by a too-short window). The ETA is a
        rough generation-scoped linear extrapolation, and it is also written back onto
        ``job`` so the main ETA line agrees with the widget (the parser's own estimate
        is unit-elapsed, which for a grading run is inflated by the long quant phase).
        """
        if job is None or job.job_type is not JobType.SCORE:
            self._eval_unit = None
            self._eval_gen_start = None
            return None

        generating = job.phase is Phase.SCORING
        finished = job.phase is Phase.FINISHED
        if not (generating or finished):
            return None  # prep/quantizing — main line covers it, no widget yet

        # Reset observation state when the monitored unit changes.
        if job.unit_name != self._eval_unit:
            self._eval_unit = job.unit_name
            self._eval_gen_start = None
            self._eval_from_zero = False

        # Start (or note) the generation-observation window on first sight.
        if generating and self._eval_gen_start is None:
            self._eval_gen_start = now_wall
            self._eval_from_zero = not job.gen_done  # True when 0/None tasks so far

        done, total = job.gen_done, job.heldout_total
        gen_elapsed = None
        if self._eval_gen_start is not None:
            gen_elapsed = max(0.0, now_wall - self._eval_gen_start)

        # Observed average throughput — only when honestly measurable.
        tok_s = None
        if (
            self._eval_from_zero and job.gen_tokens
            and gen_elapsed is not None and gen_elapsed > 1.0
        ):
            tok_s = job.gen_tokens / gen_elapsed

        # Observed, generation-scoped ETA (rough); estimating before the first task.
        eta_s: int | None = None
        eta_note: EtaNote | None = None
        if not finished:
            if done and total and gen_elapsed is not None and gen_elapsed > 1.0 and done >= 1:
                eta_s = max(0, int(gen_elapsed * (total - done) / done))
                eta_note = EtaNote.ROUGH_HIGH_VARIANCE
            else:
                eta_note = EtaNote.ESTIMATING_FIRST_TASK
            # Keep the main ETA line consistent with this observed value.
            job.eta_seconds = eta_s
            job.eta_note = eta_note

        if finished:
            phase = EvalPhase.FINISHED
        elif job.eval_compiling and (not total or (done or 0) >= total):
            phase = EvalPhase.COMPILING
        else:
            phase = EvalPhase.GENERATING

        label = job.model_info.eval_label or job.unit_name
        return EvalProgress(
            label=label, done=done, total=total, cur_task=job.cur_task,
            phase=phase, tok_s=tok_s, eta_s=eta_s, eta_note=eta_note,
            score=job.eval_score, max=job.eval_max, pct=job.eval_pct, clean=job.eval_clean,
        )

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
        raw_net: list[RawNetIface] = _safe(lambda: self.network.collect(self.ctx), [])
        net = self._net_stats(raw_net, dt)
        battery: BatteryStat = _safe(lambda: self.battery.collect(self.ctx), BatteryStat())
        temps: list[TempStat] = (
            _safe(lambda: self.temperature.collect(self.ctx), [])
            if self.temperature is not None else []
        )
        job: JobState | None = _safe(lambda: self.job_provider(now_wall), None)
        eval_progress = _safe(lambda: self._eval_progress(job, now_wall), None)

        flags = Flags(
            ram_low=(mem.ram_free_gb is not None and mem.ram_free_gb < _RAM_LOW_GB),
            has_error=bool(job and job.error_count),
            disk_low=any(d.low for d in disks),
            battery_low=(battery.alert != "ok"),
            temp_hot=any(t.alert != "ok" for t in temps),
        )
        return Snapshot(
            ts=now_wall,
            title=self.cfg.title,
            job=job,
            memory=mem,
            power=power,
            clocks=clk,
            disks=disks,
            net=net,
            eval=eval_progress,
            battery=battery,
            temps=temps,
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
