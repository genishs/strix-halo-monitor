"""Widgets: value formatting, progress bar, model line, ETA (DESIGN §2.2 A).

Pure string helpers mirroring monitor.sh's awk/printf number formatting so the
rendered values are byte-identical.
"""

from __future__ import annotations

import time
import unicodedata
from typing import Callable

from ..model import DiskStat, EvalPhase, EvalProgress, JobState, JobType, ModelInfo, NetStat, Phase
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


# --- disk widget (Phase 5) ------------------------------------------------- #
def _disp_width(s: str) -> int:
    """Terminal column width of ``s`` (CJK wide/fullwidth glyphs count as 2)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad_label(label: str, width: int) -> str:
    """Right-pad ``label`` with spaces to ``width`` *display* columns."""
    gap = width - _disp_width(label)
    return label + " " * gap if gap > 0 else label


def disk_flag(low: bool, lang: str, theme: Theme) -> str:
    """``⚠️위험``/``⚠️LOW`` when a mount is below threshold, else ``✓``.

    Same glyphs as :func:`ram_flag` so the two low-space warnings read alike.
    """
    return theme.ram_low_prefix + i18n.t(lang, "ram_low") if low else theme.ram_ok


def disk_lines(disks: list[DiskStat], lang: str, theme: Theme) -> list[str]:
    """One rendered line per configured mount (label columns aligned).

    Present mount::

        ★<label>:   12.3 / 456GB  [███░░░...] 27%   여유 333.0GB ✓

    Absent mount (unmounted / removable drive not connected)::

        ★<label>:   사용불가

    The label, used and total columns are each widened to the widest value in the
    set, so the bars and percentages line up in one column. The numeric widths must
    be dynamic because auto-discovery can put a 4-digit multi-TB drive (``1422.1 /
    1863GB``) next to a 3-digit one (``133.7 / 210GB``); a fixed width made the
    larger row jut out and dragged every column after it out of alignment.
    """
    if not disks:
        return []
    present = [d for d in disks if d.present]
    label_w = max(_disp_width(d.label or d.path) for d in disks)
    used_w = max((len(gb1(d.used_bytes)) for d in present), default=0)
    total_w = max((len(gb0(d.total_bytes)) for d in present), default=0)
    free_w = max((len(gb1(d.free_bytes)) for d in present), default=0)
    free_word = i18n.t(lang, "free")
    out: list[str] = []
    for d in disks:
        head = f"   {theme.star}{_pad_label(d.label or d.path, label_w)}:"
        if not d.present:
            out.append(f"{head}   {i18n.t(lang, 'disk_na')}")
            continue
        pct = d.used_pct if d.used_pct is not None else 0
        out.append(
            f"{head}   {gb1(d.used_bytes):>{used_w}} / {gb0(d.total_bytes):>{total_w}}GB  "
            f"[{bar(pct, theme)}] {pct}%   "
            f"{free_word} {gb1(d.free_bytes):>{free_w}}GB {disk_flag(d.low, lang, theme)}"
        )
    return out


# --- network widget (Phase 5) ---------------------------------------------- #
def net_rate(mb_s: float | None) -> str:
    """Throughput in MB/s to one decimal (``?`` when unknown/first tick)."""
    return "?" if mb_s is None else f"{mb_s:.1f}"


def net_lines(nets: list[NetStat], lang: str, theme: Theme) -> list[str]:
    """One rendered line per active interface (label columns aligned).

    Present interface::

        ★<label>:   ↓    12.3 MB/s   ↑     1.2 MB/s   (누적 ↓ 4.5GB ↑ 0.3GB)

    Absent interface (down / renamed / unreadable)::

        ★<label>:   사용불가
    """
    if not nets:
        return []
    label_w = max(_disp_width(n.label or n.name) for n in nets)
    total_word = i18n.t(lang, "net_total")
    dn, up = theme.net_down, theme.net_up
    out: list[str] = []
    for n in nets:
        head = f"   {theme.star}{_pad_label(n.label or n.name, label_w)}:"
        if not n.present:
            out.append(f"{head}   {i18n.t(lang, 'net_na')}")
            continue
        out.append(
            f"{head}   {dn} {net_rate(n.rx_mb_s):>7} MB/s   "
            f"{up} {net_rate(n.tx_mb_s):>7} MB/s   "
            f"({total_word} {dn} {gb1(n.rx_session_bytes)}GB {up} {gb1(n.tx_session_bytes)}GB)"
        )
    return out


# --- eval / grading widget (Phase 5) --------------------------------------- #
def tok_s(value: float | None) -> str:
    """Throughput in tok/s to one decimal (``—`` when not honestly measurable)."""
    return "—" if value is None else f"{value:.1f}"


def eval_lines(ev: EvalProgress | None, lang: str, theme: Theme) -> list[str]:
    """One rendered line for the active eval/grading run (standard ML eval terms).

    Generating::

        ★평가:   task 3/7   현재 pyexpr_eval   45.2 tok/s   ETA 0h12m30s (관측)

    Compiling / scoring::

        ★평가:   task 7/7   컴파일·채점 중   45.2 tok/s

    Finished::

        ★평가:   완료 7/7   점수 5.50/7 = 78.6%
    """
    if ev is None:
        return []
    head = f"   {theme.star}{i18n.t(lang, 'eval')}:"
    done = ev.done if ev.done is not None else 0
    total = ev.total if ev.total is not None else "?"

    if ev.phase is EvalPhase.FINISHED:
        body = f"{i18n.t(lang, 'eval_done')} {done}/{total}"
        if ev.score is not None and ev.max is not None:
            pct = f" = {ev.pct:.1f}%" if ev.pct is not None else ""
            body += f"   {i18n.t(lang, 'eval_score')} {ev.score:.2f}/{ev.max}{pct}"
        return [f"{head}   {body}"]

    parts = [f"{i18n.t(lang, 'eval_task')} {done}/{total}"]
    if ev.phase is EvalPhase.COMPILING:
        parts.append(i18n.t(lang, "eval_compiling"))
    elif ev.cur_task:
        parts.append(f"{i18n.t(lang, 'eval_cur')} {ev.cur_task}")
    parts.append(f"{tok_s(ev.tok_s)} tok/s")
    if ev.eta_s is not None:
        parts.append(f"ETA {hms(ev.eta_s)} ({i18n.t(lang, 'eval_observed')})")
    else:
        parts.append(f"ETA {i18n.t(lang, 'eval_estimating')}")
    return [f"{head}   " + "   ".join(parts)]
