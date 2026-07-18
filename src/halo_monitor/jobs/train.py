"""Training job parser (DESIGN §2.2 C).

Phase resolution mirrors monitor.sh's if/elif chain exactly for the regex fallback
(including its quirk of showing the last training line for a crashed run — parity is
the Phase 3 goal). When a HALOJSON status line is present it is authoritative for
phase and progress (ADR-0002); systemd liveness is still surfaced via ``unit_active``.
"""

from __future__ import annotations

from ..config import Config
from ..model import JobState, JobType, Phase, Source
from ..status_schema import parse_last_status
from . import base
from ._scrape import as_float, as_int, count_errors, train_progress
from .eta import eta_for
from .modelinfo import parse_model_info

# Phases a training status line may legitimately carry.
_TRAIN_PHASES = {
    Phase.IDLE,
    Phase.QUANTIZING,
    Phase.FIRST_STEP,
    Phase.TRAINING,
    Phase.EVAL_SAVE,
    Phase.FINISHED,
}


def _is_active(active: str | None) -> bool:
    # monitor.sh: `[ "$active" = "active" ]` — empty/None counts as not active.
    return active == "active"


def _phase_or_none(s) -> Phase | None:
    try:
        return Phase(s)
    except ValueError:
        return None


def _regex_phase(nums: dict, active: str | None) -> Phase:
    """Exact port of monitor.sh's training if/elif chain."""
    step, total = nums["step"], nums["total"]
    if step is not None and total is not None and step >= total and _is_active(active):
        return Phase.EVAL_SAVE
    if step is not None and total is not None:
        return Phase.TRAINING
    if total is not None:
        return Phase.FIRST_STEP
    if nums["quant_done"] is not None:
        return Phase.QUANTIZING
    if not _is_active(active):
        return Phase.FINISHED
    return Phase.IDLE


@base.register
class TrainParser:
    """Catch-all parser (registered last): handles anything not matched as scoring."""

    name = "train"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def matches(self, unit: base.UnitRef) -> bool:  # fallback for all non-score units
        return True

    def parse(self, log_text: str, unit: base.UnitRef, now: float) -> JobState:
        mi = parse_model_info(log_text, self.cfg)
        errs = count_errors(log_text)
        elapsed = None
        if unit.start_epoch:
            elapsed = max(0, int(now - unit.start_epoch))

        status = parse_last_status(log_text)
        phase: Phase | None = None
        nums: dict
        source = Source.REGEX

        if status is not None and status.get("job", "train") == "train":
            jp = _phase_or_none(status.get("phase"))
            if jp in _TRAIN_PHASES:
                phase = jp
                nums = {
                    "step": as_int(status.get("step")),
                    "total": as_int(status.get("total")),
                    "loss": as_float(status.get("loss")),
                    # canonical wire field is s_step; accept legacy sstep too (ADR-0002)
                    "sstep": as_float(status.get("s_step", status.get("sstep"))),
                    "quant_done": as_int(status.get("quant_done")),
                    "quant_total": as_int(status.get("quant_total")),
                }
                source = Source.JSON

        if phase is None:  # regex fallback
            nums = train_progress(log_text)
            phase = _regex_phase(nums, unit.active)
            source = Source.REGEX

        eta_s, eta_note = eta_for(
            phase,
            step=nums["step"],
            total=nums["total"],
            sstep=nums["sstep"],
        )

        return JobState(
            job_type=JobType.TRAIN,
            phase=phase,
            model_info=mi,
            quant_done=nums["quant_done"],
            quant_total=nums["quant_total"],
            step=nums["step"],
            total=nums["total"],
            loss=nums["loss"],
            sstep=nums["sstep"],
            eta_seconds=eta_s,
            eta_note=eta_note,
            error_count=errs,
            elapsed_seconds=elapsed,
            unit_active=unit.active,
            unit_result=unit.result,
            source=source,
        )
