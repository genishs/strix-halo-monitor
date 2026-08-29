"""Tests for the update loop's delta math, resilience, and flags (loop.py).

Uses fake collectors + injected clocks — no hardware. The concrete collectors and their
sysfs-fixture tests are delivered separately (Phase 2 collectors)."""

import unittest

import _util  # noqa: F401

from halo_monitor.config import config_from_env
from halo_monitor.loop import UpdateLoop
from halo_monitor.model import (
    BatteryStat, ClockStats, DiskStat, JobState, JobType, MemoryStats, RawNetIface, RawPower,
    TempStat,
)


class _Fake:
    def __init__(self, value):
        self.value = value
        self.name = "fake"

    def available(self, ctx):
        return True

    def collect(self, ctx):
        v = self.value() if callable(self.value) else self.value
        if isinstance(v, Exception):
            raise v
        return v


def make_loop(mem, raw, clk, job=None, disks=None, net=None, battery=None, temps=None):
    cfg = config_from_env(env={})
    return UpdateLoop(
        cfg,
        backend=None,
        memory=_Fake(mem),
        power=_Fake(raw),
        clocks=_Fake(clk),
        disk=_Fake([] if disks is None else disks),
        network=_Fake([] if net is None else net),
        battery=_Fake(BatteryStat() if battery is None else battery),
        temperature=None if temps is None else _Fake(temps),
        job_provider=lambda now: job,
        renderer=lambda snap: None,
    )


class TestLoopDeltas(unittest.TestCase):
    def test_gtt_rate_needs_two_samples(self):
        seq = [MemoryStats(gtt_used_bytes=1048576 * 100),
               MemoryStats(gtt_used_bytes=1048576 * 130)]
        it = iter(seq)
        loop = make_loop(lambda: next(it), RawPower(), ClockStats())
        s1 = loop.tick(now_mono=0.0, now_wall=1000.0)
        self.assertIsNone(s1.memory.gtt_rate_mb_s)          # first tick: no delta
        s2 = loop.tick(now_mono=2.0, now_wall=1002.0)
        self.assertAlmostEqual(s2.memory.gtt_rate_mb_s, 15.0)  # +30MB over 2s

    def test_rapl_watts_and_wraparound(self):
        # tick1 primes counters; tick2 computes; tick3 wraparound -> skip.
        seq = [
            RawPower(pkg_uj=1_000_000, core_uj=400_000, amdgpu_w=88.0),
            RawPower(pkg_uj=1_000_000 + 218_000_000, core_uj=400_000 + 44_000_000, amdgpu_w=90.0),
            RawPower(pkg_uj=5_000, core_uj=1_000, amdgpu_w=91.0),  # counter reset (wrap)
        ]
        it = iter(seq)
        loop = make_loop(MemoryStats(), lambda: next(it), ClockStats())
        s1 = loop.tick(0.0, 1000.0)
        self.assertIsNone(s1.power.cpu_w)
        self.assertEqual(s1.power.total_w, 88.0)             # fallback = amdgpu hwmon
        s2 = loop.tick(2.0, 1002.0)
        self.assertAlmostEqual(s2.power.total_w, 109.0)      # 218MJ/1e6/2s
        self.assertAlmostEqual(s2.power.cpu_w, 22.0)         # 44MJ/1e6/2s
        self.assertAlmostEqual(s2.power.gpu_w, 87.0)         # total - cpu
        s3 = loop.tick(4.0, 1004.0)
        self.assertIsNone(s3.power.cpu_w)                    # wraparound skipped
        self.assertEqual(s3.power.total_w, 91.0)             # fallback again

    def test_resilience_collector_raises(self):
        loop = make_loop(lambda: RuntimeError("boom"), RawPower(), ClockStats())
        snap = loop.tick(0.0, 1000.0)                        # must not raise
        self.assertIsInstance(snap.memory, MemoryStats)
        self.assertIsNone(snap.memory.gtt_used_bytes)

    def test_flags(self):
        job = JobState(job_type=JobType.TRAIN, error_count=2)
        loop = make_loop(MemoryStats(ram_free_gb=1.5), RawPower(), ClockStats(), job=job)
        snap = loop.tick(0.0, 1000.0)
        self.assertTrue(snap.flags.ram_low)                  # 1.5 < 3.0
        self.assertTrue(snap.flags.has_error)                # error_count > 0
        self.assertFalse(snap.flags.disk_low)                # no disks -> not low
        self.assertEqual(snap.ts, 1000.0)

    def test_disk_low_flag_and_passthrough(self):
        disks = [
            DiskStat(path="/", label="/", total_bytes=100, free_bytes=90, low=False),
            DiskStat(path="/mnt/data", label="/mnt/data",
                     total_bytes=100, free_bytes=1, low=True),
        ]
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), disks=disks)
        snap = loop.tick(0.0, 1000.0)
        self.assertEqual(snap.disks, disks)                  # collected into Snapshot
        self.assertTrue(snap.flags.disk_low)                 # any(d.low) -> True

    def test_disk_collector_raising_is_survived(self):
        loop = make_loop(MemoryStats(), RawPower(), ClockStats())
        loop.disk = _Fake(RuntimeError("statvfs boom"))
        snap = loop.tick(0.0, 1000.0)                        # must not raise
        self.assertEqual(snap.disks, [])                     # blank for the tick
        self.assertFalse(snap.flags.disk_low)

    def test_battery_passthrough_and_flag_ok(self):
        bat = BatteryStat(present=True, capacity_pct=100, status="Full", alert="ok")
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), battery=bat)
        snap = loop.tick(0.0, 1000.0)
        self.assertIs(snap.battery, bat)                    # collected into Snapshot verbatim
        self.assertFalse(snap.flags.battery_low)

    def test_battery_warn_and_crit_set_the_flag(self):
        for alert in ("warn", "crit"):
            bat = BatteryStat(present=True, capacity_pct=10, discharging=True, alert=alert)
            loop = make_loop(MemoryStats(), RawPower(), ClockStats(), battery=bat)
            snap = loop.tick(0.0, 1000.0)
            self.assertTrue(snap.flags.battery_low, alert)

    def test_battery_collector_raising_is_survived(self):
        loop = make_loop(MemoryStats(), RawPower(), ClockStats())
        loop.battery = _Fake(RuntimeError("sysfs boom"))
        snap = loop.tick(0.0, 1000.0)                        # must not raise
        self.assertFalse(snap.battery.present)               # blank default for the tick
        self.assertFalse(snap.flags.battery_low)

    def test_no_temperature_collector_wired_yields_empty_temps(self):
        # temperature is an optional collaborator (default None) so existing
        # UpdateLoop construction sites don't need updating for this feature.
        loop = make_loop(MemoryStats(), RawPower(), ClockStats())
        snap = loop.tick(0.0, 1000.0)
        self.assertEqual(snap.temps, [])
        self.assertFalse(snap.flags.temp_hot)

    def test_temp_passthrough_and_flag_ok(self):
        temps = [TempStat(key="gpu", label="GPU", temp_c=88.0, warn_c=95.0, crit_c=105.0, alert="ok")]
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), temps=temps)
        snap = loop.tick(0.0, 1000.0)
        self.assertIs(snap.temps, temps)
        self.assertFalse(snap.flags.temp_hot)

    def test_temp_warn_and_crit_set_the_flag(self):
        for alert in ("warn", "crit"):
            temps = [TempStat(key="gpu", label="GPU", temp_c=100.0,
                               warn_c=95.0, crit_c=105.0, alert=alert)]
            loop = make_loop(MemoryStats(), RawPower(), ClockStats(), temps=temps)
            snap = loop.tick(0.0, 1000.0)
            self.assertTrue(snap.flags.temp_hot, alert)

    def test_temp_collector_raising_is_survived(self):
        temps = [TempStat(key="gpu", label="GPU", temp_c=88.0, warn_c=95.0, crit_c=105.0)]
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), temps=temps)
        loop.temperature = _Fake(RuntimeError("hwmon boom"))
        snap = loop.tick(0.0, 1000.0)                        # must not raise
        self.assertEqual(snap.temps, [])                     # blank for the tick
        self.assertFalse(snap.flags.temp_hot)


