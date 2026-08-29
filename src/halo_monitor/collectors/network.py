"""Network-throughput collector: per-interface RX/TX byte counters (Phase 5).

C2 INVARIANT (training/downloads must not be disturbed): this collector reads
throughput information from the kernel's own cumulative byte counters at
``/sys/class/net/<iface>/statistics/{rx,tx}_bytes`` **only**. It performs NO
packet capture, runs NO ``tcpdump``/``ip``/``ethtool``, and opens NO socket, so a
tick costs effectively zero and never competes with a running training job or a
large model download for the link. Do NOT add any wire-touching probe here.

Like every other collector it is read-only, stateless (keeps no previous sample —
rate/session math is ``loop.py``'s job), and non-raising: an interface whose
counters cannot be read (absent / renamed / permission) comes back as
``present=False`` rather than an exception.

Interface selection reads only sysfs/procfs under the injected root, so tests
exercise auto-detection deterministically against a fixture tree.
"""

from __future__ import annotations

import os

from ..config import NET_AUTO_ALL, Config, NetTarget
from ..model import RawNetIface
from .base import CollectContext

_LOOPBACK = "lo"


def _read_int(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _list_non_loopback(root: str) -> list[str]:
    """Every interface under ``<root>/sys/class/net`` except loopback, sorted."""
    net_dir = os.path.join(root, "sys/class/net")
    try:
        names = os.listdir(net_dir)
    except OSError:
        return []
    return sorted(n for n in names if n != _LOOPBACK)


def _default_route_ifaces(root: str) -> list[str]:
    """Interfaces carrying a default route, parsed from ``<root>/proc/net/route``.

    The default route is the row whose hex Destination is all-zeros (``00000000``).
    Returns the matching interface names in file order, de-duplicated. Empty on any
    read/parse problem (caller then falls back to all non-loopback interfaces).
    """
    path = os.path.join(root, "proc/net/route")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out: list[str] = []
    for line in lines[1:]:  # skip the header row
        fields = line.split()
        if len(fields) < 2:
            continue
        iface, destination = fields[0], fields[1]
        if destination == "00000000" and iface != _LOOPBACK and iface not in out:
            out.append(iface)
    return out


class NetworkCollector:
    """Collector for per-interface RX/TX byte counters (statistics-file-only)."""

    name = "network"

    def available(self, ctx: CollectContext) -> bool:
        # Disabled only when the user explicitly cleared the interface list ("()").
        # None (auto-detect) and a non-empty explicit list are both available.
        return ctx.cfg.net_ifaces != ()

    def _targets(self, ctx: CollectContext) -> list[NetTarget]:
        """Resolve the interfaces to probe this tick (explicit, or auto-detected)."""
        cfg: Config = ctx.cfg
        if cfg.net_ifaces is not None:
            return list(cfg.net_ifaces)
        # Auto-detect. Labels stay None so the renderer shows the bare iface name.
        if cfg.net_auto == NET_AUTO_ALL:
            names = _list_non_loopback(ctx.root)
        else:
            names = _default_route_ifaces(ctx.root) or _list_non_loopback(ctx.root)
        return [NetTarget(name=n) for n in names]

    def collect(self, ctx: CollectContext) -> list[RawNetIface]:
        return [self._probe(t, ctx.root) for t in self._targets(ctx)]

    def _probe(self, target: NetTarget, root: str) -> RawNetIface:
        stats = os.path.join(root, "sys/class/net", target.name, "statistics")
        rx = _read_int(os.path.join(stats, "rx_bytes"))
        tx = _read_int(os.path.join(stats, "tx_bytes"))
        present = rx is not None and tx is not None
        return RawNetIface(
            name=target.name, label=target.label, rx_bytes=rx, tx_bytes=tx, present=present
        )
