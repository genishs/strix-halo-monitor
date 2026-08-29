"""Tests for the eval/grading widget: parser detail, loop observation, render.

Covers the Phase-5 grading feature end to end without hardware:
  * the eval-detail scrape (task name, cumulative tokens, compile stage, final score),
  * the loop's *observed* throughput/ETA (timed across ticks because the eval log has
    no per-line timestamps), including the "don't measure a rate we didn't see start"
    honesty guard, and
  * the additive Eval widget render (generating / compiling / finished, ko+en) and its
    additive property (absent -> unchanged legacy frame).
"""

import time
import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.jobs.base import UnitRef
from halo_monitor.jobs.score import ScoreParser
from halo_monitor.model import (
    ClockStats, EvalPhase, EvalProgress, JobType, MemoryStats, Phase, PowerStats,
    RawPower, Snapshot,
)
from halo_monitor.loop import UpdateLoop
from halo_monitor.ui import widgets
from halo_monitor.ui.render import render_frame
from halo_monitor.ui.theme import DEFAULT

CFG = config_from_env(env={})
CFG_EN = config_from_env(env={"HALO_LANG": "en"})

_CMD = ("command : python scripts/eval_hard_tsc.py --adapter models/mix-hqq2-full "
        "--label mixtral141b-heldout7 --heldout "
        "--base /run/media/user/새 볼륨/mixtral-8x22b-v0.1 --hqq-nbits 2\n")
_REPLACED = "HQQ 스트리밍 치환 완료: Linear 1624개\n"


def _gen(name, new):
    # eval_hard_tsc format, with the 18-wide left-justified name pad.
    return f"  generated [{name:18s}] {800:5d} chars (in=1200tok new={new})\n"


def _parse(log, *, active="active", name="gpujob-grade141b-x", now=1000, start=1):
    return ScoreParser(CFG).parse(log, UnitRef(name=name, active=active, start_epoch=start), now)


# --------------------------------------------------------------------------- #
# Parser: eval-specific fields
# --------------------------------------------------------------------------- #
class TestEvalParserDetail(unittest.TestCase):
    def test_eval_label_and_generation_detail(self):
        log = _CMD + _REPLACED + _gen("pyexpr_eval", 512) + _gen("ho_admin_medit", 640)
        js = _parse(log)
        self.assertEqual(js.job_type, JobType.SCORE)
        self.assertEqual(js.phase, Phase.SCORING)
        self.assertEqual(js.gen_done, 2)
        self.assertEqual(js.cur_task, "ho_admin_medit")       # last generated, cleaned
        self.assertEqual(js.gen_tokens, 512 + 640)            # cumulative new tokens
        self.assertEqual(js.model_info.eval_label, "mixtral141b-heldout7")

    def test_compiling_and_final_score(self):
        log = (_CMD + _REPLACED
               + "".join(_gen(f"t{i}", 500) for i in range(7))
               + "  running tsc (파일별 단독컴파일) ...\n"
               + "[t0                ] CLEAN    score=1.00  errs=0 \n"
               + "\n[mixtral141b-heldout7] CLEAN 5/7 compiles | total_errors=4 | "
                 "SCORE 5.50/7 = 78.6%\n"
               + "saved → eval_results/mixtral141b-heldout7.json\n")
        js = _parse(log)
        self.assertTrue(js.eval_compiling)
        self.assertEqual(js.gen_done, 7)
        self.assertEqual(js.eval_score, 5.5)
        self.assertEqual(js.eval_max, 7)
        self.assertEqual(js.eval_pct, 78.6)
        self.assertEqual(js.eval_clean, 5)

    def test_prep_has_no_eval_detail(self):
        log = _CMD + "120/1624 Linear 양자화 완료\n"   # quant only, no replaced/generated
        js = _parse(log)
        self.assertEqual(js.phase, Phase.SCORE_PREP)
        self.assertIsNone(js.cur_task)
        self.assertIsNone(js.gen_tokens)


# --------------------------------------------------------------------------- #
# Loop: observed throughput + ETA
# --------------------------------------------------------------------------- #
class _Fake:
    def __init__(self, v):
        self.v = v
        self.name = "f"

    def available(self, ctx):
        return True

    def collect(self, ctx):
        return self.v


class _LogProvider:
    """Grows a log across ticks and parses it as a grade unit each call."""

    def __init__(self, log="", active="active"):
        self.log = log
        self.active = active

    def __call__(self, now):
        return ScoreParser(CFG).parse(
            self.log, UnitRef(name="gpujob-grade141b-x", active=self.active, start_epoch=1), now)


def _loop(provider):
    return UpdateLoop(
        CFG, backend=None, memory=_Fake(MemoryStats()), power=_Fake(RawPower()),
        clocks=_Fake(ClockStats()), disk=_Fake([]), network=_Fake([]),
        job_provider=provider, renderer=lambda s: None,
    )


