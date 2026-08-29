"""Render tests for the Phase-6 battery block (ui/render.py + ui/widgets.py).

Locks the battery section's formatting and — critically — verifies it is
*additive*: a snapshot with no battery (desktop/mini-PC, ``present=False``)
renders the unchanged 12-line legacy frame, which is why the existing byte-parity
golden tests keep passing untouched.
"""

import time
import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.model import (
    BatteryStat, ClockStats, JobState, JobType, MemoryStats, PowerStats, Snapshot,
)
from halo_monitor.ui.render import render_frame

CFG_KO = config_from_env(env={})
CFG_EN = config_from_env(env={"HALO_LANG": "en"})


def fixed_lt(h, m, s):
    return lambda t: time.struct_time((2026, 8, 29, h, m, s, 5, 241, -1))


def snap_with(battery):
    return Snapshot(ts=0.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151",
                    job=JobState(job_type=JobType.TRAIN), memory=MemoryStats(),
                    power=PowerStats(), clocks=ClockStats(), battery=battery)


def frame(snap, cfg):
    return render_frame(snap, cfg, localtime=fixed_lt(12, 0, 0)).split("\n")


_FULL_AC = BatteryStat(present=True, ac_online=True, status="Full", capacity_pct=100,
                        discharging=False, discharge_w=0.0, alert="ok")
_DISCHARGING_WARN = BatteryStat(present=True, ac_online=True, status="Discharging",
                                 capacity_pct=28, discharging=True, discharge_w=34.0,
                                 time_remaining_s=4331, alert="warn")
_DISCHARGING_CRIT = BatteryStat(present=True, ac_online=False, status="Discharging",
                                 capacity_pct=6, discharging=True, discharge_w=12.0,
                                 time_remaining_s=1500, alert="crit")


class TestBatteryBlockAdditive(unittest.TestCase):
    def test_no_battery_leaves_legacy_12_line_frame(self):
        lines = frame(snap_with(BatteryStat()), CFG_KO)   # present=False default
        self.assertEqual(len(lines), 12)                   # unchanged legacy layout
        self.assertTrue(lines[-1].startswith("╚"))         # footer still last
        self.assertNotIn("배터리", "\n".join(lines))         # no battery section emitted

    def test_battery_block_position_and_count(self):
        lines = frame(snap_with(_FULL_AC), CFG_KO)
        # 12 legacy lines + 1 separator + 1 battery line = 14
        self.assertEqual(len(lines), 14)
        self.assertIn("배터리", lines[11])                   # separator right after sclk(=10)
        self.assertTrue(lines[-1].startswith("╚"))          # footer stays last (box closed)

    def test_battery_block_appears_before_disk_block(self):
        from halo_monitor.model import DiskStat
        snap = snap_with(_FULL_AC)
        snap.disks = [DiskStat(path="/", label="/", total_bytes=100, free_bytes=90,
                                used_bytes=10, used_pct=10, present=True)]
        lines = frame(snap, CFG_KO)
        battery_idx = next(i for i, ln in enumerate(lines) if "배터리" in ln)
        disk_idx = next(i for i, ln in enumerate(lines) if "디스크" in ln)
        self.assertLess(battery_idx, disk_idx)


class TestBatteryLineFormat(unittest.TestCase):
    def test_full_and_charging_line_ko(self):
        line = frame(snap_with(_FULL_AC), CFG_KO)[12]
        self.assertTrue(line.startswith("   ★배터리:"))
        self.assertIn("100%", line)
        self.assertIn("충전기 연결됨", line)
        self.assertIn("완충", line)
        self.assertTrue(line.rstrip().endswith("✓"))

    def test_full_and_charging_line_en(self):
        line = frame(snap_with(_FULL_AC), CFG_EN)[12]
        self.assertIn("charger connected", line)
        self.assertIn("full", line)
        self.assertTrue(line.rstrip().endswith("✓"))  # ✓

    def test_discharging_warn_shows_watts_and_marker_ko(self):
        line = frame(snap_with(_DISCHARGING_WARN), CFG_KO)[12]
        self.assertIn("28%", line)
        self.assertIn("방전 34W", line)
        self.assertIn("⚠️낮음", line)
        self.assertIn("잔여", line)

    def test_discharging_warn_shows_watts_and_marker_en(self):
        line = frame(snap_with(_DISCHARGING_WARN), CFG_EN)[12]
        self.assertIn("discharge 34W", line)
        self.assertIn("LOW", line)
        self.assertIn("remaining", line)

    def test_discharging_crit_uses_distinct_marker(self):
        ko = frame(snap_with(_DISCHARGING_CRIT), CFG_KO)[12]
        en = frame(snap_with(_DISCHARGING_CRIT), CFG_EN)[12]
        self.assertIn("🚨위험", ko)
        self.assertIn("CRITICAL", en)
        self.assertIn("충전기 분리됨", ko)     # ac_online=False on this fixture
        self.assertIn("charger disconnected", en)

    def test_unmeasurable_discharge_shows_unknown_watts_not_crash(self):
        bat = BatteryStat(present=True, ac_online=False, status=None,
                           capacity_pct=40, discharging=True, discharge_w=None, alert="warn")
        line = frame(snap_with(bat), CFG_KO)[12]
        self.assertIn("?W", line)

    def test_unknown_capacity_shows_question_mark(self):
        bat = BatteryStat(present=True, ac_online=True, status="Charging",
                           capacity_pct=None, discharging=False, alert="ok")
        line = frame(snap_with(bat), CFG_KO)[12]
        self.assertIn("?%", line)


if __name__ == "__main__":
    unittest.main()
