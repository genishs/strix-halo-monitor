"""Tests for DiskCollector + threshold logic + config parsing (DESIGN §2.2 B, Phase 5).

``os.statvfs`` is faked (never called for real) so the warning-threshold logic is
verified deterministically without touching any mount. This also documents the C2
invariant at the test level: the collector only ever consults statvfs block
counters — there is no ``du``/directory-walk path to exercise.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

import _util  # noqa: F401

from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.disk import DiskCollector, is_low
from halo_monitor.config import Config, DiskTarget, config_from_env

_GIB = 1073741824
_FRSIZE = 4096

# --- auto-discovery fixtures (Phase 5.1) ----------------------------------- #
# Decoded paths as the collector sees them, and the ``\040``-escaped spellings the
# kernel writes into /proc/mounts. Both externals are Korean-named with a space —
# the exact shape that has broken path handling in this project before.
EXT1 = "/run/media/user/새 볼륨"
EXT2 = "/run/media/user/새 볼륨1"
EXT3 = "/run/media/user/백업"
EXT1_ESC = r"/run/media/user/새\040볼륨"
EXT2_ESC = r"/run/media/user/새\040볼륨1"
EXT3_ESC = r"/run/media/user/백업"

#: The real machine's four filesystems plus the usual pseudo-fs noise.
MOUNTS_4 = (
    "sysfs /sys sysfs rw 0 0\n"
    "proc /proc proc rw 0 0\n"
    "tmpfs /run tmpfs rw 0 0\n"
    "/dev/loop0 /snap/core24/1587 squashfs ro 0 0\n"
    "/dev/nvme0n1p5 /boot/efi vfat rw 0 0\n"
    "gvfsd-fuse /run/user/1000/gvfs fuse.gvfsd-fuse rw 0 0\n"
    "/dev/nvme0n1p4 / ext4 rw 0 0\n"
    "/dev/nvme0n1p2 /mnt/data fuseblk rw 0 0\n"
    f"/dev/sda2 {EXT1_ESC} exfat rw 0 0\n"
    f"/dev/sdb2 {EXT2_ESC} ntfs3 rw 0 0\n"
)


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
    def test_default_is_auto_discovery(self):
        """No env -> ``None``, the auto-discover sentinel (mirrors ``net_ifaces``).

        This replaces the former ``test_default_mounts_present``, which asserted a
        hardcoded default list containing ``/mnt/data`` and ``/``. That list *was*
        the bug: mounts it did not name were invisible. The same guarantee — the
        real ``/mnt/data`` and ``/`` are reported — is now covered at the layer that
        actually decides it, by ``test_mounts_discovery.test_finds_fixed_mounts``
        and ``TestDiskAutoDiscovery`` below.
        """
        cfg = config_from_env(env={})
        self.assertIsNone(cfg.disk_mounts)
        self.assertEqual(cfg.disk_warn_free_gb, 10.0)
        self.assertEqual(cfg.disk_warn_free_pct, 5.0)
        self.assertEqual(cfg.disk_max_mounts, 8)

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

    def test_cap_and_rescan_overrides(self):
        cfg = config_from_env(env={"HALO_DISK_MAX": "3", "HALO_DISK_RESCAN_S": "30"})
        self.assertEqual(cfg.disk_max_mounts, 3)
        self.assertEqual(cfg.disk_rescan_s, 30.0)


class FakeClock:
    """Monotonic clock the test drives by hand, so TTL expiry is deterministic."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class TestDiskAutoDiscovery(unittest.TestCase):
    """Auto-discovery end-to-end through the collector (Phase 5.1 regression).

    The hardcoded three-mount default hid every drive it did not name — with two
    external disks connected, only one was rendered. These tests drive the collector
    against a fixture ``/proc/mounts`` tree so the whole path is exercised without
    touching a real mount.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        self.clock = FakeClock()
        self.addCleanup(self._td.cleanup)

    def write_mounts(self, text):
        proc = os.path.join(self.root, "proc")
        os.makedirs(proc, exist_ok=True)
        with open(os.path.join(proc, "mounts"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def auto_ctx(self, **cfg_kw):
        """Context in auto mode (``disk_mounts=None``) reading the fixture root."""
        cfg = Config(disk_mounts=None, sysfs_root=self.root, **cfg_kw)
        return CollectContext(cfg=cfg, backend=None, root=self.root)

    def collect(self, sv, ctx):
        return DiskCollector(statvfs=sv, monotonic=self.clock).collect(ctx)

    def test_all_four_mounts_reported(self):
        """THE BUG, at collector level: 4 filesystems mounted -> 4 rows."""
        self.write_mounts(MOUNTS_4)
        sv = statvfs_map({
            "/": fake_statvfs(210, 66),
            "/mnt/data": fake_statvfs(701, 184),
            EXT1: fake_statvfs(932, 102),
            EXT2: fake_statvfs(1900, 441),
        })
        got = self.collect(sv, self.auto_ctx())
        self.assertEqual([d.path for d in got], ["/mnt/data", EXT1, EXT2, "/"])
        self.assertTrue(all(d.present for d in got))
        self.assertEqual([d.label for d in got],
                         ["/mnt/data", "새 볼륨", "새 볼륨1", "/"])

    def test_noise_never_reaches_the_collector(self):
        self.write_mounts(MOUNTS_4)
        sv = statvfs_map({
            "/": fake_statvfs(210, 66), "/mnt/data": fake_statvfs(701, 184),
            EXT1: fake_statvfs(932, 102), EXT2: fake_statvfs(1900, 441),
        })
        got = self.collect(sv, self.auto_ctx())          # KeyError if noise slipped in
        self.assertEqual(len(got), 4)

    def test_hotplug_new_drive_appears_after_ttl(self):
        """A drive plugged in mid-run must show up without restarting the monitor."""
        self.write_mounts(MOUNTS_4)
        sizes = {
            "/": fake_statvfs(210, 66), "/mnt/data": fake_statvfs(701, 184),
            EXT1: fake_statvfs(932, 102), EXT2: fake_statvfs(1900, 441),
            EXT3: fake_statvfs(500, 250),
        }
        sv = statvfs_map(sizes)
        collector = DiskCollector(statvfs=sv, monotonic=self.clock)
        ctx = self.auto_ctx(disk_rescan_s=5.0)
        self.assertEqual(len(collector.collect(ctx)), 4)

        # Drive plugged in: the mount table gains a row.
        self.write_mounts(MOUNTS_4 + f"/dev/sdc1 {EXT3_ESC} exfat rw 0 0\n")
        self.clock.advance(1.0)                          # still inside the TTL
        self.assertEqual(len(collector.collect(ctx)), 4, "should still be cached")
        self.clock.advance(5.0)                          # TTL expired -> rescan
        got = collector.collect(ctx)
        self.assertEqual(len(got), 5)
        self.assertIn(EXT3, [d.path for d in got])

    def test_hotunplug_removed_drive_disappears_after_ttl(self):
        """...and an unplugged drive must stop being listed, not linger as 사용불가."""
        self.write_mounts(MOUNTS_4)
        sv = statvfs_map({
            "/": fake_statvfs(210, 66), "/mnt/data": fake_statvfs(701, 184),
            EXT1: fake_statvfs(932, 102),
        }, absent=(EXT2,))
        collector = DiskCollector(statvfs=sv, monotonic=self.clock)
        ctx = self.auto_ctx(disk_rescan_s=5.0)
        self.assertEqual(len(collector.collect(ctx)), 4)

        self.write_mounts(MOUNTS_4.replace(f"/dev/sdb2 {EXT2_ESC} ntfs3 rw 0 0\n", ""))
        self.clock.advance(6.0)
        got = collector.collect(ctx)
        self.assertEqual([d.path for d in got], ["/mnt/data", EXT1, "/"])

    def test_cache_avoids_rereading_every_tick(self):
        """Within the TTL the mount table is not re-read (cost bound, C2)."""
        self.write_mounts(MOUNTS_4)
        sv = statvfs_map({
            "/": fake_statvfs(210, 66), "/mnt/data": fake_statvfs(701, 184),
            EXT1: fake_statvfs(932, 102), EXT2: fake_statvfs(1900, 441),
        })
        collector = DiskCollector(statvfs=sv, monotonic=self.clock)
        ctx = self.auto_ctx(disk_rescan_s=5.0)
        collector.collect(ctx)
        os.remove(os.path.join(self.root, "proc", "mounts"))   # table now unreadable
        self.clock.advance(1.0)
        self.assertEqual(len(collector.collect(ctx)), 4)       # served from cache

    def test_cap_keeps_largest_when_over_limit(self):
        """Too many volumes must not push the frame off-screen; biggest win."""
        rows, sizes = [], {}
        for i in range(12):
            path = f"/mnt/vol{i:02d}"
            rows.append(f"/dev/sd{i:02d} {path} ext4 rw 0 0")
            sizes[path] = fake_statvfs(100 + i * 10, 50)       # vol11 largest
        self.write_mounts("\n".join(rows) + "\n")
        got = self.collect(statvfs_map(sizes), self.auto_ctx(disk_max_mounts=8))
        self.assertEqual(len(got), 8)
        self.assertEqual([d.path for d in got][:2], ["/mnt/vol11", "/mnt/vol10"])
        self.assertNotIn("/mnt/vol00", [d.path for d in got])  # smallest dropped

    def test_under_the_cap_display_order_is_preserved(self):
        self.write_mounts(MOUNTS_4)
        sv = statvfs_map({
            "/": fake_statvfs(210, 66), "/mnt/data": fake_statvfs(701, 184),
            EXT1: fake_statvfs(932, 102), EXT2: fake_statvfs(1900, 441),
        })
        got = self.collect(sv, self.auto_ctx(disk_max_mounts=8))
        # NOT sorted by size (that would be EXT2, /mnt/data, EXT1, /).
        self.assertEqual([d.path for d in got], ["/mnt/data", EXT1, EXT2, "/"])

    def test_unreadable_mount_table_yields_no_rows_not_a_crash(self):
        got = self.collect(statvfs_map({}), self.auto_ctx())    # no proc/mounts at all
        self.assertEqual(got, [])

    def test_available_true_in_auto_mode(self):
        c = DiskCollector(statvfs=statvfs_map({}), monotonic=self.clock)
        self.assertTrue(c.available(self.auto_ctx()))           # None -> auto -> on
        self.assertFalse(c.available(ctx_with([])))             # () -> explicitly off


class TestExplicitMountsBackwardCompat(unittest.TestCase):
    """``HALO_DISK_MOUNTS`` keeps overriding everything, exactly as before."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        os.makedirs(os.path.join(self.root, "proc"))
        with open(os.path.join(self.root, "proc", "mounts"), "w", encoding="utf-8") as f:
            f.write(MOUNTS_4)                     # discovery would find 4 here
        self.addCleanup(self._td.cleanup)

    def ctx_for(self, env):
        cfg = config_from_env(env=env)
        return CollectContext(cfg=cfg, backend=None, root=self.root)

    def test_explicit_list_wins_over_discovery(self):
        ctx = self.ctx_for({"HALO_DISK_MOUNTS": "데이터=/mnt/data"})
        sv = statvfs_map({"/mnt/data": fake_statvfs(701, 184)})
        got = DiskCollector(statvfs=sv).collect(ctx)
        self.assertEqual([(d.path, d.label) for d in got], [("/mnt/data", "데이터")])

    def test_explicit_path_with_space_still_probed(self):
        ctx = self.ctx_for({"HALO_DISK_MOUNTS": f"외장={EXT1}"})
        sv = statvfs_map({EXT1: fake_statvfs(932, 102)})
        [d] = DiskCollector(statvfs=sv).collect(ctx)
        self.assertTrue(d.present)
        self.assertEqual(d.label, "외장")

    def test_explicit_list_is_not_capped(self):
        """A user who named 10 mounts gets 10 — the cap guards auto-discovery only."""
        paths = [f"/mnt/vol{i}" for i in range(10)]
        env = {"HALO_DISK_MOUNTS": ";".join(paths), "HALO_DISK_MAX": "8"}
        sv = statvfs_map({p: fake_statvfs(100, 50) for p in paths})
        got = DiskCollector(statvfs=sv).collect(self.ctx_for(env))
        self.assertEqual([d.path for d in got], paths)

    def test_empty_env_still_disables_widget(self):
        ctx = self.ctx_for({"HALO_DISK_MOUNTS": ""})
        c = DiskCollector(statvfs=statvfs_map({}))
        self.assertFalse(c.available(ctx))
        self.assertEqual(c.collect(ctx), [])


if __name__ == "__main__":
    unittest.main()
