"""Tests for ETA strategies (jobs/eta.py)."""

import unittest

import _util  # noqa: F401

from halo_monitor.jobs.eta import eta_for, scoring_eta, training_eta
from halo_monitor.model import EtaNote, Phase


class TestEta(unittest.TestCase):
    def test_training_eta(self):
        self.assertEqual(training_eta(18, 39, 12.5), 262)
        self.assertIsNone(training_eta(None, 39, 12.5))
        self.assertIsNone(training_eta(18, 39, None))
        self.assertEqual(training_eta(39, 39, 12.5), 0)  # clamp non-negative

    def test_scoring_eta(self):
        secs, note = scoring_eta(3, 7, 3600)
        self.assertEqual(secs, 4800)
        self.assertEqual(note, EtaNote.ROUGH_HIGH_VARIANCE)

    def test_scoring_eta_before_first_task(self):
        secs, note = scoring_eta(0, 7, 100)
        self.assertIsNone(secs)
        self.assertEqual(note, EtaNote.ESTIMATING_FIRST_TASK)

    def test_dispatch_notes(self):
        self.assertEqual(eta_for(Phase.QUANTIZING)[1], EtaNote.PRE_TRAINING_PREP)
        self.assertEqual(eta_for(Phase.FIRST_STEP)[1], EtaNote.FIRST_STEP_WARMUP)
        self.assertEqual(eta_for(Phase.EVAL_SAVE)[1], EtaNote.BEYOND_STEP_COUNT)
        self.assertEqual(eta_for(Phase.SCORE_PREP)[1], EtaNote.PRE_SCORING_PREP)
        self.assertEqual(eta_for(Phase.IDLE), (None, None))
        self.assertEqual(eta_for(Phase.FINISHED), (None, None))


if __name__ == "__main__":
    unittest.main()
