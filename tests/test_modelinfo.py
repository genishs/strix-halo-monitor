"""Tests for command-line -> ModelInfo parsing (jobs/modelinfo.py)."""

import unittest

import _util  # noqa: F401  (sets up sys.path)

from halo_monitor.config import Config, config_from_env
from halo_monitor.jobs.modelinfo import parse_command, parse_model_info


class TestModelInfo(unittest.TestCase):
    def setUp(self):
        self.cfg = config_from_env(env={})

    def test_training_command_full(self):
        cmd = ("command python train_directml.py --base /models/qwen2.5-72b-instruct "
               "--hqq-nbits 4 --seq 4096 --lora-r 32 --lora-mlp --epochs 2")
        mi = parse_command(cmd, self.cfg)
        self.assertEqual(mi.base_raw, "/models/qwen2.5-72b-instruct")
        self.assertEqual(mi.base_bn, "qwen2.5-72b-instruct")
        self.assertEqual(mi.base_label, "Qwen2.5 72B")  # mapped via label_map
        self.assertEqual(mi.nbits, 4)
        self.assertEqual(mi.seq, 4096)
        self.assertEqual(mi.lora_r, 32)
        self.assertTrue(mi.lora_mlp)
        self.assertEqual(mi.epochs, 2)
        self.assertIsNone(mi.max_new)
        self.assertFalse(mi.heldout)

    def test_scoring_command(self):
        cmd = ("command python eval_hard_tsc.py --base /models/qwen2.5-coder-32b "
               "--hqq-nbits 4 --adapter /adapters/coder32b-lora-v3 --heldout --max-new 512")
        mi = parse_command(cmd, self.cfg)
        self.assertEqual(mi.base_label, "Qwen2.5-Coder 32B")
        self.assertEqual(mi.adapter, "coder32b-lora-v3")  # basename only
        self.assertTrue(mi.heldout)
        self.assertEqual(mi.max_new, 512)
        self.assertIsNone(mi.seq)
        self.assertFalse(mi.lora_mlp)

    def test_base_path_with_spaces(self):
        # Regression: --base value on the box is "/run/media/user/새 볼륨/<model>". A
        # naive \S+ capture truncated at the space -> basename "새". The value must be
        # taken whole (up to the next --flag), so the real model name survives.
        cmd = ("command : python train_directml.py "
               "--base /run/media/user/새 볼륨/mistral-large-2411 "
               "--quant hqq --hqq-nbits 2 --seq 512")
        mi = parse_command(cmd, self.cfg)
        self.assertEqual(mi.base_raw, "/run/media/user/새 볼륨/mistral-large-2411")
        self.assertEqual(mi.base_bn, "mistral-large-2411")
        self.assertEqual(mi.base_label, "Mistral-Large 123B")  # mapped, not "새"
        self.assertEqual(mi.nbits, 2)
        self.assertEqual(mi.seq, 512)

    def test_base_path_with_spaces_at_end_of_line(self):
        # --base is the final argument (no trailing --flag): capture to end of line.
        mi = parse_command("command --base /run/media/user/새 볼륨/mixtral-8x22b-v0.1", self.cfg)
        self.assertEqual(mi.base_raw, "/run/media/user/새 볼륨/mixtral-8x22b-v0.1")
        self.assertEqual(mi.base_bn, "mixtral-8x22b-v0.1")

    def test_adapter_path_with_spaces(self):
        cmd = ("command eval_hard_tsc.py --adapter /run/media/user/새 볼륨/coder-lora "
               "--heldout --label run7")
        mi = parse_command(cmd, self.cfg)
        self.assertEqual(mi.adapter, "coder-lora")   # basename of the spaced path
        self.assertEqual(mi.eval_label, "run7")
        self.assertTrue(mi.heldout)

    def test_unmapped_base_falls_back_to_basename(self):
        mi = parse_command("command --base /x/some-new-model-7b", self.cfg)
        self.assertEqual(mi.base_label, "some-new-model-7b")

    def test_custom_label_map_override(self):
        cfg = Config(label_map={"some-new-model-7b": "Shiny 7B"})
        mi = parse_command("command --base /x/some-new-model-7b", cfg)
        self.assertEqual(mi.base_label, "Shiny 7B")

    def test_empty_and_missing(self):
        self.assertIsNone(parse_command(None, self.cfg).base_raw)
        self.assertIsNone(parse_command("", self.cfg).base_label)
        mi = parse_command("command python foo.py --unrelated 3", self.cfg)
        self.assertIsNone(mi.base_raw)
        self.assertIsNone(mi.nbits)

    def test_no_prefix_false_match(self):
        # A longer option that merely contains "--seq" must not match --seq.
        mi = parse_command("command --sequence-len 8", self.cfg)
        self.assertIsNone(mi.seq)

    def test_parse_from_full_log(self):
        log = ("noise line\n"
               "command python train_directml.py --base /m/qwen2.5-coder-14b --hqq-nbits 4\n"
               "more noise\n")
        mi = parse_model_info(log, self.cfg)
        self.assertEqual(mi.base_label, "Qwen2.5-Coder 14B")
        self.assertEqual(mi.nbits, 4)


if __name__ == "__main__":
    unittest.main()
