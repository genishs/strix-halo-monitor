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
    gen_done: int | None = None           # scoring: heldout tasks generated
    heldout_total: int | None = None      # scoring: total heldout tasks
    last_gen: str | None = None           # scoring: last "generated [...]" line

    # --- derived / overlay ---
    eta_seconds: int | None = None
    eta_note: EtaNote | None = None
    error_count: int = 0
    elapsed_seconds: int | None = None
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
class Flags:
    """Alert/threshold flags computed from a Snapshot (alerts.py, Phase 5)."""

    ram_low: bool = False
    has_error: bool = False


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
    flags: Flags = field(default_factory=Flags)
