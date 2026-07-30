"""Domain model — the central contract (seam) of halo-monitor.

Pure dataclasses and enums only. NO logic, NO I/O, NO system calls here.

Dependency rule (see DESIGN.md §2): everything depends *inward* on this module.
Collectors and job parsers *produce* these types; the renderer *consumes* them and
never touches collectors/parsers directly. ``Snapshot`` is the single object that
crosses the acquisition -> presentation boundary.

The ``Phase`` and ``EtaNote`` values are stable *keys/enums*, not translated text.
Translation to ko/en happens in the renderer (DESIGN O10). The same phase keys are
also the ``phase`` field of the machine-readable status line (ADR-0002), so treat
them as a stable external contract consumed by the unattended monitoring agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class JobType(str, Enum):
    """Kind of GPU job being monitored."""

    TRAIN = "train"
    SCORE = "score"


class Phase(str, Enum):
    """Lifecycle phase of a job. String values are the stable contract keys.

    Training path:  QUANTIZING -> FIRST_STEP -> TRAINING -> EVAL_SAVE -> FINISHED
    Scoring path:   SCORE_PREP -> SCORING -> FINISHED
    """

    IDLE = "idle"
    # training
    QUANTIZING = "quantizing"
    FIRST_STEP = "first_step"
    TRAINING = "training"
    EVAL_SAVE = "eval_save"
    # scoring
    SCORE_PREP = "score_prep"
    SCORING = "scoring"
    # terminal (either job type)
    FINISHED = "finished"


class EtaNote(str, Enum):
    """A qualifier attached to an ETA. Key only — renderer supplies the ko/en text.

    Mirrors the free-text ETA annotations the bash tool printed inline (e.g.
    "학습 전 준비단계", "대략(태스크 편차 큼)"), but kept out of the parser so i18n
    stays in the renderer.
    """

    PRE_TRAINING_PREP = "pre_training_prep"
    PRE_SCORING_PREP = "pre_scoring_prep"
    FIRST_STEP_WARMUP = "first_step_warmup"
    BEYOND_STEP_COUNT = "beyond_step_count"
    ROUGH_HIGH_VARIANCE = "rough_high_variance"
    ESTIMATING_FIRST_TASK = "estimating_first_task"


class Source(str, Enum):
    """Provenance of a parsed :class:`JobState` — useful for debugging and for the
    unattended agents to know whether they got structured (JSON) or scraped data."""

    JSON = "json"      # derived from the HALOJSON status line (preferred)
    REGEX = "regex"    # derived from legacy log-phrase scraping (fallback)
    MIXED = "mixed"    # JSON progress + regex/systemd overlay
    NONE = "none"      # no data


@dataclass
class ModelInfo:
    """Model/config parsed from the job's ``command`` line (jobs/modelinfo.py)."""

    base_raw: str | None = None      # raw value of --base (may be a path)
    base_bn: str | None = None       # basename of base_raw
    base_label: str | None = None    # display label (mapped; falls back to base_bn)
    nbits: int | None = None         # --hqq-nbits
    seq: int | None = None           # --seq (training)
    max_new: int | None = None       # --max-new (scoring)
    lora_r: int | None = None        # --lora-r
    lora_mlp: bool = False           # --lora-mlp present
    epochs: int | None = None        # --epochs
    adapter: str | None = None       # basename of --adapter (scoring)
    heldout: bool = False            # --heldout present (scoring)
    eval_label: str | None = None    # --label: eval run name (eval_hard_tsc.py), for display


