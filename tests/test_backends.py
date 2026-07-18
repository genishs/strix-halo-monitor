"""Tests for GpuBackend implementations (DESIGN §2.2 B, C5, Phase 2).

Covers what the collector tests only exercise indirectly: card discovery must
skip non-amdgpu cards (glob, not a hardcoded card number) and hwmon must be
matched by its ``name`` file content, not by hwmon number -- both fixture
trees deliberately include a decoy to prove this. Also covers the nvidia stub
and the amdgpu-vs-nvidia ``select_backend`` helper.
"""

import os
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.backends import select_backend
from halo_monitor.collectors.backends.amdgpu import AmdgpuBackend
from halo_monitor.collectors.backends.nvidia import NvidiaBackend

SYSFS = os.path.join(_util.FIXTURES, "sysfs")
SYSFS_NO_RAPL = os.path.join(_util.FIXTURES, "sysfs_no_rapl")


class TestAmdgpuBackend(unittest.TestCase):
    def test_detect_true_and_skips_decoy_card(self):
        # card0 is a decoy (vendor 0x8086, no mem_info_* files); card1 is amdgpu.
        backend = AmdgpuBackend(SYSFS)
        self.assertTrue(backend.detect())

    def test_detect_false_on_empty_root(self):
        empty_root = os.path.join(_util.FIXTURES, "sysfs_empty_root_does_not_exist")
        self.assertFalse(AmdgpuBackend(empty_root).detect())

    def test_mem_info(self):
        mem = AmdgpuBackend(SYSFS).mem_info()
        self.assertEqual(mem.gtt_total_bytes, 60129542144)
        self.assertEqual(mem.gtt_used_bytes, 51667468288)
        self.assertEqual(mem.vram_used_bytes, 889708544)

    def test_power_matches_hwmon_by_name_not_number(self):
        # fixture has hwmon0="k10temp" (decoy) and hwmon1="amdgpu" -- if the
        # backend matched by number it would find nothing at hwmon0.
        raw = AmdgpuBackend(SYSFS).power()
        self.assertAlmostEqual(raw.amdgpu_w, 85.043, places=3)
        self.assertIsNone(raw.pkg_uj)   # RAPL is not this backend's concern
        self.assertIsNone(raw.core_uj)

    def test_power_missing_hwmon_gives_none(self):
        empty_root = os.path.join(_util.FIXTURES, "sysfs_empty_root_does_not_exist")
        raw = AmdgpuBackend(empty_root).power()  # must not raise
        self.assertIsNone(raw.amdgpu_w)

    def test_clocks_parses_starred_level(self):
        clk = AmdgpuBackend(SYSFS).clocks()
        self.assertEqual(clk.sclk_mhz, 2846)

    def test_clocks_missing_pp_dpm_sclk_gives_none(self):
        clk = AmdgpuBackend(SYSFS_NO_RAPL).clocks()  # no pp_dpm_sclk in this fixture
        self.assertIsNone(clk.sclk_mhz)

    def test_never_raises_on_unreadable_root(self):
        backend = AmdgpuBackend("/dev/null/not/a/real/path")
        self.assertFalse(backend.detect())
        self.assertIsNone(backend.mem_info().gtt_used_bytes)
        self.assertIsNone(backend.power().amdgpu_w)
        self.assertIsNone(backend.clocks().sclk_mhz)


class TestNvidiaStub(unittest.TestCase):
    def test_always_absent(self):
        backend = NvidiaBackend(SYSFS)
        self.assertFalse(backend.detect())
        self.assertIsNone(backend.mem_info().gtt_used_bytes)
        self.assertIsNone(backend.power().amdgpu_w)
        self.assertIsNone(backend.clocks().sclk_mhz)


class _Ctx:
    def __init__(self, root):
        self.root = root


class TestSelectBackend(unittest.TestCase):
    def test_picks_amdgpu_when_present(self):
        backend = select_backend(_Ctx(SYSFS))
        self.assertEqual(backend.name, "amdgpu")

    def test_falls_back_to_nvidia_stub(self):
        empty_root = os.path.join(_util.FIXTURES, "sysfs_empty_root_does_not_exist")
        backend = select_backend(_Ctx(empty_root))
        self.assertEqual(backend.name, "nvidia")
        self.assertFalse(backend.detect())


if __name__ == "__main__":
    unittest.main()
