"""Temperature collector: GPU/CPU/NVMe sensors via hwmon (Phase 7, DESIGN §3).

C2 INVARIANT (training must not be disturbed): this collector reads a handful of
tiny text files under ``sys/class/hwmon/hwmon*/`` — the kernel's own cached sensor
readings — and nothing else. No ``sensors``, no ``rocm-smi``, no subprocess. A tick
costs a few open/read/close calls on files a few bytes long, unmeasurably cheap next
to a running GPU job. **Do not call ``rocm-smi`` here** — its GPU-edge reading is the
exact same ``hwmon`` value this module already reads directly, at zero process-spawn
cost (confirmed on this box: ``rocm-smi --showtemp`` prints the same "Temperature
(Sensor edge)" value as ``amdgpu`` hwmon's ``edge`` input).

Sensor selection is by hwmon ``name`` file content (``amdgpu`` / ``k10temp`` /
``nvme``), never by hwmon number — hwmon numbering is not stable across boots or
boxes (the same fix ``collectors/mounts.py`` made for disks and
``collectors/battery.py`` made for power_supply devices). Within a chip, the exact
sensor is picked by its ``temp*_label`` text (``edge`` for amdgpu, ``Tctl`` for
k10temp, ``Composite`` for nvme) rather than always ``temp1``, since multi-sensor
chips expose several temp inputs and the wrong one would silently mislead (e.g.
amdgpu's ``junction``/``mem`` run hotter than ``edge``). A chip with no
``temp*_label`` files at all falls back to its first ``tempN_input`` (nothing to
disambiguate), per the "temp*_label 있으면 라벨, 없으면 tempN" rule.

Read-only, non-raising, best-effort like every other collector: any sensor this box
doesn't have (no discrete/APU GPU, no k10temp, no NVMe) is simply absent from the
returned list — never a crash, never a placeholder row.

⚠️ **Threshold sanity**: a chip's own ``temp*_max``/``temp*_crit`` sysfs limit is
preferred over the configured default *when it looks like a real temperature*
(0-150°C). Some NVMe firmwares report obvious garbage there — observed on this box:
``temp2_max`` = 65261°C, almost certainly an unsigned overflow sentinel. Using such a
value unfiltered as the alert threshold means the alert can mathematically never
fire; see :func:`_sane_threshold`.
"""

from __future__ import annotations

import glob
import os
import re

from ..config import Config
from ..model import TempStat
from .base import CollectContext

HWMON_GLOB = "sys/class/hwmon/hwmon*"

#: Sane bound for a hwmon temperature *reading or threshold*, °C. Real silicon and
#: real drives never legitimately report outside this band; a value outside it is
#: sensor noise, an unpopulated sensor, or (as observed) an overflowed sentinel.
_SANE_MIN_C = 0.0
_SANE_MAX_C = 150.0

