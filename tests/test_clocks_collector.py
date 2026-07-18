"""Tests for ClockCollector against sysfs fixtures (DESIGN §2.2 B, Phase 2).

ClockCollector is a thin delegate to the GpuBackend, which owns the
pp_dpm_sclk-vs-rocm-smi decision (see test_amdgpu_backend.py for the parsing
detail). Confirms the collector itself degrades gracefully with no backend or
a backend whose pp_dpm_sclk is absent.
"""

import os
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.backends.amdgpu import AmdgpuBackend
from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.clocks import ClockCollector
from halo_monitor.config import config_from_env

SYSFS = os.path.join(_util.FIXTURES, "sysfs")
SYSFS_NO_RAPL = os.path.join(_util.FIXTURES, "sysfs_no_rapl")  # also has no pp_dpm_sclk


def _ctx(root: str, backend):
    cfg = config_from_env(env={})
    return CollectContext(cfg=cfg, backend=backend, root=root)


class TestClockCollector(unittest.TestCase):
    def test_reads_starred_level_from_backend(self):
        ctx = _ctx(SYSFS, AmdgpuBackend(SYSFS))
        clk = ClockCollector().collect(ctx)
        self.assertEqual(clk.sclk_mhz, 2846)  # "1: 2846Mhz *" in the fixture

    def test_no_backend_gives_none_not_raise(self):
        ctx = _ctx(SYSFS, backend=None)
        clk = ClockCollector().collect(ctx)
        self.assertIsNone(clk.sclk_mhz)

    def test_pp_dpm_sclk_missing_degrades_gracefully(self):
        ctx = _ctx(SYSFS_NO_RAPL, AmdgpuBackend(SYSFS_NO_RAPL))
        clk = ClockCollector().collect(ctx)  # must not raise
        self.assertIsNone(clk.sclk_mhz)


if __name__ == "__main__":
    unittest.main()
