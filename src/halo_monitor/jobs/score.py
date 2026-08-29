"""Scoring job parser (DESIGN §2.2 C).

Mirrors monitor.sh's ``is_score`` branch: the finished check comes FIRST (an inactive
unit is terminal regardless of markers — unlike the training branch), then quant-only
=> SCORE_PREP, otherwise SCORING with linear-extrapolation ETA. A HALOJSON status line
refines the prep/scoring distinction (ADR-0002) but does not override the finished rule.
"""

from __future__ import annotations

from ..config import Config
from ..model import JobState, JobType, Phase, Source
from ..status_schema import parse_last_status
from . import base
from ._scrape import as_int, as_str, count_errors, eval_progress, score_progress
from .eta import eta_for
from .modelinfo import parse_model_info

_SCORE_PHASES = {Phase.IDLE, Phase.SCORE_PREP, Phase.SCORING, Phase.FINISHED}

#: Unit-name substrings that mark an eval/scoring/grading run (all handled by this
#: parser). The eval script (``eval_hard_tsc.py``) is launched under several unit
#: names over time — ``gpujob-score-*`` (legacy) and ``gpujob-grade*`` (current) —
#: so match all synonyms rather than the single literal ``score`` (the miss that
#: made ``gpujob-grade141b-*`` render as a stuck training job).
_EVAL_UNIT_MARKERS = ("score", "grade", "eval")


def _is_active(active: str | None) -> bool:
    return active == "active"


def _phase_or_none(s) -> Phase | None:
    try:
        return Phase(s)
    except ValueError:
        return None


@base.register
class ScoreParser:
    """Handles eval/scoring/grading units (registered before TrainParser).

    Matches unit names containing ``score``/``grade``/``eval`` — all the aliases the
    heldout eval job (``eval_hard_tsc.py``) has run under.
    """

    name = "score"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def matches(self, unit: base.UnitRef) -> bool:
        name = unit.name.lower()
        return any(marker in name for marker in _EVAL_UNIT_MARKERS)

    def parse(self, log_text: str, unit: base.UnitRef, now: float) -> JobState:
        mi = parse_model_info(log_text, self.cfg)
        errs = count_errors(log_text)
        elapsed = None
        if unit.start_epoch:
            elapsed = max(0, int(now - unit.start_epoch))

        scraped = score_progress(log_text)
        ev = eval_progress(log_text)
        status = parse_last_status(log_text)

        # JSON refinement (progress numbers + prep/scoring phase), if present.
        gen_done = scraped["gen_done"]
        heldout_total = self.cfg.heldout_total
        last_gen = scraped["last_gen"]
        quant_done = scraped["quant_done"]
        quant_total = scraped["quant_total"]
        json_phase: Phase | None = None
        source = Source.REGEX

        if status is not None and status.get("job", "score") == "score":
            jp = _phase_or_none(status.get("phase"))
            if jp in _SCORE_PHASES:
                json_phase = jp
                gen_done = as_int(status.get("gen_done"))
                if gen_done is None:
                    gen_done = scraped["gen_done"]
                ht = as_int(status.get("heldout_total"))
                if ht is not None:
                    heldout_total = ht
                lg = as_str(status.get("last_gen"))
                if lg is not None:
                    last_gen = lg
                qd = as_int(status.get("quant_done"))
                if qd is not None:
                    quant_done = qd
                    quant_total = as_int(status.get("quant_total"))
                source = Source.JSON

        # Phase resolution — finished FIRST (monitor.sh scoring branch ordering).
        if not _is_active(unit.active):
            phase = Phase.FINISHED
            # provenance: systemd overlay on top of whichever source we had.
            source = Source.MIXED if source is Source.JSON else Source.REGEX
        elif json_phase is not None:
            phase = json_phase
        elif scraped["replaced"] == 0 and scraped["gen_done"] == 0 and quant_done is not None:
            phase = Phase.SCORE_PREP
        else:
            phase = Phase.SCORING

        eta_s, eta_note = eta_for(
            phase,
            gen_done=gen_done,
            heldout_total=heldout_total,
            elapsed_s=elapsed,
        )

        return JobState(
            job_type=JobType.SCORE,
            phase=phase,
            model_info=mi,
            quant_done=quant_done,
            quant_total=quant_total,
            gen_done=gen_done,
            heldout_total=heldout_total,
            last_gen=last_gen,
            cur_task=ev["cur_task"],
            gen_tokens=ev["gen_tokens"],
            eval_compiling=ev["compiling"],
            eval_score=ev["eval_score"],
            eval_max=ev["eval_max"],
            eval_pct=ev["eval_pct"],
            eval_clean=ev["eval_clean"],
            eta_seconds=eta_s,
            eta_note=eta_note,
            error_count=errs,
            elapsed_seconds=elapsed,
            unit_name=unit.name,
            unit_active=unit.active,
            unit_result=unit.result,
            source=source,
        )
