"""Widgets: value formatting, progress bar, model line, ETA (DESIGN §2.2 A).

Pure string helpers mirroring monitor.sh's awk/printf number formatting so the
rendered values are byte-identical.
"""

from __future__ import annotations

import time
from typing import Callable

from ..model import JobState, JobType, ModelInfo, Phase
from . import i18n
from .theme import Theme

_GIB = 1073741824


def hms(seconds: int) -> str:
    """``%dh%02dm%02ds`` (monitor.sh ``hms``). Hours are not zero-padded."""
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m{seconds % 60:02d}s"


def gb1(nbytes: int | None) -> str:
    return "?" if nbytes is None else f"{nbytes / _GIB:.1f}"


def gb0(nbytes: int | None) -> str:
    return "?" if nbytes is None else f"{nbytes / _GIB:.0f}"


def gb_float(gb: float | None) -> str:
    return "?" if gb is None else f"{gb:.1f}"


def pct_int(used: int | None, total: int | None) -> int:
    if not used or not total:
        return 0
    return int(f"{used / total * 100:.0f}")  # match awk %.0f rounding


def bar(pct: int, theme: Theme) -> str:
    filled = pct // 5
    if filled > theme.bar_width:
        filled = theme.bar_width
    if filled < 0:
        filled = 0
    return theme.bar_fill * filled + theme.bar_empty * (theme.bar_width - filled)


def rate(mb_s: float | None) -> str:
    return "?" if mb_s is None else f"{mb_s:+.0f}"


def watt(w: float | None) -> str:
    return "?" if w is None else f"{w:.0f}"


def elapsed(seconds: int | None) -> str:
    return "?" if seconds is None else hms(seconds)


def _add(parts: list[str], s: str | None) -> None:
    if s:
        parts.append(s)


def model_line(lang: str, mi: ModelInfo, is_score: bool) -> str:
    """Reproduce monitor.sh's addpart chain for the "Model:" line."""
    parts: list[str] = []
    _add(parts, mi.base_label)
    if mi.nbits:
        _add(parts, f"HQQ {mi.nbits}bit")
    if is_score:
        if mi.adapter:
            _add(parts, f"{i18n.t(lang, 'adapter')} {mi.adapter}")
        if mi.heldout:
            _add(parts, f"heldout mn{mi.max_new}" if mi.max_new else "heldout")
    else:
        if mi.seq:
            _add(parts, f"seq{mi.seq}")
        if mi.lora_r:
            lora = f"LoRA r{mi.lora_r}"
            if mi.lora_mlp:
                lora += "+mlp"
            _add(parts, lora)
        if mi.epochs:
            _add(parts, f"{mi.epochs}ep")
    return " · ".join(parts) if parts else "?"


def ram_flag(gb: float | None, lang: str, theme: Theme) -> str:
    """``⚠️위험``/``⚠️LOW`` when RAM < 3GB else ``✓`` (monitor.sh ramflag)."""
    if gb is not None and gb < 3:
        return theme.ram_low_prefix + i18n.t(lang, "ram_low")
    return theme.ram_ok


def eta_display(lang: str, job: JobState) -> str:
    """The ``eta`` slot: hms + optional note, a note alone, or ``—``."""
    if job.eta_seconds is not None:
        base = hms(job.eta_seconds)
        if job.eta_note is not None:
            base += "  " + i18n.note_text(lang, job.eta_note)
        return base
    if job.eta_note is not None:
        return i18n.note_text(lang, job.eta_note)
    return "—"


def done_time(job: JobState, ts: float, localtime: Callable = time.localtime) -> str:
    """``HH:MM`` wall-clock completion time, or ``—`` when no ETA seconds."""
    if job.eta_seconds is None:
        return "—"
    return time.strftime("%H:%M", localtime(ts + job.eta_seconds))
