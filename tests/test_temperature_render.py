"""Render tests for the Phase-7 temperature block (ui/render.py + ui/widgets.py).

Locks the temperature section's formatting and — critically — verifies it is
*additive*: a snapshot with no temps (``temps=[]``, e.g. a box with no readable
GPU/CPU/NVMe hwmon sensor) renders the unchanged 12-line legacy frame, same
guarantee ``test_battery_render.py`` makes for the battery block.
"""

import time
import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.model import (
    BatteryStat, ClockStats, DiskStat, JobState, JobType, MemoryStats, PowerStats, Snapshot,
    TempStat,
)
from halo_monitor.ui.render import render_frame

CFG_KO = config_from_env(env={})
CFG_EN = config_from_env(env={"HALO_LANG": "en"})


def fixed_lt(h, m, s):
    return lambda t: time.struct_time((2026, 8, 29, h, m, s, 5, 241, -1))


def snap_with(temps):
    return Snapshot(ts=0.0, title="Strix Halo Train/Score Monitor", gfx="gfx1151",
                    job=JobState(job_type=JobType.TRAIN), memory=MemoryStats(),
                    power=PowerStats(), clocks=ClockStats(), battery=BatteryStat(),
                    temps=temps)


def frame(snap, cfg):
    return render_frame(snap, cfg, localtime=fixed_lt(12, 0, 0)).split("\n")


_GPU_OK = TempStat(key="gpu", label="GPU", temp_c=88.0, warn_c=95.0, crit_c=105.0, alert="ok")
_CPU_OK = TempStat(key="cpu", label="CPU", temp_c=87.0, warn_c=95.0, crit_c=105.0, alert="ok")
_NVME1_OK = TempStat(key="nvme0", label="NVMe1", temp_c=53.0, warn_c=70.0, crit_c=80.0, alert="ok")
_NVME2_OK = TempStat(key="nvme1", label="NVMe2", temp_c=64.0, warn_c=70.0, crit_c=80.0, alert="ok")
_GPU_WARN = TempStat(key="gpu", label="GPU", temp_c=97.0, warn_c=95.0, crit_c=105.0, alert="warn")
_GPU_CRIT = TempStat(key="gpu", label="GPU", temp_c=108.0, warn_c=95.0, crit_c=105.0, alert="crit")


class TestTempBlockAdditive(unittest.TestCase):
    def test_no_sensors_leaves_legacy_12_line_frame(self):
        lines = frame(snap_with([]), CFG_KO)
        self.assertEqual(len(lines), 12)
        self.assertTrue(lines[-1].startswith("╚"))
        self.assertNotIn("온도", "\n".join(lines))

    def test_temp_block_position_and_count(self):
        lines = frame(snap_with([_GPU_OK, _CPU_OK]), CFG_KO)
        # 12 legacy lines + 1 separator + 2 sensor lines = 15
        self.assertEqual(len(lines), 15)
        self.assertIn("온도", lines[11])                    # separator right after sclk(=10)
        self.assertTrue(lines[-1].startswith("╚"))

    def test_temp_block_appears_before_disk_block(self):
        snap = snap_with([_GPU_OK])
        snap.disks = [DiskStat(path="/", label="/", total_bytes=100, free_bytes=90,
                                used_bytes=10, used_pct=10, present=True)]
        lines = frame(snap, CFG_KO)
        temp_idx = next(i for i, ln in enumerate(lines) if "온도" in ln)
        disk_idx = next(i for i, ln in enumerate(lines) if "디스크" in ln)
        self.assertLess(temp_idx, disk_idx)

    def test_temp_block_appears_after_battery_block(self):
        from halo_monitor.model import BatteryStat as _BS
        snap = snap_with([_GPU_OK])
        snap.battery = _BS(present=True, ac_online=True, status="Full", capacity_pct=100,
                            discharging=False, discharge_w=0.0, alert="ok")
        lines = frame(snap, CFG_KO)
        battery_idx = next(i for i, ln in enumerate(lines) if "배터리" in ln)
        temp_idx = next(i for i, ln in enumerate(lines) if "온도" in ln)
        self.assertLess(battery_idx, temp_idx)


class TestTempLineFormat(unittest.TestCase):
    def test_gpu_and_cpu_lines_ko(self):
        lines = frame(snap_with([_GPU_OK, _CPU_OK]), CFG_KO)
        gpu_line = next(ln for ln in lines if "★GPU:" in ln)
        cpu_line = next(ln for ln in lines if "★CPU:" in ln)
        self.assertIn("88°C", gpu_line)
        self.assertTrue(gpu_line.rstrip().endswith("✓"))
        self.assertIn("87°C", cpu_line)
        self.assertTrue(cpu_line.rstrip().endswith("✓"))

    def test_multi_nvme_lines_labelled_and_aligned(self):
        lines = frame(snap_with([_NVME1_OK, _NVME2_OK]), CFG_KO)
        nvme1 = next(ln for ln in lines if "NVMe1" in ln)
        nvme2 = next(ln for ln in lines if "NVMe2" in ln)
        self.assertIn("53°C", nvme1)
        self.assertIn("64°C", nvme2)

    def test_warn_marker_ko_and_en(self):
        ko = next(ln for ln in frame(snap_with([_GPU_WARN]), CFG_KO) if "★GPU:" in ln)
        en = next(ln for ln in frame(snap_with([_GPU_WARN]), CFG_EN) if "★GPU:" in ln)
        self.assertIn("⚠️높음", ko)
        self.assertIn("HIGH", en)

    def test_crit_marker_uses_distinct_prefix(self):
        ko = next(ln for ln in frame(snap_with([_GPU_CRIT]), CFG_KO) if "★GPU:" in ln)
        en = next(ln for ln in frame(snap_with([_GPU_CRIT]), CFG_EN) if "★GPU:" in ln)
        self.assertIn("🚨위험", ko)
        self.assertIn("CRITICAL", en)

    def test_normal_training_load_shows_ok_not_warn(self):
        # Regression guard for the exact scenario the boss flagged: GPU 88°C / CPU
        # 87°C under real training load must render as a plain checkmark, never a
        # warning, against the shipped default thresholds.
        lines = frame(snap_with([_GPU_OK, _CPU_OK]), CFG_KO)
        self.assertNotIn("⚠️", "\n".join(lines))
        self.assertNotIn("🚨", "\n".join(lines))

    def test_unknown_temp_shows_question_mark_not_crash(self):
        unknown = TempStat(key="gpu", label="GPU", temp_c=None, warn_c=95.0, crit_c=105.0)
        line = next(ln for ln in frame(snap_with([unknown]), CFG_KO) if "★GPU:" in ln)
        self.assertIn("?°C", line)


if __name__ == "__main__":
    unittest.main()
