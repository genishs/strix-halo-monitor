"""Battery/AC power collector (Phase 6, DESIGN §3 "새 지표 추가").

C2 INVARIANT (training must not be disturbed): this collector reads a handful of
tiny text files under ``power_supply/*`` — the kernel's own cached fuel-gauge and
charger-online values — and nothing else. No ``upower``, no ``acpi``, no
subprocess, no polling loop of its own. A tick costs a few open/read/close calls on
files a few bytes long, so it is unmeasurably cheap next to a running GPU job.

Why this exists: an overnight 34h unattended training/scoring campaign was once
killed mid-run because the attached charger (100W) could not cover the box's full
load (~100W+), so the deficit was quietly drained from the battery until it hit 6%
and force-shut-down at 05:00 — logged only as "training stopped", with no power
telemetry anywhere to explain why. See ``BatteryStat`` in ``model.py`` for the full
account and ``memory/gfx1151-4bit-training.md``. This collector exists so that
exact condition — charger present but insufficient — becomes visible the instant
it starts, not after the box has already gone dark.

**Charger wattage is unreadable here on purpose — do not try to report it.** This
box's USB-PD ``ucsi-source-psy-*`` nodes report ``online=0`` and no current/voltage
even while genuinely delivering power (only the ``Mains``/``ADP0`` node's online
flag is real). Guessing "200W charger" from what little sysfs exposes would be a
lie dressed as data. Instead this reports **discharge watts**: however many watts
are actually flowing out of the battery right now, which is charger-agnostic and
tells the operator exactly what they need to know (draining or not, and how fast).

Device names are auto-detected by each ``power_supply/*/type`` file (``Battery`` /
``Mains`` / ``USB`` / ...) rather than hardcoded (``BAT0``, ``AC``, ``ADP0``, ...
vary by vendor) — the same fix ``collectors/mounts.py`` made for disks: a hardcoded
name list is exactly the kind of thing that goes invisible on someone else's box.
"""

from __future__ import annotations

import os

from ..model import BatteryStat
from .base import CollectContext

#: power_supply directory, relative to the injected root.
POWER_SUPPLY_DIR = "sys/class/power_supply"

_UWH_PER_WH = 1_000_000.0
_UW_PER_W = 1_000_000.0
_UA_PER_A = 1_000_000.0
_UV_PER_V = 1_000_000.0


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def list_power_supplies(root: str) -> list[str]:
    """Names under ``power_supply/``, sorted for deterministic pick order.

    Empty (never raises) when the directory doesn't exist — kernels without ACPI
    battery support, containers, etc.
    """
    try:
        return sorted(os.listdir(os.path.join(root, POWER_SUPPLY_DIR)))
    except OSError:
        return []


def _supply_type(root: str, name: str) -> str | None:
    return _read_text(os.path.join(root, POWER_SUPPLY_DIR, name, "type"))


def find_battery(root: str, names: list[str]) -> str | None:
    """First ``power_supply`` entry whose ``type`` is ``Battery``, or ``None``."""
    for name in names:
        if _supply_type(root, name) == "Battery":
            return name
    return None


def any_ac_online(root: str, names: list[str], battery_name: str | None) -> bool | None:
    """True if any non-battery supply reports ``online=1``.

    ``None`` when no non-battery supply exposed a readable ``online`` file at all
    (charger-presence is simply unknown on this box), as opposed to ``False`` which
    means every readable one said "not online".
    """
    saw_any = False
    for name in names:
        if name == battery_name:
            continue
        online = _read_int(os.path.join(root, POWER_SUPPLY_DIR, name, "online"))
        if online is None:
            continue
        saw_any = True
        if online == 1:
            return True
    return False if saw_any else None


def is_discharging_status(status: str | None) -> bool | None:
    """``True``/``False`` from the raw ``status`` string; ``None`` if unreadable."""
    if status is None:
        return None
    return status.strip().lower() == "discharging"


