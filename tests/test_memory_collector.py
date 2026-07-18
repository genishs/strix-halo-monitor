"""Tests for MemoryCollector against sysfs fixtures (DESIGN §2.2 B, Phase 2).

Verifies GTT/VRAM (via AmdgpuBackend) + host RAM/swap from /proc/meminfo, all
read under an injected root for determinism. gtt_rate_mb_s must always come
back None — that delta is loop-owned, never collector-owned.
"""

import os
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.backends.amdgpu import AmdgpuBackend
from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.memory import MemoryCollector
from halo_monitor.config import config_from_env

SYSFS = os.path.join(_util.FIXTURES, "sysfs")
SYSFS_NO_RAPL = os.path.join(_util.FIXTURES, "sysfs_no_rapl")


def _ctx(root: str, backend):
    cfg = config_from_env(env={})
    return CollectContext(cfg=cfg, backend=backend, root=root)


class TestMemoryCollector(unittest.TestCase):
    def test_full_fixture_with_backend(self):
        ctx = _ctx(SYSFS, AmdgpuBackend(SYSFS))
        mem = MemoryCollector().collect(ctx)
        self.assertEqual(mem.gtt_total_bytes, 60129542144)
        self.assertEqual(mem.gtt_used_bytes, 51667468288)
        self.assertEqual(mem.vram_used_bytes, 889708544)
        self.assertAlmostEqual(mem.ram_free_gb, 2579336 / 1024 / 1024, places=6)
        self.assertAlmostEqual(
            mem.swap_used_gb, (67380216 - 64670052) / 1024 / 1024, places=6
        )
        self.assertIsNone(mem.gtt_rate_mb_s)  # loop-owned, never set here

    def test_no_backend_still_reads_host_ram(self):
        ctx = _ctx(SYSFS, backend=None)
        mem = MemoryCollector().collect(ctx)
        self.assertIsNone(mem.gtt_used_bytes)
        self.assertIsNone(mem.gtt_total_bytes)
        self.assertIsNone(mem.vram_used_bytes)
        self.assertIsNotNone(mem.ram_free_gb)  # host RAM independent of GPU backend
        self.assertIsNotNone(mem.swap_used_gb)

    def test_missing_meminfo_gives_none_not_raise(self):
        empty_root = os.path.join(_util.FIXTURES, "sysfs_empty_root_does_not_exist")
        ctx = _ctx(empty_root, backend=None)
        mem = MemoryCollector().collect(ctx)  # must not raise
        self.assertIsNone(mem.ram_free_gb)
        self.assertIsNone(mem.swap_used_gb)
        self.assertIsNone(mem.gtt_used_bytes)

    def test_no_rapl_fixture_memory_unaffected(self):
        # sysfs_no_rapl exercises the "RAPL missing" case for PowerCollector, but
        # its GTT/VRAM/meminfo files are present and should read fine here too.
        ctx = _ctx(SYSFS_NO_RAPL, AmdgpuBackend(SYSFS_NO_RAPL))
        mem = MemoryCollector().collect(ctx)
        self.assertEqual(mem.gtt_used_bytes, 40000000000)
        self.assertIsNotNone(mem.ram_free_gb)


if __name__ == "__main__":
    unittest.main()
