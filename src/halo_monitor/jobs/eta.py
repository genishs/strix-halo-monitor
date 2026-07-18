"""ETA estimation strategies per job phase (DESIGN §2.2 C, eta.py).

Returns *structured* ETA — ``(eta_seconds, EtaNote|None)`` — never formatted text.
The renderer turns seconds into ``HhMMmSSs`` / a wall-clock done time and translates
the note key. This mirrors monitor.sh's ETA math while keeping i18n out of jobs.
"""

from __future__ import annotations

from ..model import EtaNote, Phase


def training_eta(step: int | None, total: int | None, sstep: float | None) -> int | None:
    """Remaining seconds = (total - step) * sstep  (monitor.sh training branch)."""
    if step is None or total is None or sstep is None:
        return None
    rem = int((total - step) * sstep)
    return rem if rem >= 0 else 0


def scoring_eta(
    gen_done: int | None,
    heldout_total: int | None,
    elapsed_s: int | None,
) -> tuple[int | None, EtaNote]:
    """Linear extrapolation: elapsed * (total - done) / done  (monitor.sh scoring).

    Returns (seconds, note). Before the first task completes, seconds is None and the
    note signals that we're still estimating.
    """
    if (
        gen_done is not None
        and gen_done >= 1
        and heldout_total is not None
        and elapsed_s is not None
        and elapsed_s > 0
    ):
        rem = int(elapsed_s * (heldout_total - gen_done) / gen_done)
        return (rem if rem >= 0 else 0), EtaNote.ROUGH_HIGH_VARIANCE
    return None, EtaNote.ESTIMATING_FIRST_TASK


def eta_for(
    phase: Phase,
    *,
    step: int | None = None,
    total: int | None = None,
    sstep: float | None = None,
    gen_done: int | None = None,
    heldout_total: int | None = None,
    elapsed_s: int | None = None,
) -> tuple[int | None, EtaNote | None]:
    """Dispatch ETA computation by resolved phase (works for JSON and regex sources)."""
    if phase is Phase.TRAINING:
        return training_eta(step, total, sstep), None
    if phase is Phase.EVAL_SAVE:
        return None, EtaNote.BEYOND_STEP_COUNT
    if phase is Phase.FIRST_STEP:
        return None, EtaNote.FIRST_STEP_WARMUP
    if phase is Phase.QUANTIZING:
        return None, EtaNote.PRE_TRAINING_PREP
    if phase is Phase.SCORING:
        return scoring_eta(gen_done, heldout_total, elapsed_s)
    if phase is Phase.SCORE_PREP:
        return None, EtaNote.PRE_SCORING_PREP
    return None, None
