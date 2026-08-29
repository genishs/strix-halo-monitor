"""Tests for BatteryCollector + threshold logic (Phase 6).

Builds a throwaway ``power_supply/*`` tree in a tmp dir and points ``ctx.root`` at
it, so charger/battery auto-detection and the discharge-power/threshold logic are
verified deterministically without touching a real battery. This also documents
the C2 invariant at the test level: the collector only ever reads a handful of
sysfs text files — there is no ``upower``/``acpi`` subprocess path to exercise.

Fixture values for the "present, fully charged, not discharging" cases mirror this
project's own dev box, captured 2026-08-29 while a 34h GPU campaign was running:

    ADP0/online=1  BAT0/status=Full  BAT0/capacity=100  BAT0/power_now=0
    BAT0/energy_now=68107000  BAT0/energy_full=68107000  BAT0/voltage_now=17482000
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import _util  # noqa: F401

from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.battery import (
    BatteryCollector, alert_level, any_ac_online, find_battery, is_discharging_status,
    list_power_supplies, time_remaining_s,
)
from halo_monitor.config import Config


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class FakePowerSupplyRoot:
    """A tmp filesystem root with ``sys/class/power_supply/<name>/*`` files."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="halo-battery-")

    def add_supply(self, name: str, type_: str, **files: object) -> None:
        base = os.path.join(self.root, "sys/class/power_supply", name)
        _write(os.path.join(base, "type"), type_ + "\n")
        for key, value in files.items():
            _write(os.path.join(base, key), f"{value}\n")

    def ctx(self, **cfg_kwargs) -> CollectContext:
        cfg = Config(**cfg_kwargs)
        return CollectContext(cfg=cfg, backend=None, root=self.root)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class BatteryFixtureTestCase(unittest.TestCase):
    def setUp(self):
        self.fs = FakePowerSupplyRoot()

    def tearDown(self):
        self.fs.cleanup()


# --- pure threshold/verdict logic (no filesystem) -------------------------- #
class TestAlertLevel(unittest.TestCase):
    def test_ok_when_full_and_not_discharging(self):
        self.assertEqual(alert_level(100, False, 30.0, 15.0), "ok")

    def test_discharging_is_always_at_least_warn(self):
        self.assertEqual(alert_level(90, True, 30.0, 15.0), "warn")  # even at 90%

    def test_below_warn_pct_while_not_discharging_is_warn(self):
        self.assertEqual(alert_level(20, False, 30.0, 15.0), "warn")

    def test_below_crit_pct_is_crit_even_while_charging(self):
        self.assertEqual(alert_level(10, False, 30.0, 15.0), "crit")

    def test_discharging_below_crit_pct_is_crit(self):
        self.assertEqual(alert_level(5, True, 30.0, 15.0), "crit")

    def test_unknown_capacity_never_trips_pct_floors(self):
        self.assertEqual(alert_level(None, False, 30.0, 15.0), "ok")
        self.assertEqual(alert_level(None, True, 30.0, 15.0), "warn")  # discharging still warns


class TestIsDischargingStatus(unittest.TestCase):
    def test_case_insensitive_match(self):
        self.assertTrue(is_discharging_status("Discharging"))
        self.assertTrue(is_discharging_status("discharging"))

    def test_other_statuses_false(self):
        self.assertFalse(is_discharging_status("Full"))
        self.assertFalse(is_discharging_status("Charging"))
        self.assertFalse(is_discharging_status("Not charging"))

    def test_none_is_none(self):
        self.assertIsNone(is_discharging_status(None))


class TestTimeRemaining(unittest.TestCase):
    def test_computes_hours_at_rate(self):
        # 68.107 Wh / 34W ~= 2.003h -> 7212s
        self.assertEqual(time_remaining_s(68.107, 34.0), 7211)

    def test_none_when_not_discharging_or_unknown(self):
        self.assertIsNone(time_remaining_s(None, 34.0))
        self.assertIsNone(time_remaining_s(68.107, None))
        self.assertIsNone(time_remaining_s(68.107, 0.0))


# --- power_supply discovery (filesystem) ----------------------------------- #
class TestDiscovery(BatteryFixtureTestCase):
    def test_finds_battery_and_ac_by_type_not_name(self):
        # Deliberately non-standard names: the fix must not hardcode BAT0/ADP0.
        self.fs.add_supply("weird-batt-7", "Battery", capacity=50, status="Full")
        self.fs.add_supply("weird-charger-3", "Mains", online=1)
        names = list_power_supplies(self.fs.root)
        self.assertEqual(find_battery(self.fs.root, names), "weird-batt-7")
        self.assertTrue(any_ac_online(self.fs.root, names, "weird-batt-7"))

    def test_usb_pd_online_zero_is_not_mistaken_for_charging(self):
        # Reproduces this box's real ucsi-source-psy-* nodes: online=0, no current.
        self.fs.add_supply("BAT0", "Battery", capacity=100, status="Full")
        self.fs.add_supply("ADP0", "Mains", online=1)
        self.fs.add_supply("ucsi-source-psy-USBC000:001", "USB", online=0)
        self.fs.add_supply("ucsi-source-psy-USBC000:002", "USB", online=0)
        names = list_power_supplies(self.fs.root)
        self.assertTrue(any_ac_online(self.fs.root, names, "BAT0"))  # ADP0 wins

    def test_no_battery_type_present(self):
        self.fs.add_supply("ADP0", "Mains", online=1)
        names = list_power_supplies(self.fs.root)
        self.assertIsNone(find_battery(self.fs.root, names))

    def test_ac_unknown_when_no_online_file_readable(self):
        self.fs.add_supply("BAT0", "Battery", capacity=50)
        names = list_power_supplies(self.fs.root)
        self.assertIsNone(any_ac_online(self.fs.root, names, "BAT0"))

    def test_empty_dir_yields_no_supplies_not_a_crash(self):
        os.makedirs(os.path.join(self.fs.root, "sys/class/power_supply"))
        self.assertEqual(list_power_supplies(self.fs.root), [])

    def test_missing_power_supply_dir_yields_empty_not_a_crash(self):
        self.assertEqual(list_power_supplies(self.fs.root), [])


# --- BatteryCollector end-to-end -------------------------------------------- #
class TestBatteryCollectorNoHardware(BatteryFixtureTestCase):
    """The desktop/mini-PC case: must not crash, must not render anything."""

    def test_available_false_and_present_false(self):
        self.fs.add_supply("ADP0", "Mains", online=1)  # some boxes have AC but no BAT
        ctx = self.fs.ctx()
        c = BatteryCollector()
        self.assertFalse(c.available(ctx))
        stat = c.collect(ctx)
        self.assertFalse(stat.present)
        self.assertIsNone(stat.capacity_pct)
        self.assertEqual(stat.alert, "ok")

    def test_totally_empty_root(self):
        ctx = self.fs.ctx()
        c = BatteryCollector()
        self.assertFalse(c.available(ctx))
        self.assertFalse(c.collect(ctx).present)


class TestBatteryCollectorRealBoxFixture(BatteryFixtureTestCase):
    """Exact readings captured from this dev box: charger connected, Full, 0W."""

    def setUp(self):
        super().setUp()
        self.fs.add_supply(
            "BAT0", "Battery",
            status="Full", capacity=100, power_now=0,
            energy_now=68107000, energy_full=68107000, voltage_now=17482000,
        )
        self.fs.add_supply("ADP0", "Mains", online=1)
        self.fs.add_supply("ucsi-source-psy-USBC000:001", "USB", online=0)
        self.fs.add_supply("ucsi-source-psy-USBC000:002", "USB", online=0)

    def test_present_full_no_discharge_ok(self):
        stat = BatteryCollector().collect(self.fs.ctx())
        self.assertTrue(stat.present)
        self.assertTrue(stat.ac_online)
        self.assertEqual(stat.status, "Full")
        self.assertEqual(stat.capacity_pct, 100)
        self.assertFalse(stat.discharging)
        self.assertEqual(stat.discharge_w, 0.0)
        self.assertIsNone(stat.time_remaining_s)
        self.assertEqual(stat.alert, "ok")

    def test_available_true(self):
        self.assertTrue(BatteryCollector().available(self.fs.ctx()))


class TestBatteryCollectorDischarging(BatteryFixtureTestCase):
    """The scenario this widget exists for: charger present but insufficient."""

    def setUp(self):
        super().setUp()
        # 100W charger under load -> drawing ~34W from the battery despite AC online.
        self.fs.add_supply(
            "BAT0", "Battery",
            status="Discharging", capacity=28, power_now=34_000_000,
            energy_now=19_070_000, voltage_now=17_000_000,
        )
        self.fs.add_supply("ADP0", "Mains", online=1)

    def test_discharging_while_ac_online_is_visible(self):
        stat = BatteryCollector().collect(self.fs.ctx())
        self.assertTrue(stat.present)
        self.assertTrue(stat.ac_online)          # charger IS connected...
        self.assertTrue(stat.discharging)        # ...but the box is still draining it
        self.assertAlmostEqual(stat.discharge_w, 34.0)
        self.assertIsNotNone(stat.time_remaining_s)
        self.assertGreater(stat.time_remaining_s, 0)

    def test_warn_alert_below_30pct(self):
        stat = BatteryCollector().collect(self.fs.ctx(battery_warn_pct=30.0, battery_crit_pct=15.0))
        self.assertEqual(stat.alert, "warn")     # 28% < 30% and discharging

    def test_crit_alert_below_15pct(self):
        self.fs.add_supply(
            "BAT0", "Battery",
            status="Discharging", capacity=6, power_now=34_000_000,
            energy_now=4_000_000, voltage_now=17_000_000,
        )
        stat = BatteryCollector().collect(self.fs.ctx(battery_warn_pct=30.0, battery_crit_pct=15.0))
        self.assertEqual(stat.alert, "crit")     # 6% < 15% -> critical


class TestBatteryCollectorNoAcNoStatus(BatteryFixtureTestCase):
    """Fuel gauges that expose no ``status`` file: infer from AC presence."""

    def setUp(self):
        super().setUp()
        self.fs.add_supply("BAT0", "Battery", capacity=40, current_now=2_000_000, voltage_now=12_000_000)
        # No Mains/USB entry at all -> AC presence unknown -> any_ac_online returns None.

    def test_unknown_ac_and_status_defaults_to_not_discharging(self):
        stat = BatteryCollector().collect(self.fs.ctx())
        self.assertIsNone(stat.ac_online)
        self.assertFalse(stat.discharging)       # don't cry wolf on pure unknowns
        self.assertEqual(stat.discharge_w, 0.0)  # raw reading known, but masked to 0 (not discharging)


class TestBatteryCollectorAcExplicitlyOff(BatteryFixtureTestCase):
    def setUp(self):
        super().setUp()
        self.fs.add_supply("BAT0", "Battery", capacity=55, current_now=1_500_000, voltage_now=12_000_000)
        self.fs.add_supply("ADP0", "Mains", online=0)

    def test_no_status_but_ac_confirmed_off_infers_discharging(self):
        stat = BatteryCollector().collect(self.fs.ctx())
        self.assertFalse(stat.ac_online)
        self.assertTrue(stat.discharging)
        self.assertAlmostEqual(stat.discharge_w, 18.0)  # 1.5A * 12V


class TestBatteryCollectorCurrentVoltageFallback(BatteryFixtureTestCase):
    """Hardware exposing only current_now/voltage_now (no power_now)."""

    def test_power_computed_from_current_times_voltage(self):
        self.fs.add_supply(
            "BAT0", "Battery",
            status="Discharging", capacity=50,
            current_now=2_500_000, voltage_now=16_000_000,  # 2.5A * 16V = 40W
        )
        stat = BatteryCollector().collect(self.fs.ctx())
        self.assertAlmostEqual(stat.discharge_w, 40.0)


class TestBatteryCollectorChargeOnlyFallback(BatteryFixtureTestCase):
    """Hardware exposing charge_now (µAh) instead of energy_now (µWh)."""

    def test_time_remaining_via_charge_now_times_voltage(self):
        self.fs.add_supply(
            "BAT0", "Battery",
            status="Discharging", capacity=30,
            power_now=20_000_000, charge_now=2_000_000, voltage_now=12_000_000,
        )
        stat = BatteryCollector().collect(self.fs.ctx())
        # energy_now = 2Ah * 12V = 24Wh; at 20W -> 1.2h = 4320s
        self.assertIsNotNone(stat.time_remaining_s)
        self.assertAlmostEqual(stat.time_remaining_s, 4320, delta=2)


if __name__ == "__main__":
    unittest.main()
