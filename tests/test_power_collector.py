"""Tests for PowerCollector against sysfs fixtures (DESIGN §2.2 B, Phase 2).

PowerCollector must return only *raw* readings (energy counters + their
wraparound bound, and the amdgpu hwmon instantaneous watts) — no watts math,
no deltas. That belongs to loop.py (see tests/test_loop.py for the delta
math). The sysfs_no_rapl fixture exercises the "RAPL sysfs entirely absent"
case: PowerCollector must degrade to None fields, never raise.
"""

import os
import shutil
import tempfile
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.backends.amdgpu import AmdgpuBackend
from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.power import PowerCollector
from halo_monitor.config import config_from_env

_STATIC_SYSFS = os.path.join(_util.FIXTURES, "sysfs")
SYSFS_NO_RAPL = os.path.join(_util.FIXTURES, "sysfs_no_rapl")

# RAPL powercap domains use a literal ':' in the directory name
# (`intel-rapl:0`, `intel-rapl:0:0`), matching the real Linux sysfs layout
# that PowerCollector reads. NTFS reserves ':' as its Alternate Data Stream
# separator and refuses to create such a path at all -- not just on `git
# checkout`, but even via a plain os.makedirs() at test run time. So these
# four files cannot be committed as static fixtures (issue #7): any checkout
# that happens to live on an NTFS-backed volume (Windows, or a dual-boot
# shared NTFS mount on Linux) silently loses them. Instead we synthesize them
# into a throwaway tempfile.TemporaryDirectory() -- which lands on the
# system's real temp filesystem, not the checkout's -- for the duration of
# this test class only.
_RAPL_FILES = {
    ("intel-rapl:0", "energy_uj"): "64930873566",
    ("intel-rapl:0", "max_energy_range_uj"): "65532610987",
    ("intel-rapl:0:0", "energy_uj"): "31041919121",
    ("intel-rapl:0:0", "max_energy_range_uj"): "65532610987",
}


def _build_full_sysfs_root(tmp_dir: str) -> str:
    """Copy the static (colon-free) SYSFS fixture tree into tmp_dir, then add
    the RAPL powercap directories on top of it. Returns the merged root."""
    root = os.path.join(tmp_dir, "sysfs")
    shutil.copytree(_STATIC_SYSFS, root)
    powercap = os.path.join(root, "sys", "class", "powercap")
    for (domain, filename), content in _RAPL_FILES.items():
        domain_dir = os.path.join(powercap, domain)
        os.makedirs(domain_dir, exist_ok=True)
        with open(os.path.join(domain_dir, filename), "w", encoding="utf-8") as fh:
            fh.write(content + "\n")
    return root


def _ctx(root: str, backend):
    cfg = config_from_env(env={})
    return CollectContext(cfg=cfg, backend=backend, root=root)


class TestPowerCollector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.SYSFS = _build_full_sysfs_root(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_full_fixture_raw_readings(self):
        ctx = _ctx(self.SYSFS, AmdgpuBackend(self.SYSFS))
        raw = PowerCollector().collect(ctx)
        self.assertEqual(raw.pkg_uj, 64930873566)
        self.assertEqual(raw.core_uj, 31041919121)
        self.assertEqual(raw.pkg_max_uj, 65532610987)
        self.assertEqual(raw.core_max_uj, 65532610987)
        self.assertAlmostEqual(raw.amdgpu_w, 85.043, places=3)

    def test_no_backend_no_amdgpu_watts(self):
        ctx = _ctx(self.SYSFS, backend=None)
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
        ctx = _ctx(self.SYSFS, AmdgpuBackend(self.SYSFS))
        raw = PowerCollector().collect(ctx)
        self.assertFalse(hasattr(raw, "total_w"))
        self.assertFalse(hasattr(raw, "gpu_w"))


if __name__ == "__main__":
    unittest.main()