@dataclass
class JobState:
    """Everything the dashboard needs to know about the current job.

    ``phase`` is a :class:`Phase` key; the renderer translates it. Progress fields
    are populated per phase (unused ones stay ``None``). ETA is structured
    (seconds + optional note key) so the renderer, not the parser, formats it.
    """

    job_type: JobType
    phase: Phase = Phase.IDLE
    model_info: ModelInfo = field(default_factory=ModelInfo)

    # --- progress (per-phase; None when not applicable) ---
    quant_done: int | None = None
    quant_total: int | None = None
    step: int | None = None
    total: int | None = None
    loss: float | None = None
    sstep: float | None = None            # seconds per optimizer step
    gen_done: int | None = None           # scoring/eval: heldout tasks generated
    heldout_total: int | None = None      # scoring/eval: total heldout tasks
    last_gen: str | None = None           # scoring: last "generated [...]" line (verbatim)
    cur_task: str | None = None           # eval: name of the last generated task (cleaned)
    gen_tokens: int | None = None         # eval: cumulative generated tokens (sum of new=)
    eval_compiling: bool = False          # eval: `running tsc` seen (compile/score stage)
    eval_score: float | None = None       # eval: final SCORE g (from "SCORE g/max = pct%")
    eval_max: int | None = None           # eval: final SCORE max (task count)
    eval_pct: float | None = None         # eval: final percentage
    eval_clean: int | None = None         # eval: clean-compile count (from "CLEAN n/max")

    # Raw display strings, preserved verbatim from the log for byte-exact render
    # parity (the log may carry high precision like "1.9119" / "471.0" that a float
    # round-trip would lose). None on the JSON path — renderer formats the float then.
    loss_disp: str | None = None
    sstep_disp: str | None = None

    # --- derived / overlay ---
    eta_seconds: int | None = None
    eta_note: EtaNote | None = None
    error_count: int = 0
    elapsed_seconds: int | None = None
    unit_name: str | None = None          # unit name (scoring smodel fallback / display)
    unit_active: str | None = None        # systemd is-active (active/inactive/failed/...)
    unit_result: str | None = None        # systemd Result (success/exit-code/...)
    source: Source = Source.NONE


@dataclass
class MemoryStats:
    """Unified-memory picture (collectors, Phase 2). Bytes for raw sysfs values."""

    gtt_used_bytes: int | None = None
    gtt_total_bytes: int | None = None
    vram_used_bytes: int | None = None
    ram_free_gb: float | None = None
    swap_used_gb: float | None = None
    gtt_rate_mb_s: float | None = None    # derived over the tick delta (state/loop)
    gpu_busy_pct: int | None = None       # amdgpu gpu_busy_percent (utilization %), read
                                          # from the same card as GTT — instantaneous, no delta


@dataclass
class RawPower:
    """Raw, *stateless* power readings from collectors (Phase 2).

    The loop turns the RAPL energy counters into watts over the tick delta and
    handles counter wraparound; collectors never keep state. ``amdgpu_w`` is an
    already-instantaneous reading (hwmon ``power1_input``) usable as a total-power
    fallback when RAPL is unreadable.
    """

    pkg_uj: int | None = None       # RAPL package energy counter (microjoules) = PPT
    core_uj: int | None = None      # RAPL core (CPU) energy counter (microjoules)
    pkg_max_uj: int | None = None   # max_energy_range_uj for pkg (wraparound bound)
    core_max_uj: int | None = None  # max_energy_range_uj for core
    amdgpu_w: float | None = None   # instantaneous amdgpu power (hwmon), watts


@dataclass
class PowerStats:
    """3-way power split (RAPL), watts. GPU is Total - CPU approximation (Phase 2)."""

    cpu_w: float | None = None
    gpu_w: float | None = None
    total_w: float | None = None


@dataclass
class ClockStats:
    """GPU clocks (Phase 2)."""

    sclk_mhz: int | None = None


@dataclass
class DiskStat:
    """Filesystem usage for one configured mount (collectors/disk.py, Phase 5).

    Sourced from ``os.statvfs`` ONLY — free-block counters the kernel already
    caches, so probing is essentially zero-I/O and never competes with a running
    training job for disk bandwidth (C2 invariant: no ``du``, no directory walk,
    no file-content reads).

    ``free_bytes`` is the space available to an unprivileged user (``f_bavail``),
    i.e. what the dashboard reports as "free". ``used_bytes``/``used_pct`` are
    against the full capacity (``f_blocks``), so ``free + used`` need not equal
    ``total`` (the reserved-block gap). ``present`` is False when the mount could
    not be stat'd (unmounted / absent removable drive) — the renderer shows it as
    unavailable rather than crashing. ``low`` is the threshold verdict computed by
    the collector from the config warning limits.
    """

    path: str
    label: str | None = None          # display label; falls back to path
    total_bytes: int | None = None
    free_bytes: int | None = None     # available to unprivileged user (f_bavail)
    used_bytes: int | None = None
    used_pct: int | None = None       # used / total, rounded (awk %.0f parity)
    low: bool = False                 # free below a configured warning threshold
    present: bool = False             # mount was stat'd successfully