class TestNetDeltas(unittest.TestCase):
    def test_net_rate_needs_two_samples(self):
        MB = 1048576
        seq = [
            [RawNetIface(name="eth0", label="eth0", rx_bytes=100 * MB,
                         tx_bytes=10 * MB, present=True)],
            [RawNetIface(name="eth0", label="eth0", rx_bytes=130 * MB,
                         tx_bytes=14 * MB, present=True)],
        ]
        it = iter(seq)
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), net=lambda: next(it))
        s1 = loop.tick(0.0, 1000.0)
        self.assertIsNone(s1.net[0].rx_mb_s)                 # first tick: no delta
        self.assertEqual(s1.net[0].rx_session_bytes, 0)      # baseline == current
        s2 = loop.tick(2.0, 1002.0)
        self.assertAlmostEqual(s2.net[0].rx_mb_s, 15.0)      # +30MB over 2s
        self.assertAlmostEqual(s2.net[0].tx_mb_s, 2.0)       # +4MB over 2s
        self.assertEqual(s2.net[0].rx_session_bytes, 30 * MB)  # since first sight

    def test_net_counter_reset_yields_none_rate(self):
        MB = 1048576
        seq = [
            [RawNetIface(name="wlan0", label="wlan0", rx_bytes=500 * MB,
                         tx_bytes=50 * MB, present=True)],
            [RawNetIface(name="wlan0", label="wlan0", rx_bytes=5 * MB,
                         tx_bytes=1 * MB, present=True)],   # counter reset (iface down/up)
        ]
        it = iter(seq)
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), net=lambda: next(it))
        loop.tick(0.0, 1000.0)
        s2 = loop.tick(2.0, 1002.0)
        self.assertIsNone(s2.net[0].rx_mb_s)                 # negative delta -> skip
        self.assertIsNone(s2.net[0].tx_mb_s)

    def test_net_absent_iface_passthrough(self):
        net = [RawNetIface(name="eth0", label="eth0", present=False)]
        loop = make_loop(MemoryStats(), RawPower(), ClockStats(), net=net)
        snap = loop.tick(0.0, 1000.0)
        self.assertFalse(snap.net[0].present)
        self.assertIsNone(snap.net[0].rx_mb_s)
        self.assertEqual(snap.net[0].label, "eth0")

    def test_net_collector_raising_is_survived(self):
        loop = make_loop(MemoryStats(), RawPower(), ClockStats())
        loop.network = _Fake(RuntimeError("net boom"))
        snap = loop.tick(0.0, 1000.0)                        # must not raise
        self.assertEqual(snap.net, [])                       # blank for the tick


if __name__ == "__main__":
    unittest.main()
