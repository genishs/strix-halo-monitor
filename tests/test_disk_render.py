"""Render tests for the Phase-5 disk block (ui/render.py + ui/widgets.py).

Locks the disk section's formatting and — critically — verifies it is *additive*:
a snapshot with no disk data renders the unchanged 12-line legacy frame, which is
why the existing byte-parity golden tests keep passing untouched.
"""

import time
import unicodedata
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


def col_of(line, needle):
    """Terminal *column* where ``needle`` starts — not its character index.

    Alignment must be asserted in display columns: a Korean volume label such as
    ``새 볼륨1`` is 5 characters but 8 columns wide, so ``str.index`` would report
    two correctly-aligned rows as mismatched. Deliberately a second, independent
    implementation of the width rule rather than the renderer's own helper.
    """
    prefix = line[:line.index(needle)]
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in prefix)


_DATA = DiskStat(path="/mnt/data", label="/mnt/data", total_bytes=500 * GIB,
                 free_bytes=333 * GIB, used_bytes=167 * GIB, used_pct=33,
                 low=False, present=True)
_ROOT_LOW = DiskStat(path="/", label="/", total_bytes=100 * GIB,
                     free_bytes=3 * GIB, used_bytes=97 * GIB, used_pct=97,
                     low=True, present=True)
_EXT_ABSENT = DiskStat(path="/run/media/user/새 볼륨", label="외장모델", present=False)
#: The 1.9TB external that auto-discovery now surfaces: 4-digit used/total figures.
_BIG_EXT = DiskStat(path="/run/media/user/새 볼륨1", label="새 볼륨1",
                    total_bytes=1863 * GIB, free_bytes=441 * GIB,
                    used_bytes=1422 * GIB, used_pct=76, low=False, present=True)


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


class TestDiskColumnAlignment(unittest.TestCase):
    """Auto-discovery can mix magnitudes (a 1.9TB drive next to a 210GB root).

    A fixed-width numeric column made the 4-digit row jut out and dragged the bar,
    percentage and free-space columns out of line on every other row. Widths are
    now computed per frame, so the bars start in the same column regardless.
    """

    def test_bars_align_across_mixed_magnitudes(self):
        lines = frame(snap_with([_BIG_EXT, _DATA, _ROOT_LOW]), CFG_KO)[12:15]
        starts = [col_of(ln, "[") for ln in lines]
        self.assertEqual(len(set(starts)), 1, f"bar columns misaligned: {starts}")

    def test_free_column_aligns_too(self):
        lines = frame(snap_with([_BIG_EXT, _DATA, _ROOT_LOW]), CFG_KO)[12:15]
        self.assertEqual(len({col_of(ln, "여유") for ln in lines}), 1)

    def test_absent_row_does_not_break_width_computation(self):
        # An absent mount has no numbers; it must not poison the max() widths.
        lines = frame(snap_with([_BIG_EXT, _EXT_ABSENT, _DATA]), CFG_KO)[12:15]
        self.assertTrue(lines[1].rstrip().endswith("사용불가"))
        self.assertEqual(col_of(lines[0], "["), col_of(lines[2], "["))

    def test_single_disk_has_no_stray_padding(self):
        # One mount -> natural widths, i.e. the legacy spacing is unchanged.
        self.assertIn("167.0 / 500GB", frame(snap_with([_DATA]), CFG_KO)[12])


if __name__ == "__main__":
    unittest.main()
