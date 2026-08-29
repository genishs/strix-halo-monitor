"""Tests for the scoring job parser: prep/scoring/finished, ETA, JSON vs regex."""

import unittest

import _util
from _util import load_log

from halo_monitor.config import config_from_env
from halo_monitor.jobs.base import UnitRef, parse_job
from halo_monitor.model import EtaNote, JobType, Phase, Source

CFG = config_from_env(env={})


def score(log, *, active="active", start=0, now=1000, name="gpujob-score-32b"):
    return parse_job(CFG, log, UnitRef(name=name, active=active, start_epoch=start), now)


class TestScoreParserRegex(unittest.TestCase):
    def test_routing_by_unit_name(self):
        js = score(load_log("score_running.log"))
        self.assertEqual(js.job_type, JobType.SCORE)

    def test_routing_grade_and_eval_unit_names(self):
        # Regression: the live eval job runs as gpujob-grade141b-* (not *score*), and
        # was mis-routed to the training parser -> stuck "quantizing". All eval aliases
        # must route here.
        for name in ("gpujob-grade141b-20260731-011405", "gpujob-eval-mixtral", "SCORE-x"):
            js = score(load_log("score_running.log"), name=name)
            self.assertEqual(js.job_type, JobType.SCORE, name)

    def test_scoring_phase_progress_and_eta(self):
        js = score(load_log("score_running.log"), start=200, now=200 + 3600)
        self.assertEqual(js.phase, Phase.SCORING)
        self.assertEqual(js.gen_done, 3)
        self.assertEqual(js.heldout_total, 7)  # default from config
        self.assertEqual(js.last_gen, "generated [task-charlie] 512 tokens")
        # ETA = elapsed(3600) * (7-3)/3 = 4800
        self.assertEqual(js.eta_seconds, 4800)
        self.assertEqual(js.eta_note, EtaNote.ROUGH_HIGH_VARIANCE)
        self.assertEqual(js.model_info.adapter, "coder32b-lora-v3")
        self.assertTrue(js.model_info.heldout)

    def test_score_prep_phase(self):
        js = score(load_log("score_prep.log"))
        self.assertEqual(js.phase, Phase.SCORE_PREP)
        self.assertEqual(js.quant_done, 120)
        self.assertEqual(js.eta_note, EtaNote.PRE_SCORING_PREP)

    def test_finished_first_when_inactive(self):
        # Scoring branch checks finished FIRST, even with generation markers present.
        js = score(load_log("score_running.log"), active="inactive")
        self.assertEqual(js.phase, Phase.FINISHED)
        self.assertEqual(js.unit_active, "inactive")

    def test_estimating_before_first_task(self):
        log = ("command --base /m/qwen2.5-coder-32b --heldout\n"
               "HQQ 스트리밍 치환 완료\n")  # replaced but nothing generated yet
        js = score(log, start=200, now=260)
        self.assertEqual(js.phase, Phase.SCORING)
        self.assertEqual(js.gen_done, 0)
        self.assertIsNone(js.eta_seconds)
        self.assertEqual(js.eta_note, EtaNote.ESTIMATING_FIRST_TASK)

    def test_custom_heldout_total_from_config(self):
        cfg = config_from_env(env={"HALO_HELDOUT_TOTAL": "12"})
        js = parse_job(cfg, load_log("score_running.log"),
                       UnitRef(name="gpujob-score-32b", active="active", start_epoch=0),
                       now=3600)
        self.assertEqual(js.heldout_total, 12)


class TestScoreParserJson(unittest.TestCase):
    def test_json_progress_preferred(self):
        js = score(load_log("score_running_json.log"), start=100, now=100 + 1800)
        self.assertEqual(js.source, Source.JSON)
        self.assertEqual(js.phase, Phase.SCORING)
        self.assertEqual(js.gen_done, 2)  # last HALOJSON line
        self.assertEqual(js.last_gen, "task-bravo")
        self.assertEqual(js.eta_seconds, int(1800 * (7 - 2) / 2))

    def test_json_but_inactive_still_finished(self):
        # systemd overlay wins for scoring finished; provenance becomes MIXED.
        js = score(load_log("score_running_json.log"), active="failed")
        self.assertEqual(js.phase, Phase.FINISHED)
        self.assertEqual(js.source, Source.MIXED)


if __name__ == "__main__":
    unittest.main()