class TestEvalObservation(unittest.TestCase):
    def test_observed_tok_s_and_eta_from_zero(self):
        prov = _LogProvider(_CMD + _REPLACED)          # generation just started, 0 tasks
        loop = _loop(prov)
        s1 = loop.tick(0.0, 1000.0)
        self.assertIsNotNone(s1.eval)
        self.assertEqual(s1.eval.phase, EvalPhase.GENERATING)
        self.assertIsNone(s1.eval.tok_s)               # nothing generated yet
        self.assertIsNone(s1.eval.eta_s)               # estimating before first task
        # 60s later: 3 tasks, 1536 tokens
        prov.log += _gen("t0", 512) + _gen("t1", 512) + _gen("t2", 512)
        s2 = loop.tick(60.0, 1060.0)
        self.assertEqual(s2.eval.done, 3)
        self.assertAlmostEqual(s2.eval.tok_s, 1536 / 60.0)     # observed avg (from zero)
        self.assertEqual(s2.eval.eta_s, int(60 * (7 - 3) / 3))  # 80s, generation-scoped
        # main ETA line is kept consistent with the observed value
        self.assertEqual(s2.job.eta_seconds, 80)

    def test_tok_s_suppressed_when_generation_underway_on_first_tick(self):
        # Monitor starts mid-generation (already 2 tasks): we can't honestly time a rate.
        prov = _LogProvider(_CMD + _REPLACED + _gen("t0", 512) + _gen("t1", 512))
        loop = _loop(prov)
        loop.tick(0.0, 1000.0)
        prov.log += _gen("t2", 512)
        s = loop.tick(30.0, 1030.0)
        self.assertEqual(s.eval.done, 3)
        self.assertIsNone(s.eval.tok_s)                # not measurable -> honest None
        self.assertIsNotNone(s.eval.eta_s)             # ETA still offered (rough)

    def test_no_widget_during_prep(self):
        prov = _LogProvider(_CMD + "60/1624 Linear 양자화 완료\n")
        s = _loop(prov).tick(0.0, 1000.0)
        self.assertEqual(s.job.phase, Phase.SCORE_PREP)
        self.assertIsNone(s.eval)                      # prep -> main line only

    def test_finished_shows_score_no_eta(self):
        log = (_CMD + _REPLACED + "".join(_gen(f"t{i}", 500) for i in range(7))
               + "\n[mixtral141b-heldout7] CLEAN 5/7 compiles | total_errors=4 | "
                 "SCORE 5.50/7 = 78.6%\nsaved → eval_results/x.json\n")
        prov = _LogProvider(log, active="inactive")
        s = _loop(prov).tick(0.0, 1000.0)
        self.assertEqual(s.eval.phase, EvalPhase.FINISHED)
        self.assertEqual(s.eval.score, 5.5)
        self.assertIsNone(s.eval.eta_s)

    def test_non_score_job_clears_eval(self):
        # A training job must not produce an eval widget.
        def prov(now):
            from halo_monitor.jobs.train import TrainParser
            return TrainParser(CFG).parse("optim_steps≈100\nstep 5 |\n",
                                          UnitRef(name="gpujob-train-x", active="active"), now)
        s = _loop(prov).tick(0.0, 1000.0)
        self.assertIsNone(s.eval)


# --------------------------------------------------------------------------- #
# Render: additive Eval widget
# --------------------------------------------------------------------------- #
def _snap(ev):
    from halo_monitor.model import JobState
    return Snapshot(ts=0.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151",
                    job=JobState(job_type=JobType.SCORE), memory=MemoryStats(),
                    power=PowerStats(), clocks=ClockStats(), eval=ev)


def _lt(t):
    return time.struct_time((2026, 7, 31, 12, 0, 0, 3, 212, -1))


def _frame(ev, cfg):
    return render_frame(_snap(ev), cfg, localtime=_lt).split("\n")


class TestEvalRender(unittest.TestCase):
    def test_absent_eval_leaves_legacy_frame(self):
        lines = _frame(None, CFG)
        self.assertEqual(len(lines), 12)
        self.assertNotIn("평가", "\n".join(lines))

    def test_generating_line_ko(self):
        ev = EvalProgress(label="mixtral141b-heldout7", done=3, total=7, cur_task="pyexpr_eval",
                          phase=EvalPhase.GENERATING, tok_s=25.6, eta_s=80,
                          eta_note=None)
        line = _frame(ev, CFG)[12]
        self.assertTrue(line.startswith("   ★평가:"))
        self.assertIn("태스크 3/7", line)
        self.assertIn("현재 pyexpr_eval", line)
        self.assertIn("25.6 tok/s", line)
        self.assertIn("ETA 0h01m20s (관측)", line)

    def test_generating_line_en(self):
        ev = EvalProgress(done=3, total=7, cur_task="pyexpr_eval",
                          phase=EvalPhase.GENERATING, tok_s=25.6, eta_s=80)
        line = _frame(ev, CFG_EN)[12]
        self.assertIn("task 3/7", line)
        self.assertIn("current pyexpr_eval", line)
        self.assertIn("ETA 0h01m20s (observed)", line)

    def test_estimating_and_unmeasurable(self):
        ev = EvalProgress(done=0, total=7, phase=EvalPhase.GENERATING, tok_s=None, eta_s=None)
        line = _frame(ev, CFG)[12]
        self.assertIn("— tok/s", line)                 # not measurable
        self.assertIn("ETA 산정 대기", line)             # estimating

    def test_compiling_line(self):
        ev = EvalProgress(done=7, total=7, phase=EvalPhase.COMPILING, tok_s=25.6)
        line = _frame(ev, CFG)[12]
        self.assertIn("태스크 7/7", line)
        self.assertIn("컴파일·채점 중", line)

    def test_finished_line(self):
        ev = EvalProgress(done=7, total=7, phase=EvalPhase.FINISHED,
                          score=5.5, max=7, pct=78.6, clean=5)
        ko = _frame(ev, CFG)[12]
        en = _frame(ev, CFG_EN)[12]
        self.assertIn("완료 7/7", ko)
        self.assertIn("점수 5.50/7 = 78.6%", ko)
        self.assertIn("done 7/7", en)
        self.assertIn("score 5.50/7 = 78.6%", en)


if __name__ == "__main__":
    unittest.main()
