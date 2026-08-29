"""Render tests for the Phase-5 network block (ui/render.py + ui/widgets.py).

Locks the network section's formatting and verifies it is *additive*: a snapshot
with no interface data renders the unchanged legacy frame, and the network block
sits after any disk block, so the existing golden/disk tests keep passing untouched.
"""

import time
import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.model import (
    ClockStats, DiskStat, MemoryStats, NetStat, JobState, JobType, PowerStats, Snapshot,
)
from halo_monitor.ui.render import render_frame

GIB = 1073741824
MB = 1048576
CFG_KO = config_from_env(env={})
CFG_EN = config_from_env(env={"HALO_LANG": "en"})


def fixed_lt(h, m, s):
    return lambda t: time.struct_time((2026, 7, 18, h, m, s, 4, 199, -1))


def snap_with(net, disks=None):
    return Snapshot(ts=0.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151",
                    job=JobState(job_type=JobType.TRAIN), memory=MemoryStats(),
                    power=PowerStats(), clocks=ClockStats(),
                    disks=disks or [], net=net)


def frame(snap, cfg):
    return render_frame(snap, cfg, localtime=fixed_lt(12, 0, 0)).split("\n")


_ETH = NetStat(name="eth0", label="eth0", rx_mb_s=12.34, tx_mb_s=1.2,
               rx_session_bytes=int(4.5 * GIB), tx_session_bytes=int(0.3 * GIB),
               present=True)
_WLAN_FIRST = NetStat(name="wlan0", label="wlan0", present=True)   # first tick: no rate
_ABSENT = NetStat(name="eth1", label="유선2", present=False)
_DISK = DiskStat(path="/", label="/", total_bytes=100 * GIB, free_bytes=50 * GIB,
                 used_bytes=50 * GIB, used_pct=50, low=False, present=True)


class TestNetBlockAdditive(unittest.TestCase):
    def test_no_net_leaves_legacy_12_line_frame(self):
        lines = frame(snap_with([]), CFG_KO)
        self.assertEqual(len(lines), 12)                 # unchanged legacy layout
        self.assertTrue(lines[-1].startswith("╚"))       # footer still last
        self.assertNotIn("네트워크", "\n".join(lines))

    def test_net_block_position_and_count(self):
        lines = frame(snap_with([_ETH, _WLAN_FIRST]), CFG_KO)
        # 12 legacy + 1 separator + 2 iface lines = 15
        self.assertEqual(len(lines), 15)
        self.assertIn("네트워크", lines[11])              # separator right after sclk(=10)
        self.assertTrue(lines[-1].startswith("╚"))       # footer stays last

    def test_net_block_after_disk_block(self):
        lines = frame(snap_with([_ETH], disks=[_DISK]), CFG_KO)
        # 12 legacy + (disk sep + 1 disk line) + (net sep + 1 net line) = 16
        self.assertEqual(len(lines), 16)
        joined = "\n".join(lines)
        self.assertLess(joined.index("디스크"), joined.index("네트워크"))  # disk before net


class TestNetLineFormat(unittest.TestCase):
    def test_present_iface_line_ko(self):
        line = frame(snap_with([_ETH]), CFG_KO)[12]
        self.assertTrue(line.startswith("   ★eth0:"))
        self.assertIn("↓    12.3 MB/s", line)
        self.assertIn("↑     1.2 MB/s", line)
        self.assertIn("(누적 ↓ 4.5GB ↑ 0.3GB)", line)

    def test_present_iface_line_en(self):
        line = frame(snap_with([_ETH]), CFG_EN)[12]
        self.assertIn("(total ↓ 4.5GB ↑ 0.3GB)", line)

    def test_first_tick_shows_question_marks(self):
        line = frame(snap_with([_WLAN_FIRST]), CFG_KO)[12]
        self.assertIn("↓       ? MB/s", line)            # no delta yet (rate padded >7)
        self.assertIn("↑       ? MB/s", line)
        self.assertIn("(누적 ↓ ?GB ↑ ?GB)", line)        # no session bytes yet either

    def test_absent_iface_shows_unavailable(self):
        ko = frame(snap_with([_ABSENT]), CFG_KO)[12]
        en = frame(snap_with([_ABSENT]), CFG_EN)[12]
        self.assertTrue(ko.startswith("   ★유선2:"))
        self.assertTrue(ko.rstrip().endswith("사용불가"))
        self.assertTrue(en.rstrip().endswith("unavailable"))

    def test_labels_aligned_to_widest(self):
        short = NetStat(name="e", label="e", present=False)
        lines = frame(snap_with([_ETH, short]), CFG_KO)
        # "eth0"(4) vs "e"(1), both ASCII: the short label is padded so the colons
        # line up in the same column (char index == display column here).
        self.assertEqual(lines[12].index(":"), lines[13].index(":"))


if __name__ == "__main__":
    unittest.main()
