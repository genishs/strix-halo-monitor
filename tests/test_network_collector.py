"""Tests for NetworkCollector: interface resolution + counter reads (Phase 5).

Builds a throwaway sysfs/procfs tree in a tmp dir and points ``ctx.root`` at it, so
auto-detection (default-route parsing, non-loopback discovery) and the byte-counter
reads are verified deterministically without touching real interfaces. This also
documents the C2 invariant at the test level: the collector only ever opens the
kernel's ``statistics/{rx,tx}_bytes`` files — there is no packet-capture path to test.
"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import _util  # noqa: F401

from halo_monitor.collectors.base import CollectContext
from halo_monitor.collectors.network import NetworkCollector
from halo_monitor.config import Config, NetTarget, config_from_env


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class _FakeNetRoot:
    """A tmp filesystem root with ``sys/class/net/*`` and ``proc/net/route``."""

    def __init__(self, ifaces, route_defaults=()):
        # ifaces: {name: (rx_bytes, tx_bytes) | None}. None => stats files absent.
        self.root = tempfile.mkdtemp(prefix="halo-net-")
        for name, counters in ifaces.items():
            base = os.path.join(self.root, "sys/class/net", name)
            os.makedirs(base, exist_ok=True)
            if counters is not None:
                rx, tx = counters
                _write(os.path.join(base, "statistics/rx_bytes"), f"{rx}\n")
                _write(os.path.join(base, "statistics/tx_bytes"), f"{tx}\n")
        header = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\t"
                  "Mask\tMTU\tWindow\tIRTT\n")
        rows = ""
        for iface in route_defaults:
            # default route: Destination = 00000000
            rows += f"{iface}\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
            # plus a non-default on-link row for the same iface (must be ignored)
            rows += f"{iface}\t0000A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
        _write(os.path.join(self.root, "proc/net/route"), header + rows)

    def ctx(self, **cfg_kwargs):
        cfg = Config(**cfg_kwargs)
        return CollectContext(cfg=cfg, backend=None, root=self.root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestNetworkCollector(unittest.TestCase):
    def setUp(self):
        self.fs = None

    def tearDown(self):
        if self.fs is not None:
            self.fs.cleanup()

    def test_explicit_iface_reads_counters(self):
        self.fs = _FakeNetRoot({"eth0": (1234, 567), "lo": (9, 9)})
        ctx = self.fs.ctx(net_ifaces=(NetTarget("eth0", "LAN"),))
        [r] = NetworkCollector().collect(ctx)
        self.assertTrue(r.present)
        self.assertEqual(r.name, "eth0")
        self.assertEqual(r.label, "LAN")
        self.assertEqual(r.rx_bytes, 1234)
        self.assertEqual(r.tx_bytes, 567)

    def test_absent_iface_is_present_false_not_raise(self):
        self.fs = _FakeNetRoot({"eth0": (1, 1)})
        ctx = self.fs.ctx(net_ifaces=(NetTarget("wlan0", "WiFi"),))
        [r] = NetworkCollector().collect(ctx)   # must not raise
        self.assertFalse(r.present)
        self.assertIsNone(r.rx_bytes)
        self.assertEqual(r.label, "WiFi")

    def test_auto_default_route_picks_default_iface(self):
        # Two ifaces exist; only wlan0 carries the default route -> auto "default".
        self.fs = _FakeNetRoot(
            {"eth0": (10, 20), "wlan0": (100, 200), "lo": (0, 0)},
            route_defaults=("wlan0",),
        )
        ctx = self.fs.ctx(net_ifaces=None, net_auto="default")
        rows = NetworkCollector().collect(ctx)
        self.assertEqual([r.name for r in rows], ["wlan0"])
        self.assertEqual(rows[0].rx_bytes, 100)
        self.assertIsNone(rows[0].label)        # auto-detected -> bare name in render

    def test_auto_default_falls_back_to_all_non_loopback(self):
        # No default route in the table -> fall back to every non-lo iface, sorted.
        self.fs = _FakeNetRoot({"eth0": (1, 1), "wlan0": (2, 2), "lo": (0, 0)})
        ctx = self.fs.ctx(net_ifaces=None, net_auto="default")
        rows = NetworkCollector().collect(ctx)
        self.assertEqual([r.name for r in rows], ["eth0", "wlan0"])  # lo excluded

    def test_auto_all_lists_every_non_loopback(self):
        self.fs = _FakeNetRoot(
            {"eth0": (1, 1), "wlan0": (2, 2), "lo": (0, 0)},
            route_defaults=("wlan0",),   # ignored in "all" mode
        )
        ctx = self.fs.ctx(net_ifaces=None, net_auto="all")
        rows = NetworkCollector().collect(ctx)
        self.assertEqual([r.name for r in rows], ["eth0", "wlan0"])

    def test_disabled_when_ifaces_empty(self):
        self.fs = _FakeNetRoot({"eth0": (1, 1)})
        ctx = self.fs.ctx(net_ifaces=())
        c = NetworkCollector()
        self.assertFalse(c.available(ctx))          # explicitly cleared -> off
        self.assertEqual(c.collect(ctx), [])

    def test_available_true_for_auto_and_explicit(self):
        self.fs = _FakeNetRoot({"eth0": (1, 1)})
        c = NetworkCollector()
        self.assertTrue(c.available(self.fs.ctx(net_ifaces=None)))
        self.assertTrue(c.available(self.fs.ctx(net_ifaces=(NetTarget("eth0"),))))

    def test_missing_sysfs_root_yields_empty_not_raise(self):
        # Auto-detect against a root with no /sys/class/net and no route table.
        empty = tempfile.mkdtemp(prefix="halo-net-empty-")
        try:
            ctx = CollectContext(cfg=Config(net_ifaces=None), backend=None, root=empty)
            self.assertEqual(NetworkCollector().collect(ctx), [])  # must not raise
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class TestNetConfigParsing(unittest.TestCase):
    def test_unset_env_is_auto(self):
        cfg = config_from_env(env={})
        self.assertIsNone(cfg.net_ifaces)           # None => auto-detect
        self.assertEqual(cfg.net_auto, "default")

    def test_empty_env_disables_widget(self):
        cfg = config_from_env(env={"HALO_NET_IFACES": ""})
        self.assertEqual(cfg.net_ifaces, ())

    def test_env_label_and_bare_names(self):
        cfg = config_from_env(env={"HALO_NET_IFACES": "유선=eth0 ; wlan0"})
        self.assertEqual(len(cfg.net_ifaces), 2)
        self.assertEqual(cfg.net_ifaces[0], NetTarget("eth0", "유선"))
        self.assertEqual(cfg.net_ifaces[1], NetTarget("wlan0", "wlan0"))

    def test_env_auto_all(self):
        cfg = config_from_env(env={"HALO_NET_AUTO": "all"})
        self.assertEqual(cfg.net_auto, "all")

    def test_env_auto_unknown_falls_back_to_default(self):
        cfg = config_from_env(env={"HALO_NET_AUTO": "bogus"})
        self.assertEqual(cfg.net_auto, "default")


if __name__ == "__main__":
    unittest.main()