def _raw_power_w(base: str) -> float | None:
    """Magnitude of instantaneous battery power flow, watts (direction-agnostic).

    ``power_now`` (µW) is preferred; ``current_now`` (µA) × ``voltage_now`` (µV) is
    the fallback for fuel gauges that only expose the current/voltage pair. Both are
    non-negative kernel readings — the ``status`` string carries the direction, not
    the sign of these values (ACPI battery convention).
    """
    power_now = _read_int(os.path.join(base, "power_now"))
    if power_now is not None:
        return power_now / _UW_PER_W
    current_now = _read_int(os.path.join(base, "current_now"))
    voltage_now = _read_int(os.path.join(base, "voltage_now"))
    if current_now is not None and voltage_now is not None:
        return (current_now / _UA_PER_A) * (voltage_now / _UV_PER_V)
    return None


def _energy_now_wh(base: str, voltage_uv: int | None) -> float | None:
    """Remaining energy in Wh, from ``energy_now`` (µWh) or ``charge_now``×V fallback."""
    energy_now = _read_int(os.path.join(base, "energy_now"))
    if energy_now is not None:
        return energy_now / _UWH_PER_WH
    charge_now = _read_int(os.path.join(base, "charge_now"))  # µAh
    if charge_now is not None and voltage_uv is not None:
        return (charge_now / _UA_PER_A) * (voltage_uv / _UV_PER_V)
    return None


def time_remaining_s(energy_now_wh: float | None, discharge_w: float | None) -> int | None:
    """Runway at the current discharge rate, seconds. ``None`` unless both are known
    and positive (a 0W "discharge" would divide by zero / means nothing is draining)."""
    if not energy_now_wh or not discharge_w:
        return None
    return int(energy_now_wh / discharge_w * 3600.0)


def alert_level(
    capacity_pct: int | None, discharging: bool, warn_pct: float, crit_pct: float
) -> str:
    """Pure verdict: ``"ok" | "warn" | "crit"`` (unit-testable standalone, like
    ``disk.is_low``).

    Below ``crit_pct`` is always "crit", charging or not — a battery that low is
    one bad moment (unplug, brownout, a stalled charger) from shutting the box
    down. Otherwise, actively **discharging is "warn" at any percentage**: on an
    unattended run, the moment a charger-covered box starts draining its battery
    at all is itself the anomaly worth flagging, not just a low reading. A battery
    sitting below ``warn_pct`` while *not* discharging (still charging back up, or
    stalled) is also flagged, since it hasn't recovered yet.
    """
    if capacity_pct is not None and capacity_pct < crit_pct:
        return "crit"
    if discharging:
        return "warn"
    if capacity_pct is not None and capacity_pct < warn_pct:
        return "warn"
    return "ok"


class BatteryCollector:
    """Collector for the battery/AC-power widget. Read-only, non-raising."""

    name = "battery"

    def available(self, ctx: CollectContext) -> bool:
        names = list_power_supplies(ctx.root)
        return find_battery(ctx.root, names) is not None

    def collect(self, ctx: CollectContext) -> BatteryStat:
        root = ctx.root
        names = list_power_supplies(root)
        bat_name = find_battery(root, names)
        if bat_name is None:
            return BatteryStat(present=False)

        base = os.path.join(root, POWER_SUPPLY_DIR, bat_name)
        status = _read_text(os.path.join(base, "status"))
        capacity = _read_int(os.path.join(base, "capacity"))
        voltage_uv = _read_int(os.path.join(base, "voltage_now"))
        ac_online = any_ac_online(root, names, bat_name)

        by_status = is_discharging_status(status)
        if by_status is not None:
            discharging = by_status
        elif ac_online is False:
            # No status text to go on, but we know for certain no charger is
            # attached: the only honest assumption is that the battery is in use.
            discharging = True
        else:
            # Status unknown and AC presence unknown/true: don't cry wolf.
            discharging = False

        raw_w = _raw_power_w(base)
        discharge_w = None if raw_w is None else (raw_w if discharging else 0.0)

        remaining_s = None
        if discharging and discharge_w:
            remaining_s = time_remaining_s(_energy_now_wh(base, voltage_uv), discharge_w)

        alert = alert_level(capacity, discharging, ctx.cfg.battery_warn_pct, ctx.cfg.battery_crit_pct)

        return BatteryStat(
            present=True,
            ac_online=ac_online,
            status=status,
            capacity_pct=capacity,
            discharging=discharging,
            discharge_w=discharge_w,
            time_remaining_s=remaining_s,
            alert=alert,
        )
