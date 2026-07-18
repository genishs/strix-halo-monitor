"""Tests for the training job parser: phase transitions, ETA, JSON vs regex."""

import unittest

import _util
from _util import load_log

from halo_monitor.config import config_from_env
from halo_monitor.jobs.base import UnitRef, parse_job
from halo_monitor.model import EtaNote, JobType, Phase, Source

CFG = config_from_env(env={})


def train(log, *, active="active", start=0, now=1000, name="gpujob-train-72b"):
    return parse_job(CFG, log, UnitRef(name=name, active=active, start_epoch=start), now)


class TestTrainParserRegex(unittest.TestCase):
    def test_training_phase_and_eta(self):
        js = train(load_log("train_running.log"))
        self.assertEqual(js.job_type, JobType.TRAIN)
        self.assertEqual(js.phase, Phase.TRAINING)
        self.assertEqual(js.step, 18)
        self.assertEqual(js.total, 39)
        self.assertAlmostEqual(js.loss, 0.60)
        self.assertAlmostEqual(js.sstep, 12.5)
        # ETA = (39-18)*12.5 = 262.5 -> int 262 (monitor.sh math)
        self.assertEqual(js.eta_seconds, 262)
        self.assertEqual(js.source, Source.REGEX)
        self.assertEqual(js.model_info.base_label, "Qwen2.5 72B")

    def test_quantizing_phase(self):
        js = train(load_log("train_quant.log"))
        self.assertEqual(js.phase, Phase.QUANTIZING)
        self.assertEqual(js.quant_done, 300)
        self.assertEqual(js.quant_total, 616)
        self.assertEqual(js.eta_note, EtaNote.PRE_TRAINING_PREP)
        self.assertIsNone(js.eta_seconds)

    def test_first_step_phase(self):
        js = train(load_log("train_first_step.log"))
        self.assertEqual(js.phase, Phase.FIRST_STEP)
        self.assertEqual(js.total, 120)
        self.assertIsNone(js.step)
        self.assertEqual(js.eta_note, EtaNote.FIRST_STEP_WARMUP)

    def test_eval_save_when_last_step_and_active(self):
        log = ("command --base /m/qwen2.5-72b-instruct\noptim_steps≈39\n"
               "step 39 | loss(avg8) 0.31  12.0s/step\n")
        js = train(log, active="active")
        self.assertEqual(js.phase, Phase.EVAL_SAVE)
        self.assertEqual(js.eta_note, EtaNote.BEYOND_STEP_COUNT)

    def test_last_step_but_inactive_is_training_line_parity(self):
        # monitor.sh quirk: with markers present it shows the training line even when
        # the unit is not active (eval_save needs active; finished is only reached with
        # no markers). We preserve that parity.
        log = ("command --base /m/qwen2.5-72b-instruct\noptim_steps≈39\n"
               "step 39 | loss(avg8) 0.31  12.0s/step\n")
        js = train(log, active="inactive")
        self.assertEqual(js.phase, Phase.TRAINING)

    def test_finished_when_no_markers_and_inactive(self):
        js = train("command --base /m/qwen2.5-72b-instruct\nsome unparsed line\n",
                   active="inactive")
        self.assertEqual(js.phase, Phase.FINISHED)

    def test_idle_when_active_and_no_markers(self):
        js = train("command --base /m/qwen2.5-72b-instruct\n", active="active")
        self.assertEqual(js.phase, Phase.IDLE)

    def test_error_count(self):
        log = ("command --base /m/x\nstep 3 | loss(avg8) 1.0 5s/step\n"
               "Traceback (most recent call last):\nRuntimeError: hipErrorOutOfMemory\n")
        js = train(log)
        self.assertGreaterEqual(js.error_count, 2)  # Traceback + hipError

    def test_elapsed_from_start_epoch(self):
        js = train(load_log("train_running.log"), start=400, now=1000)
        self.assertEqual(js.elapsed_seconds, 600)


class TestTrainParserJson(unittest.TestCase):
    def test_json_preferred_over_regex(self):
        js = train(load_log("train_running_json.log"))
        self.assertEqual(js.source, Source.JSON)
        self.assertEqual(js.phase, Phase.TRAINING)
        self.assertEqual(js.step, 18)  # last HALOJSON line
        self.assertEqual(js.total, 39)
        self.assertEqual(js.eta_seconds, int((39 - 18) * 12.5))

    def test_malformed_json_falls_back_to_regex(self):
        log = (load_log("train_running.log")
               + "HALOJSON {this is not valid json}\n")
        js = train(log)
        self.assertEqual(js.source, Source.REGEX)
        self.assertEqual(js.phase, Phase.TRAINING)

    def test_unknown_schema_version_ignored(self):
        log = (load_log("train_running.log")
               + 'HALOJSON {"v":999,"job":"train","phase":"training","step":5,"total":39}\n')
        js = train(log)
        self.assertEqual(js.source, Source.REGEX)
        self.assertEqual(js.step, 18)  # regex value, not the v999 line

    def test_s_step_canonical_and_sstep_alias(self):
        # Converged schema (ADR-0002) uses s_step; legacy sstep still accepted.
        canonical = ('HALOJSON {"v":1,"job":"train","phase":"training",'
                     '"step":10,"total":30,"loss":0.5,"s_step":20.0}\n')
        legacy = ('HALOJSON {"v":1,"job":"train","phase":"training",'
                  '"step":10,"total":30,"loss":0.5,"sstep":20.0}\n')
        for log in (canonical, legacy):
            js = train(log)
            self.assertEqual(js.sstep, 20.0)
            self.assertEqual(js.eta_seconds, int((30 - 10) * 20.0))  # 400


if __name__ == "__main__":
    unittest.main()
