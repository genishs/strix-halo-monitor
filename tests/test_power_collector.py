"""Tests for PowerCollector against sysfs fixtures (DESIGN §2.2 B, Phase 2).

PowerCollector must return only *raw* readings (energy counters + their
wraparound bound, and the amdgpu hwmon instantaneous watts) — no watts math,
no deltas. That belongs to loop.py (see tests/test_loop.py for the delta
math). The sysfs_no_rapl fixture exercises the "RAPL sysfs entirely absent"
case: PowerCollector must degrade to None fields, never raise.
"""

import os
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.backends.amdgpu import AmdgpuBackend
from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.power import PowerCollector
from halo_monitor.config import config_from_env

SYSFS = os.path.join(_util.FIXTURES, "sysfs")
SYSFS_NO_RAPL = os.path.join(_util.FIXTURES, "sysfs_no_rapl")


def _ctx(root: str, backend):
    cfg = config_from_env(env={})
    return CollectContext(cfg=cfg, backend=backend, root=root)


class TestPowerCollector(unittest.TestCase):
    def test_full_fixture_raw_readings(self):
        ctx = _ctx(SYSFS, AmdgpuBackend(SYSFS))
        raw = PowerCollector().collect(ctx)
        self.assertEqual(raw.pkg_uj, 64930873566)
        self.assertEqual(raw.core_uj, 31041919121)
        self.assertEqual(raw.pkg_max_uj, 65532610987)
        self.assertEqual(raw.core_max_uj, 65532610987)
        self.assertAlmostEqual(raw.amdgpu_w, 85.043, places=3)

    def test_no_backend_no_amdgpu_watts(self):
        ctx = _ctx(SYSFS, backend=None)
        raw = PowerCollector().collect(ctx)
        self.assertIsNone(raw.amdgpu_w)
        self.assertEqual(raw.pkg_uj, 64930873566)  # RAPL independent of GPU backend

    def test_rapl_missing_degrades_gracefully(self):
        ctx = _ctx(SYSFS_NO_RAPL, AmdgpuBackend(SYSFS_NO_RAPL))
        raw = PowerCollector().collect(ctx)  # must not raise
        self.assertIsNone(raw.pkg_uj)
        self.assertIsNone(raw.core_uj)
        self.assertIsNone(raw.pkg_max_uj)
        self.assertIsNone(raw.core_max_uj)
        self.assertAlmostEqual(raw.amdgpu_w, 42.0, places=3)  # amdgpu hwmon still works

    def test_stateless_no_watts_field(self):
        # RawPower has no watts/rate field at all -- collector cannot compute one.
        ctx = _ctx(SYSFS, AmdgpuBackend(SYSFS))
        raw = PowerCollector().collect(ctx)
        self.assertFalse(hasattr(raw, "total_w"))
        self.assertFalse(hasattr(raw, "gpu_w"))


if __name__ == "__main__":
    unittest.main()
