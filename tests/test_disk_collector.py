"""Tests for DiskCollector + threshold logic + config parsing (DESIGN §2.2 B, Phase 5).

``os.statvfs`` is faked (never called for real) so the warning-threshold logic is
verified deterministically without touching any mount. This also documents the C2
invariant at the test level: the collector only ever consults statvfs block
counters — there is no ``du``/directory-walk path to exercise.
"""

import os
import unittest
from types import SimpleNamespace

import _util  # noqa: F401

from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.disk import DiskCollector, is_low
from halo_monitor.config import Config, DiskTarget, config_from_env

_GIB = 1073741824
_FRSIZE = 4096


def fake_statvfs(total_gib, free_gib, reserved_gib=0):
    """A minimal ``os.statvfs``-like result with the fields the collector reads."""
    blocks = total_gib * _GIB // _FRSIZE
    bavail = free_gib * _GIB // _FRSIZE
    bfree = (free_gib + reserved_gib) * _GIB // _FRSIZE
    return SimpleNamespace(
        f_bsize=_FRSIZE, f_frsize=_FRSIZE, f_blocks=blocks, f_bfree=bfree, f_bavail=bavail
    )


def statvfs_map(mapping, absent=()):
    """Build a fake statvfs callable: path -> result, or OSError for ``absent`` paths."""
    def _sv(path):
        if path in absent:
            raise OSError(2, "No such file or directory", path)
        return mapping[path]
    return _sv


def ctx_with(mounts, warn_gb=10.0, warn_pct=5.0):
    cfg = Config(disk_mounts=tuple(mounts), disk_warn_free_gb=warn_gb,
                 disk_warn_free_pct=warn_pct)
    return CollectContext(cfg=cfg, backend=None, root="/")


class TestIsLow(unittest.TestCase):
    def test_low_by_gb_floor(self):
        # 8 GiB free of 500 -> below the 10 GiB floor (pct is fine at 1.6%... which is
        # also < 5, so bump total so only the GB floor trips it):
        self.assertTrue(is_low(8 * _GIB, 5000 * _GIB, 10.0, 5.0))   # 8GB<10, 0.16%<5 too
        self.assertTrue(is_low(8 * _GIB, 5000 * _GIB, 10.0, 0.0))   # only GB floor trips

    def test_low_by_pct_floor(self):
        # 40 GiB free of 1000 -> 4% < 5%, but 40GB > 10GB floor: pct floor alone trips.
        self.assertTrue(is_low(40 * _GIB, 1000 * _GIB, 10.0, 5.0))
        self.assertFalse(is_low(40 * _GIB, 1000 * _GIB, 10.0, 3.0))  # 4% >= 3%, 40>10

    def test_not_low_when_ample(self):
        self.assertFalse(is_low(200 * _GIB, 1000 * _GIB, 10.0, 5.0))  # 20% and 200GB

    def test_unknown_or_zero_never_low(self):
        self.assertFalse(is_low(None, 100, 10.0, 5.0))
        self.assertFalse(is_low(100, None, 10.0, 5.0))
        self.assertFalse(is_low(0, 0, 10.0, 5.0))