@dataclass
class RawNetIface:
    """Raw, *stateless* per-interface byte counters (collectors/network.py, Phase 5).

    The counters are the kernel's own cumulative-since-boot totals read straight
    from ``/sys/class/net/<name>/statistics/{rx,tx}_bytes`` — reading them costs no
    packet capture and never touches the wire (C2 invariant). Turning the counter
    delta into a throughput rate, and computing session totals, is ``loop.py``'s
    job (DESIGN §2.2 F); this collector keeps NO previous-sample reference.

    ``present`` is False when the interface's statistics could not be read (iface
    absent / renamed / permission) — reported as unavailable, never as an error.
    """

    name: str
    label: str | None = None         # display label; falls back to name (renderer)
    rx_bytes: int | None = None      # cumulative received bytes (since boot)
    tx_bytes: int | None = None      # cumulative transmitted bytes (since boot)
    present: bool = False


@dataclass
class NetStat:
    """Per-interface network throughput for one active interface (Phase 5).

    Derived by ``loop.py`` from two successive :class:`RawNetIface` samples: the
    download/upload *rates* are the byte-counter delta over the tick interval, and
    the session totals are the counter's growth since the monitor started. A
    counter reset/wraparound (negative delta) yields ``None`` for that rate this
    tick, matching the RAPL-watts wraparound handling. ``present`` mirrors the raw
    reading so the renderer shows a missing interface as unavailable.
    """

    name: str
    label: str | None = None          # display label; falls back to name
    rx_mb_s: float | None = None      # download rate over the tick (MB/s)
    tx_mb_s: float | None = None      # upload rate over the tick (MB/s)
    rx_session_bytes: int | None = None  # cumulative received since monitor start
    tx_session_bytes: int | None = None  # cumulative transmitted since monitor start
    present: bool = False


class EvalPhase(str, Enum):
    """Sub-stage of an eval/grading run, for the additive Eval widget (Phase 5)."""

    GENERATING = "generating"   # decoding heldout tasks (task N/total, tok/s)
    COMPILING = "compiling"     # tsc compile+score of the generated files
    FINISHED = "finished"       # done — final SCORE available


@dataclass
class EvalProgress:
    """Detailed progress of an eval/grading job for the additive Eval widget (Phase 5).

    Assembled by ``loop.py`` from the parsed :class:`JobState` PLUS loop-observed
    timing (the eval script prints no per-line timestamps, so throughput/ETA can only
    be observed across ticks — same reason GTT-rate/watts/net-rate live in the loop).

    ``tok_s`` is an *observed average* over the generation window the monitor actually
    watched; it is ``None`` when generation was already underway on the first tick (we
    can't honestly measure a rate we didn't see start). ``eta_note`` marks the ETA as
    rough/observed or "still estimating" per the "불확정이면 그렇게 표기" requirement.
    """

    label: str | None = None            # eval run label (--label) or unit name
    done: int | None = None             # tasks generated so far
    total: int | None = None            # heldout total (e.g. 7)
    cur_task: str | None = None         # last generated task name
    phase: EvalPhase = EvalPhase.GENERATING
    tok_s: float | None = None          # observed avg throughput (loop); None if unmeasurable
    eta_s: int | None = None            # observed ETA seconds (generation-scoped)
    eta_note: EtaNote | None = None     # rough/observed vs estimating
    score: float | None = None          # final SCORE g (when finished)
    max: int | None = None              # final SCORE max
    pct: float | None = None            # final percentage
    clean: int | None = None            # clean-compile count


@dataclass
class Flags:
    """Alert/threshold flags computed from a Snapshot (alerts.py, Phase 5)."""

    ram_low: bool = False
    has_error: bool = False
    disk_low: bool = False            # any configured mount below its free threshold


@dataclass
class Snapshot:
    """The one object that crosses acquisition -> presentation. Assembled by loop.py."""

    ts: float                              # epoch seconds when assembled
    title: str = "Strix Halo Train/Score Monitor"
    gfx: str = "gfx1151"
    job: JobState | None = None
    memory: MemoryStats = field(default_factory=MemoryStats)
    power: PowerStats = field(default_factory=PowerStats)
    clocks: ClockStats = field(default_factory=ClockStats)
    disks: list[DiskStat] = field(default_factory=list)
    net: list[NetStat] = field(default_factory=list)
    eval: EvalProgress | None = None       # present only for an active eval/grading job
    flags: Flags = field(default_factory=Flags)