_RE_TEMP_LABEL = re.compile(r"^temp(\d+)_label$")
_RE_TEMP_INPUT = re.compile(r"^temp(\d+)_input$")


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_millideg_c(path: str) -> float | None:
    """A hwmon ``tempN_*`` file's millidegree-Celsius value, in whole °C."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text) / 1000.0
    except ValueError:
        return None


def _sane_threshold(value: float | None, default: float) -> float:
    """``value`` if it looks like a real temperature (0-150°C), else ``default``.

    Guards against garbage device-reported limits (observed: an NVMe ``temp2_max``
    of 65261°C) so a bogus sysfs value can never silently disable the alert.
    """
    if value is not None and _SANE_MIN_C <= value <= _SANE_MAX_C:
        return value
    return default


def hwmon_dirs_named(root: str, name: str) -> list[str]:
    """Every ``hwmon*`` dir (sorted) whose ``name`` file equals ``name``.

    Plural on purpose: this box has two ``nvme`` hwmon chips (one per drive), and
    matching by content (not by hwmon number, which is reassigned across boots) is
    the same principle ``collectors/backends/amdgpu.py`` already uses for the single
    ``amdgpu`` hwmon.
    """
    out = []
    for d in sorted(glob.glob(os.path.join(root, HWMON_GLOB))):
        if _read_text(os.path.join(d, "name")) == name:
            out.append(d)
    return out


def _label_map(hwmon_dir: str) -> dict[str, int]:
    """``{lowercased temp*_label text: N}`` for every labelled temp input."""
    out: dict[str, int] = {}
    for path in glob.glob(os.path.join(hwmon_dir, "temp*_label")):
        m = _RE_TEMP_LABEL.match(os.path.basename(path))
        if not m:
            continue
        text = _read_text(path)
        if text:
            out[text.strip().lower()] = int(m.group(1))
    return out


def _first_input_n(hwmon_dir: str) -> int | None:
    """Lowest-numbered ``tempN`` with an ``_input`` file, or ``None``."""
    ns: list[int] = []
    for path in glob.glob(os.path.join(hwmon_dir, "temp*_input")):
        m = _RE_TEMP_INPUT.match(os.path.basename(path))
        if m:
            ns.append(int(m.group(1)))
    return min(ns) if ns else None


def pick_temp_n(hwmon_dir: str, wanted_label: str) -> int | None:
    """The ``tempN`` matching ``wanted_label`` (case-insensitive), if this chip
    exposes ``temp*_label`` files at all; otherwise the first available ``tempN``
    (nothing to disambiguate). ``None`` when the chip has labels but not the
    wanted one — we do not guess a different physical sensor than what was asked
    for (e.g. never substitute amdgpu ``junction`` for a requested ``edge``).
    """
    labels = _label_map(hwmon_dir)
    if labels:
        return labels.get(wanted_label.lower())
    return _first_input_n(hwmon_dir)


def _stat_for(
    hwmon_dir: str, wanted_label: str, key: str, label: str, warn_default: float, crit_default: float
) -> TempStat | None:
    n = pick_temp_n(hwmon_dir, wanted_label)
    if n is None:
        return None
    temp_c = _read_millideg_c(os.path.join(hwmon_dir, f"temp{n}_input"))
    if temp_c is None:
        return None
    warn_c = _sane_threshold(
        _read_millideg_c(os.path.join(hwmon_dir, f"temp{n}_max")), warn_default
    )
    crit_c = _sane_threshold(
        _read_millideg_c(os.path.join(hwmon_dir, f"temp{n}_crit")), crit_default
    )
    return TempStat(
        key=key, label=label, temp_c=temp_c, warn_c=warn_c, crit_c=crit_c,
        alert=alert_level(temp_c, warn_c, crit_c),
    )


def alert_level(temp_c: float | None, warn_c: float, crit_c: float) -> str:
    """Pure verdict: ``"ok" | "warn" | "crit"`` (unit-testable standalone, like
    ``disk.is_low`` / ``battery.alert_level``)."""
    if temp_c is None:
        return "ok"
    if temp_c >= crit_c:
        return "crit"
    if temp_c >= warn_c:
        return "warn"
    return "ok"


def probe(root: str, cfg: Config) -> list[TempStat]:
    """Every readable GPU/CPU/NVMe temperature sensor on this box, in display order.

    GPU and CPU are each at most one reading (a box has one APU/GPU and one CPU
    package); NVMe is 0-N readings, one per drive. A single NVMe drive is labelled
    plain ``"NVMe"``; two or more are numbered ``"NVMe1"``, ``"NVMe2"``, ... in
    hwmon-glob (sorted-path) order — that order is stable for the life of the
    process but not guaranteed to match physical slot order, since sysfs doesn't
    expose one.
    """
    out: list[TempStat] = []

    gpu_dirs = hwmon_dirs_named(root, "amdgpu")
    if gpu_dirs:
        stat = _stat_for(gpu_dirs[0], "edge", "gpu", "GPU", cfg.temp_warn_c, cfg.temp_crit_c)
        if stat is not None:
            out.append(stat)

    cpu_dirs = hwmon_dirs_named(root, "k10temp")
    if cpu_dirs:
        stat = _stat_for(cpu_dirs[0], "Tctl", "cpu", "CPU", cfg.temp_warn_c, cfg.temp_crit_c)
        if stat is not None:
            out.append(stat)

    nvme_dirs = hwmon_dirs_named(root, "nvme")
    multiple = len(nvme_dirs) > 1
    for i, d in enumerate(nvme_dirs):
        label = f"NVMe{i + 1}" if multiple else "NVMe"
        stat = _stat_for(
            d, "Composite", f"nvme{i}", label, cfg.nvme_temp_warn_c, cfg.nvme_temp_crit_c
        )
        if stat is not None:
            out.append(stat)

    return out


class TemperatureCollector:
    """Collector for the GPU/CPU/NVMe temperature widget. Read-only, non-raising."""

    name = "temperature"

    def available(self, ctx: CollectContext) -> bool:
        root = ctx.root
        return bool(
            hwmon_dirs_named(root, "amdgpu")
            or hwmon_dirs_named(root, "k10temp")
            or hwmon_dirs_named(root, "nvme")
        )

    def collect(self, ctx: CollectContext) -> list[TempStat]:
        return probe(ctx.root, ctx.cfg)