class TestDiskCollector(unittest.TestCase):
    def test_present_mount_numbers(self):
        sv = statvfs_map({"/mnt/data": fake_statvfs(500, 333, reserved_gib=0)})
        ctx = ctx_with([DiskTarget("/mnt/data", "data")])
        [d] = DiskCollector(statvfs=sv).collect(ctx)
        self.assertTrue(d.present)
        self.assertEqual(d.label, "data")
        self.assertEqual(d.total_bytes, 500 * _GIB)
        self.assertEqual(d.free_bytes, 333 * _GIB)
        self.assertEqual(d.used_bytes, (500 - 333) * _GIB)
        self.assertEqual(d.used_pct, 33)            # 167/500 = 33.4 -> 33 (awk %.0f)
        self.assertFalse(d.low)                     # 333GB, 66% free

    def test_reserved_blocks_gap(self):
        # 5 GiB reserved: free(avail)=20, but used counts against f_bfree=25.
        sv = statvfs_map({"/": fake_statvfs(100, 20, reserved_gib=5)})
        [d] = DiskCollector(statvfs=sv).collect(ctx_with([DiskTarget("/", "/")]))
        self.assertEqual(d.free_bytes, 20 * _GIB)   # user-available (f_bavail)
        self.assertEqual(d.used_bytes, 75 * _GIB)   # 100 - f_bfree(25)
        self.assertEqual(d.used_pct, 75)

    def test_warns_below_gb_threshold(self):
        sv = statvfs_map({"/mnt/data": fake_statvfs(500, 8)})  # 8GB free < 10GB
        ctx = ctx_with([DiskTarget("/mnt/data")], warn_gb=10.0, warn_pct=0.0)
        [d] = DiskCollector(statvfs=sv).collect(ctx)
        self.assertTrue(d.low)
        self.assertEqual(d.label, "/mnt/data")      # label falls back to path

    def test_warns_below_pct_threshold(self):
        sv = statvfs_map({"/": fake_statvfs(1000, 40)})  # 40GB free = 4% < 5%
        ctx = ctx_with([DiskTarget("/")], warn_gb=10.0, warn_pct=5.0)
        [d] = DiskCollector(statvfs=sv).collect(ctx)
        self.assertTrue(d.low)                      # pct floor trips though 40GB>10GB

    def test_absent_mount_is_unavailable_not_raise(self):
        sv = statvfs_map({}, absent=("/run/media/user/새 볼륨",))
        ctx = ctx_with([DiskTarget("/run/media/user/새 볼륨", "외장모델")])
        [d] = DiskCollector(statvfs=sv).collect(ctx)  # must not raise
        self.assertFalse(d.present)
        self.assertIsNone(d.total_bytes)
        self.assertFalse(d.low)                     # absence never a false alarm
        self.assertEqual(d.label, "외장모델")

    def test_multiple_mounts_mixed(self):
        sv = statvfs_map(
            {"/mnt/data": fake_statvfs(500, 300), "/": fake_statvfs(100, 3)},
            absent=("/ext",),
        )
        ctx = ctx_with([DiskTarget("/mnt/data"), DiskTarget("/ext"), DiskTarget("/")])
        data, ext, root = DiskCollector(statvfs=sv).collect(ctx)
        self.assertTrue(data.present and not data.low)
        self.assertFalse(ext.present)
        self.assertTrue(root.present and root.low)  # 3GB < 10GB

    def test_available_reflects_configured_mounts(self):
        c = DiskCollector(statvfs=statvfs_map({}))
        self.assertTrue(c.available(ctx_with([DiskTarget("/")])))
        self.assertFalse(c.available(ctx_with([])))  # empty config -> disabled


class TestDiskConfigParsing(unittest.TestCase):
    def test_default_mounts_present(self):
        cfg = config_from_env(env={})
        paths = [m.path for m in cfg.disk_mounts]
        self.assertIn("/mnt/data", paths)
        self.assertIn("/", paths)
        self.assertEqual(cfg.disk_warn_free_gb, 10.0)
        self.assertEqual(cfg.disk_warn_free_pct, 5.0)

    def test_env_label_and_bare_paths(self):
        cfg = config_from_env(env={"HALO_DISK_MOUNTS": "데이터=/mnt/data ; /var/log"})
        self.assertEqual(len(cfg.disk_mounts), 2)
        self.assertEqual(cfg.disk_mounts[0], DiskTarget("/mnt/data", "데이터"))
        self.assertEqual(cfg.disk_mounts[1], DiskTarget("/var/log", "/var/log"))

    def test_env_path_with_spaces(self):
        cfg = config_from_env(env={"HALO_DISK_MOUNTS": "외장=/run/media/user/새 볼륨"})
        self.assertEqual(cfg.disk_mounts[0].path, "/run/media/user/새 볼륨")

    def test_empty_env_disables_widget(self):
        cfg = config_from_env(env={"HALO_DISK_MOUNTS": ""})
        self.assertEqual(cfg.disk_mounts, ())

    def test_threshold_overrides(self):
        cfg = config_from_env(env={"HALO_DISK_WARN_GB": "25", "HALO_DISK_WARN_PCT": "8"})
        self.assertEqual(cfg.disk_warn_free_gb, 25.0)
        self.assertEqual(cfg.disk_warn_free_pct, 8.0)


if __name__ == "__main__":
    unittest.main()
