"""Low-level log scraping — regex fallbacks and JSON-status extraction.

These mirror the exact grep/awk patterns of monitor.sh so the regex fallback keeps
byte-for-byte parity with the bash tool (Phase 3 goal). The JSON path (ADR-0002) is
the preferred source; regex is used only when no status line is present.

All functions are pure (str -> values) and never raise.
"""

from __future__ import annotations

import re
from typing import Any

# --- training / quantization markers (monitor.sh) -------------------------- #
_RE_QUANT = re.compile(r"(\d+)/(\d+) Linear 양자화")
_RE_OPTIM_STEPS = re.compile(r"optim_steps≈(\d+)")
_RE_STEP = re.compile(r"step (\d+) \|")
_RE_SSTEP = re.compile(r"([0-9.]+)s/step")
_RE_LOSS = re.compile(r"loss\(avg8\) ([0-9.]+)")

# --- scoring markers ------------------------------------------------------- #
_RE_REPLACED = re.compile(r"HQQ 스트리밍 치환 완료")
_RE_GENERATED = re.compile(r"generated \[")
_RE_LAST_GEN = re.compile(r"generated \[.*")

# --- errors (case-insensitive, monitor.sh set) ----------------------------- #
_RE_ERRORS = re.compile(
    r"Traceback|hipError|unspecified launch|out of memory|Killed|device wedged",
    re.IGNORECASE,
)

# --- command line ---------------------------------------------------------- #
_RE_COMMAND_LINE = re.compile(r"^command.*$", re.MULTILINE)


def _last_int(pattern: re.Pattern[str], text: str, group: int = 1) -> int | None:
    """Last match's integer group, or None (mirrors bash ``... | tail -1``)."""
    m = None
    for m in pattern.finditer(text):
        pass
    if m is None:
        return None
    try:
        return int(m.group(group))
    except (ValueError, IndexError):
        return None


def _last_float(pattern: re.Pattern[str], text: str, group: int = 1) -> float | None:
    m = None
    for m in pattern.finditer(text):
        pass
    if m is None:
        return None
    try:
        return float(m.group(group))
    except (ValueError, IndexError):
        return None


# --- JSON value coercion (tolerant; a bad field never crashes a consumer) --- #
def as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def as_str(v: Any) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def count_errors(text: str) -> int:
    """Number of error-marker occurrences (monitor.sh: ``grep -icE ...``)."""
    return len(_RE_ERRORS.findall(text))


def find_command_line(text: str) -> str | None:
    """First line starting with ``command`` (monitor.sh: ``grep -m1 '^command'``)."""
    m = _RE_COMMAND_LINE.search(text)
    return m.group(0) if m else None


# --- training progress ----------------------------------------------------- #
def train_quant(text: str) -> tuple[int, int] | None:
    """Last ``N/M Linear 양자화`` as (done, total)."""
    m = None
    for m in _RE_QUANT.finditer(text):
        pass
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def _last_str(pattern: re.Pattern[str], text: str, group: int = 1) -> str | None:
    """Last match's raw group string, or None (kept verbatim for render parity)."""
    m = None
    for m in pattern.finditer(text):
        pass
    if m is None:
        return None
    try:
        return m.group(group)
    except IndexError:
        return None


def train_progress(text: str) -> dict[str, Any]:
    """Scrape training numbers: quant, total(optim_steps), step, loss, sstep.

    ``loss``/``sstep`` are floats for computation; ``loss_disp``/``sstep_disp`` keep the
    raw log strings verbatim for byte-exact render parity (the log may carry more
    precision than a rounded float would show).
    """
    q = train_quant(text)
    return {
        "quant_done": q[0] if q else None,
        "quant_total": q[1] if q else None,
        "total": _last_int(_RE_OPTIM_STEPS, text),
        "step": _last_int(_RE_STEP, text),
        "loss": _last_float(_RE_LOSS, text),
        "sstep": _last_float(_RE_SSTEP, text),
        "loss_disp": _last_str(_RE_LOSS, text),
        "sstep_disp": _last_str(_RE_SSTEP, text),
    }


# --- scoring progress ------------------------------------------------------ #
def score_progress(text: str) -> dict[str, Any]:
    """Scrape scoring numbers: replaced count, gen_done, last_gen, and reuse quant."""
    last_gen = None
    for m in _RE_LAST_GEN.finditer(text):
        last_gen = m.group(0)
    q = train_quant(text)
    return {
        "replaced": len(_RE_REPLACED.findall(text)),
        "gen_done": len(_RE_GENERATED.findall(text)),
        "last_gen": last_gen,
        "quant_done": q[0] if q else None,
        "quant_total": q[1] if q else None,
    }
