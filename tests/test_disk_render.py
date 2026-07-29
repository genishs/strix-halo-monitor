"""Render tests for the Phase-5 disk block (ui/render.py + ui/widgets.py).

Locks the disk section's formatting and — critically — verifies it is *additive*:
a snapshot with no disk data renders the unchanged 12-line legacy frame, which is
why the existing byte-parity golden tests keep passing untouched.
"""

import time
import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.model import (
    ClockStats, DiskStat, JobState, JobType, MemoryStats, PowerStats, Snapshot,
)
from halo_monitor.ui.render import render_frame

GIB = 1073741824
CFG_KO = config_from_env(env={})
CFG_EN = config_from_env(env={"HALO_LANG": "en"})


def fixed_lt(h, m, s):
    return lambda t: time.struct_time((2026, 7, 18, h, m, s, 4, 199, -1))


def snap_with(disks):
    return Snapshot(ts=0.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151",
                    job=JobState(job_type=JobType.TRAIN), memory=MemoryStats(),
                    power=PowerStats(), clocks=ClockStats(), disks=disks)


def frame(snap, cfg):
    return render_frame(snap, cfg, localtime=fixed_lt(12, 0, 0)).split("\n")


_DATA = DiskStat(path="/mnt/data", label="/mnt/data", total_bytes=500 * GIB,
                 free_bytes=333 * GIB, used_bytes=167 * GIB, used_pct=33,
                 low=False, present=True)
_ROOT_LOW = DiskStat(path="/", label="/", total_bytes=100 * GIB,
                     free_bytes=3 * GIB, used_bytes=97 * GIB, used_pct=97,
                     low=True, present=True)
_EXT_ABSENT = DiskStat(path="/run/media/user/새 볼륨", label="외장모델", present=False)


class TestDiskBlockAdditive(unittest.TestCase):
    def test_no_disks_leaves_legacy_12_line_frame(self):
        lines = frame(snap_with([]), CFG_KO)
        self.assertEqual(len(lines), 12)               # unchanged legacy layout
        self.assertTrue(lines[-1].startswith("╚"))     # footer still last
        self.assertNotIn("디스크", "\n".join(lines))    # no disk section emitted

    def test_disk_block_position_and_count(self):
        lines = frame(snap_with([_DATA, _ROOT_LOW]), CFG_KO)
        # 12 legacy lines + 1 separator + 2 mount lines = 15
        self.assertEqual(len(lines), 15)
        self.assertIn("디스크", lines[11])              # separator right after sclk(=10)
        self.assertTrue(lines[-1].startswith("╚"))     # footer stays last (box closed)


class TestDiskLineFormat(unittest.TestCase):
    def test_present_mount_line_ko(self):
        line = frame(snap_with([_DATA]), CFG_KO)[12]
        self.assertTrue(line.startswith("   ★/mnt/data:"))
        self.assertIn("167.0 / 500GB", line)
        self.assertIn("[██████░", line)                # 33% -> 6 filled cells
        self.assertIn("] 33%   여유 333.0GB ✓", line)

    def test_present_mount_line_en(self):
        line = frame(snap_with([_DATA]), CFG_EN)[12]
        self.assertIn("] 33%   free 333.0GB ✓", line)

    def test_low_mount_shows_warning_marker(self):
        ko = frame(snap_with([_ROOT_LOW]), CFG_KO)[12]
        en = frame(snap_with([_ROOT_LOW]), CFG_EN)[12]
        self.assertTrue(ko.rstrip().endswith("⚠️위험"))
        self.assertTrue(en.rstrip().endswith("⚠️LOW"))

    def test_absent_mount_shows_unavailable(self):
        ko = frame(snap_with([_EXT_ABSENT]), CFG_KO)[12]
        en = frame(snap_with([_EXT_ABSENT]), CFG_EN)[12]
        self.assertTrue(ko.startswith("   ★외장모델:"))
        self.assertTrue(ko.rstrip().endswith("사용불가"))
        self.assertTrue(en.rstrip().endswith("unavailable"))

    def test_labels_aligned_to_widest(self):
        # "/mnt/data" (9 cols) vs "/" (1 col): the short label is padded so the
        # colons line up in the same column.
        lines = frame(snap_with([_DATA, _ROOT_LOW]), CFG_KO)
        self.assertEqual(lines[12].index(":"), lines[13].index(":"))


if __name__ == "__main__":
    unittest.main()
