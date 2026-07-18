"""Parser tests against real (masked) box logs (DESIGN §2.2 C, O11).

Unlike the hand-written synthetic fixtures in test_train_parser.py /
test_score_parser.py, these two files are read-only-captured slices of actual
job logs from the live Strix Halo box (score: 123b-hqq2-seq512, train:
train123bfull), masked per tests/fixtures/logs/README.md before commit. They
carry no HALOJSON status line (the live ML scripts don't emit one yet — see
ADR-0002), so parsing here exercises the regex fallback path against the real
log dialect end to end, not just the synthetic approximations.
"""

import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.jobs.base import UnitRef, parse_job
from halo_monitor.model import Phase, Source


class TestRealScoreLog(unittest.TestCase):
    def setUp(self):
        self.cfg = config_from_env(env={})
        self.log = _util.load_log("real_score_123b.log")

    def test_scoring_phase_and_progress(self):
        unit = UnitRef(name="gpujob-score-123b-hqq2-seq512", active="active")
        js = parse_job(self.cfg, self.log, unit, now=1000.0)
        self.assertEqual(js.phase, Phase.SCORING)
        self.assertEqual(js.gen_done, 5)
        self.assertEqual(js.heldout_total, 7)  # Config default (no HALOJSON override)
        self.assertEqual(js.source, Source.REGEX)  # no HALOJSON line in the real log

    def test_model_info_from_real_command_line(self):
        unit = UnitRef(name="gpujob-score-123b-hqq2-seq512", active="active")
        js = parse_job(self.cfg, self.log, unit, now=1000.0)
        mi = js.model_info
        self.assertEqual(mi.base_bn, "mistral-large-2411")
        self.assertEqual(mi.base_label, "Mistral-Large 123B")  # label_map hit
        self.assertEqual(mi.nbits, 2)
        self.assertEqual(mi.max_new, 4096)
        self.assertTrue(mi.heldout)
        self.assertEqual(mi.adapter, "adapter-A")  # masked adapter dir, basename parsed

    def test_finished_when_unit_inactive(self):
        # score branch checks "finished" FIRST regardless of markers present.
        unit = UnitRef(name="gpujob-score-123b-hqq2-seq512", active="inactive")
        js = parse_job(self.cfg, self.log, unit, now=1000.0)
        self.assertEqual(js.phase, Phase.FINISHED)


class TestRealTrainLog(unittest.TestCase):
    def setUp(self):
        self.cfg = config_from_env(env={})
        self.log = _util.load_log("real_train_123b.log")

    def test_training_phase_and_progress(self):
        unit = UnitRef(name="gpujob-train123bfull", active="active")
        jt = parse_job(self.cfg, self.log, unit, now=1000.0)
        self.assertEqual(jt.phase, Phase.TRAINING)
        self.assertEqual(jt.step, 5)
        self.assertEqual(jt.total, 39)  # optim_steps≈39
        self.assertAlmostEqual(jt.loss, 1.9119, places=4)
        self.assertAlmostEqual(jt.sstep, 471.0, places=1)
        self.assertEqual(jt.source, Source.REGEX)

    def test_model_info_from_real_command_line(self):
        unit = UnitRef(name="gpujob-train123bfull", active="active")
        jt = parse_job(self.cfg, self.log, unit, now=1000.0)
        mi = jt.model_info
        self.assertEqual(mi.base_label, "Mistral-Large 123B")
        self.assertEqual(mi.nbits, 2)
        self.assertEqual(mi.seq, 512)
        self.assertEqual(mi.lora_r, 16)
        self.assertTrue(mi.lora_mlp)
        self.assertEqual(mi.epochs, 1)

    def test_training_quirk_survives_inactive_unit(self):
        # monitor.sh quirk (preserved on purpose): once step/total markers exist,
        # the last training line still shows even if the unit is no longer active.
        unit = UnitRef(name="gpujob-train123bfull", active="inactive")
        jt = parse_job(self.cfg, self.log, unit, now=1000.0)
        self.assertEqual(jt.phase, Phase.TRAINING)
        self.assertEqual(jt.step, 5)


if __name__ == "__main__":
    unittest.main()
