"""amdgpu ``GpuBackend`` implementation (DESIGN §2.2 B).

Encapsulates every amdgpu-specific sysfs path so the rest of the codebase stays
vendor-agnostic (C5). **Read-only, non-raising**: a missing/unreadable file
yields ``None`` for that field instead of raising (the bash tool's "graceful
blank" philosophy). No hardcoded card/hwmon numbers — both are discovered by
glob + content match under the injected root, so this works unmodified whether
the box's amdgpu card is ``card0`` or ``card1`` and its hwmon is ``hwmon0`` or
``hwmon12``.

sclk source: ``pp_dpm_sclk`` (sysfs) is preferred and is the *only* path
implemented here — it was confirmed present and readable on the target Strix
Halo box, so the ``rocm-smi`` fallback described in DESIGN §1.3/§4.2 is left
for whoever needs it on a box without ``pp_dpm_sclk`` (rocm-smi is itself a
heavy Python program and must not be invoked from the hot path per C1/the
project's safety rules).
"""

from __future__ import annotations

import glob
import os
import re

from ...model import ClockStats, MemoryStats, RawPower

_AMD_VENDOR_ID = "0x1002"
_RE_SCLK_CUR = re.compile(r"^\d+:\s*(\d+)Mhz\s*\*", re.MULTILINE)


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


def _card_device_dirs(root: str) -> list[str]:
    """``device`` dirs of every ``/sys/class/drm/card*`` under ``root``, sorted."""
    pattern = os.path.join(root, "sys/class/drm/card*/device")
    return sorted(d for d in glob.glob(pattern) if os.path.isdir(d))


def _amdgpu_card_dir(root: str) -> str | None:
    """First card ``device`` dir that looks like an amdgpu GPU.

    Presence of ``mem_info_gtt_total`` is the primary signal (only amdgpu
    exposes unified-memory GTT accounting). The ``vendor`` file (0x1002 = AMD)
    is a best-effort extra check only — fixture trees may not carry one, and
    that must not disqualify an otherwise-valid card.
    """
    for d in _card_device_dirs(root):
        if not os.path.isfile(os.path.join(d, "mem_info_gtt_total")):
            continue
        vendor = _read_text(os.path.join(d, "vendor"))
        if vendor is not None and vendor != _AMD_VENDOR_ID:
            continue
        return d
    return None


def _hwmon_dir_named(root: str, name: str) -> str | None:
    """``hwmon*`` dir whose ``name`` file equals ``name`` — matched by content,
    never by number (hwmon numbering is not stable across boots/boxes)."""
    pattern = os.path.join(root, "sys/class/hwmon/hwmon*")
    for d in sorted(glob.glob(pattern)):
        if _read_text(os.path.join(d, "name")) == name:
            return d
    return None


def _parse_pp_dpm_sclk(text: str) -> int | None:
    """Parse the ``*``-marked current level's MHz from a ``pp_dpm_sclk`` dump.

    Format (no leading whitespace, one level per line)::

        0: 600Mhz
        1: 2846Mhz *
        2: 2900Mhz
    """
    m = _RE_SCLK_CUR.search(text)
    return int(m.group(1)) if m else None


class AmdgpuBackend:
    """``GpuBackend`` for AMD amdgpu (Strix Halo gfx1151 and other amdgpu cards)."""

    name = "amdgpu"

    def __init__(self, root: str) -> None:
        self.root = root

    def detect(self) -> bool:
        return _amdgpu_card_dir(self.root) is not None

    def mem_info(self) -> MemoryStats:
        card = _amdgpu_card_dir(self.root)
        if card is None:
            return MemoryStats()
        return MemoryStats(
            gtt_used_bytes=_read_int(os.path.join(card, "mem_info_gtt_used")),
            gtt_total_bytes=_read_int(os.path.join(card, "mem_info_gtt_total")),
            vram_used_bytes=_read_int(os.path.join(card, "mem_info_vram_used")),
        )

    def power(self) -> RawPower:
        hwmon = _hwmon_dir_named(self.root, "amdgpu")
        if hwmon is None:
            return RawPower()
        raw_uw = _read_int(os.path.join(hwmon, "power1_input"))
        watts = raw_uw / 1e6 if raw_uw is not None else None
        return RawPower(amdgpu_w=watts)

    def clocks(self) -> ClockStats:
        card = _amdgpu_card_dir(self.root)
        if card is None:
            return ClockStats()
        text = _read_text(os.path.join(card, "pp_dpm_sclk"))
        if text is None:
            return ClockStats()
        return ClockStats(sclk_mhz=_parse_pp_dpm_sclk(text))
