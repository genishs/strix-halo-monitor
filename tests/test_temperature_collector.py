"""Tests for TemperatureCollector + threshold logic (Phase 7).

Builds a throwaway ``sys/class/hwmon/hwmon*/`` tree in a tmp dir and points
``ctx.root`` at it, so GPU/CPU/NVMe auto-detection (by ``name`` file content, never
hwmon number) and the garbage-threshold guard are verified deterministically
without touching real hardware. Same style as ``test_battery_collector.py``.

Fixture values mirror this project's own dev box, captured 2026-08-29 during a 34h
GPU campaign:

    hwmon12 [amdgpu]      edge      = 88°C
    hwmon8  [k10temp]     Tctl      = 87°C
    hwmon4  [nvme]        Composite = 53°C  (Sensor1 54, Sensor2 57)
    hwmon5  [nvme]        Composite = 64°C  (Sensor1 61, Sensor2 64)

and the specific garbage-threshold trap measured on that box:

    [nvme] temp2_max = 65261°C   <- overflowed sentinel, must never be used as-is
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.temperature import (
    TemperatureCollector, alert_level, hwmon_dirs_named, pick_temp_n, probe,
)
from halo_monitor.config import Config


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class FakeHwmonRoot:
    """A tmp filesystem root with ``sys/class/hwmon/hwmon<N>/*`` files."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="halo-temp-")
        self._n = 0

    def add_chip(self, name: str, sensors: dict[int, dict[str, object]]) -> str:
        """``sensors`` maps tempN -> {"label": ..., "input": ..., "max": ..., "crit": ...}
        (values are °C; ``label`` omitted means no ``tempN_label`` file at all)."""
        hwmon_dir = os.path.join(self.root, "sys/class/hwmon", f"hwmon{self._n}")
        self._n += 1
        _write(os.path.join(hwmon_dir, "name"), name + "\n")
        for n, files in sensors.items():
            for key, value in files.items():
                if key == "label":
                    _write(os.path.join(hwmon_dir, f"temp{n}_label"), f"{value}\n")
                else:
                    # sysfs stores millidegrees C
                    _write(os.path.join(hwmon_dir, f"temp{n}_{key}"), f"{int(value * 1000)}\n")
        return hwmon_dir

    def ctx(self, **cfg_kwargs) -> CollectContext:
        cfg = Config(**cfg_kwargs)
        return CollectContext(cfg=cfg, backend=None, root=self.root)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class TempFixtureTestCase(unittest.TestCase):
    def setUp(self):
        self.fs = FakeHwmonRoot()

    def tearDown(self):
        self.fs.cleanup()


# --- pure threshold/verdict logic (no filesystem) -------------------------- #
class TestAlertLevel(unittest.TestCase):
    def test_ok_below_warn(self):
        self.assertEqual(alert_level(88.0, 95.0, 105.0), "ok")

    def test_warn_at_or_above_warn(self):
        self.assertEqual(alert_level(95.0, 95.0, 105.0), "warn")
        self.assertEqual(alert_level(100.0, 95.0, 105.0), "warn")

    def test_crit_at_or_above_crit(self):
        self.assertEqual(alert_level(105.0, 95.0, 105.0), "crit")
        self.assertEqual(alert_level(120.0, 95.0, 105.0), "crit")

    def test_unknown_temp_is_ok(self):
        self.assertEqual(alert_level(None, 95.0, 105.0), "ok")

    def test_normal_training_load_does_not_trip(self):
        # The exact readings this box saw mid-campaign must stay "ok" against
        # the shipped defaults, or the alert is useless noise during real work.
        self.assertEqual(alert_level(88.0, 95.0, 105.0), "ok")   # GPU edge
        self.assertEqual(alert_level(87.0, 95.0, 105.0), "ok")   # CPU Tctl


# --- hwmon discovery (filesystem) ------------------------------------------ #
class TestDiscovery(TempFixtureTestCase):
    def test_finds_chips_by_name_not_hwmon_number(self):
        # Deliberately out-of-order hwmon numbers relative to a "natural" GPU/CPU
        # ordering, to prove content-matching (not number) is what's used.
        self.fs.add_chip("k10temp", {1: {"label": "Tctl", "input": 87.0}})
        self.fs.add_chip("amdgpu", {1: {"label": "edge", "input": 88.0}})
        self.assertEqual(len(hwmon_dirs_named(self.fs.root, "amdgpu")), 1)
        self.assertEqual(len(hwmon_dirs_named(self.fs.root, "k10temp")), 1)
        self.assertEqual(hwmon_dirs_named(self.fs.root, "nvme"), [])

    def test_multiple_nvme_chips_all_found(self):
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 53.0}})
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 64.0}})
        self.assertEqual(len(hwmon_dirs_named(self.fs.root, "nvme")), 2)

    def test_pick_temp_n_matches_label_case_insensitively(self):
        d = self.fs.add_chip("amdgpu", {
            1: {"label": "edge", "input": 88.0},
            2: {"label": "junction", "input": 95.0},
        })
        self.assertEqual(pick_temp_n(d, "EDGE"), 1)
        self.assertEqual(pick_temp_n(d, "edge"), 1)

    def test_pick_temp_n_none_when_labels_exist_but_not_wanted_one(self):
        # A chip with labels but not the one we asked for: never substitute a
        # different physical sensor (e.g. junction for edge).
        d = self.fs.add_chip("amdgpu", {2: {"label": "junction", "input": 95.0}})
        self.assertIsNone(pick_temp_n(d, "edge"))

    def test_pick_temp_n_falls_back_to_first_input_when_unlabelled(self):
        d = self.fs.add_chip("acpitz", {1: {"input": 87.0}})  # no *_label file at all
        self.assertEqual(pick_temp_n(d, "whatever"), 1)


# --- end-to-end probe() ------------------------------------------------------ #
class TestProbe(TempFixtureTestCase):
    def test_full_realistic_fixture(self):
        self.fs.add_chip("amdgpu", {1: {"label": "edge", "input": 88.0},
                                     2: {"label": "junction", "input": 95.0}})
        self.fs.add_chip("k10temp", {1: {"label": "Tctl", "input": 87.0}})
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 53.0},
                                   2: {"label": "Sensor1", "input": 54.0}})
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 64.0},
                                   2: {"label": "Sensor1", "input": 61.0}})
        cfg = Config()
        temps = probe(self.fs.root, cfg)
        by_key = {t.key: t for t in temps}

        self.assertEqual(by_key["gpu"].label, "GPU")
        self.assertAlmostEqual(by_key["gpu"].temp_c, 88.0)
        self.assertEqual(by_key["gpu"].alert, "ok")

        self.assertEqual(by_key["cpu"].label, "CPU")
        self.assertAlmostEqual(by_key["cpu"].temp_c, 87.0)
        self.assertEqual(by_key["cpu"].alert, "ok")

        self.assertEqual(by_key["nvme0"].label, "NVMe1")
        self.assertAlmostEqual(by_key["nvme0"].temp_c, 53.0)
        self.assertEqual(by_key["nvme1"].label, "NVMe2")
        self.assertAlmostEqual(by_key["nvme1"].temp_c, 64.0)

    def test_single_nvme_drive_labelled_without_number(self):
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 53.0}})
        temps = probe(self.fs.root, Config())
        self.assertEqual(temps[0].label, "NVMe")

    def test_no_sensors_present_yields_empty_list_not_a_crash(self):
        self.assertEqual(probe(self.fs.root, Config()), [])

    def test_missing_hwmon_dir_yields_empty_not_a_crash(self):
        # root exists but sys/class/hwmon does not
        os.makedirs(os.path.join(self.fs.root, "sys/class"), exist_ok=True)
        self.assertEqual(probe(self.fs.root, Config()), [])

    # --- the garbage-threshold trap ------------------------------------- #
    def test_garbage_nvme_max_is_ignored_in_favour_of_config_default(self):
        # Reproduces the measured trap: temp2_max = 65261 (deg C) is an obvious
        # overflowed sentinel, not a real threshold. If unfiltered, the alert
        # could never fire for this drive no matter how hot it gets.
        self.fs.add_chip("nvme", {2: {"label": "Composite", "input": 53.0, "max": 65261.0}})
        cfg = Config(nvme_temp_warn_c=70.0, nvme_temp_crit_c=80.0)
        temps = probe(self.fs.root, cfg)
        self.assertEqual(len(temps), 1)
        self.assertEqual(temps[0].warn_c, 70.0)   # fell back to the sane config default
        self.assertEqual(temps[0].alert, "ok")

    def test_garbage_threshold_does_not_suppress_a_real_hot_reading(self):
        # Same garbage temp2_max, but this time the drive really is hot: the
        # alert must still fire off the config default, not the garbage value.
        self.fs.add_chip("nvme", {2: {"label": "Composite", "input": 85.0, "max": 65261.0}})
        cfg = Config(nvme_temp_warn_c=70.0, nvme_temp_crit_c=80.0)
        temps = probe(self.fs.root, cfg)
        self.assertEqual(temps[0].alert, "crit")

    def test_sane_device_crit_is_honoured_over_config_default(self):
        # A device-reported crit that IS plausible should win over the config
        # default (more accurate, drive-specific knowledge).
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 75.0, "crit": 78.0}})
        cfg = Config(nvme_temp_warn_c=70.0, nvme_temp_crit_c=80.0)
        temps = probe(self.fs.root, cfg)
        self.assertEqual(temps[0].crit_c, 78.0)
        self.assertEqual(temps[0].alert, "warn")  # >= warn(70) but < device crit(78)

    def test_negative_garbage_threshold_also_filtered(self):
        self.fs.add_chip("nvme", {1: {"label": "Composite", "input": 53.0, "crit": -40.0}})
        cfg = Config(nvme_temp_warn_c=70.0, nvme_temp_crit_c=80.0)
        temps = probe(self.fs.root, cfg)
        self.assertEqual(temps[0].crit_c, 80.0)


class TestCollector(TempFixtureTestCase):
    def test_available_true_when_any_sensor_present(self):
        self.fs.add_chip("k10temp", {1: {"label": "Tctl", "input": 87.0}})
        self.assertTrue(TemperatureCollector().available(self.fs.ctx()))

    def test_available_false_on_empty_root(self):
        self.assertFalse(TemperatureCollector().available(self.fs.ctx()))

    def test_collect_matches_probe(self):
        self.fs.add_chip("amdgpu", {1: {"label": "edge", "input": 88.0}})
        ctx = self.fs.ctx()
        result = TemperatureCollector().collect(ctx)
        self.assertEqual(result, probe(self.fs.root, ctx.cfg))

    def test_never_raises_on_unreadable_root(self):
        ctx = CollectContext(cfg=Config(), backend=None, root="/dev/null/not/a/real/path")
        self.assertFalse(TemperatureCollector().available(ctx))
        self.assertEqual(TemperatureCollector().collect(ctx), [])


if __name__ == "__main__":
    unittest.main()
