"""systemd ``--user`` unit detection (DESIGN §2.2 C, Phase 3).

**READ-ONLY, hard-gated.** This module is the only place in the codebase that
shells out to ``systemctl``, and every call goes through :func:`_systemctl`,
which refuses (``ValueError``) any verb outside a small whitelist
(``list-units`` / ``is-active`` / ``show``). State-changing verbs
(``start``/``stop``/``restart``/``kill``/``enable``/``disable``/...) are never
reachable through this module by construction — this box runs live training
jobs and must never be touched by the monitor.

Mirrors monitor.sh lines 85-88 (unit selection), 123 (start timestamp), 138/174
(``Result`` on a finished unit). ``run`` (a ``systemctl`` argv runner) and
``listdir`` are injectable so :func:`find_active_unit` is fully testable
without a real systemd/filesystem (DESIGN's "testable without HW" philosophy,
mirrored from ``collectors``' injectable ``sysfs_root``).
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from typing import Callable

from ..config import Config
from .base import UnitRef

#: The ONLY systemctl verbs this module may invoke. Hard read-only gate (C2) —
#: anything else raises before a subprocess is ever spawned.
_READONLY_VERBS = frozenset({"list-units", "is-active", "show"})

RunFn = Callable[[list[str]], str]
ListDirFn = Callable[[str], list[str]]


def _default_run(argv: list[str]) -> str:
    """Real ``subprocess`` runner: argv -> stdout text. Never raises.

    Non-zero exit codes are not an error here — ``systemctl is-active`` on a
    dead unit legitimately exits non-zero while still printing e.g. ``failed``
    to stdout, and we want that text.
    """
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout or ""


def _systemctl(*args: str, run: RunFn | None = None) -> str:
    """Run ``systemctl --user <args>`` through the read-only verb whitelist.

    Raises ``ValueError`` (not caught) if ``args[0]`` is not in
    ``_READONLY_VERBS`` — a defensive hard-gate that fires even if a caller
    inside this module is ever changed carelessly. Runtime failures
    (systemd unavailable, timeout, unit not found, ...) are swallowed and
    yield ``""`` since this data feeds a best-effort dashboard.
    """
    if not args or args[0] not in _READONLY_VERBS:
        verb = args[0] if args else None
        raise ValueError(
            f"detect._systemctl: read-only guard rejected verb {verb!r} "
            f"(allowed: {sorted(_READONLY_VERBS)})"
        )
    runner = run if run is not None else _default_run
    try:
        return runner(["systemctl", "--user", *args])
    except Exception:
        return ""


def _is_active(unit: str, *, run: RunFn | None) -> str | None:
    out = _systemctl("is-active", f"{unit}.service", run=run).strip()
    return out or None


def _show_value(unit: str, prop: str, *, run: RunFn | None) -> str | None:
    out = _systemctl("show", f"{unit}.service", "-p", prop, "--value", run=run)
    out = out.strip()
    return out or None


def _find_running_unit(cfg: Config, *, run: RunFn | None) -> str | None:
    """First unit whose ``list-units`` line contains ``running`` (monitor.sh:85).

    ``awk '/running/{print $1}' | head -1`` — first matching line, first
    whitespace field, ``.service`` suffix stripped.
    """
    out = _systemctl("list-units", cfg.unit_glob, "--no-legend", run=run)
    for line in out.splitlines():
        if "running" in line:
            fields = line.split()
            if not fields:
                continue
            name = fields[0]
            return name[: -len(".service")] if name.endswith(".service") else name
    return None


def _glob_names(directory: str, pattern: str, *, listdir: ListDirFn) -> list[str]:
    try:
        names = listdir(directory)
    except OSError:
        return []
    return [n for n in names if fnmatch.fnmatch(n, pattern)]


def _newest(directory: str, names: list[str]) -> str | None:
    """Full path of the newest-mtime file among ``names`` (monitor.sh ``ls -t | head -1``)."""
    best_path: str | None = None
    best_mtime = -1.0
    for name in names:
        path = os.path.join(directory, name)
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime = mtime
            best_path = path
    return best_path


def _find_latest_log_unit(cfg: Config, *, listdir: ListDirFn) -> str | None:
    """Fallback when no unit is running: basename of the newest ``<unit_glob>.log``."""
    pattern = f"{cfg.unit_glob}.log"
    names = _glob_names(cfg.log_dir, pattern, listdir=listdir)
    path = _newest(cfg.log_dir, names)
    if path is None:
        return None
    base = os.path.basename(path)
    return base[: -len(".log")] if base.endswith(".log") else base


def _latest_log_for_unit(cfg: Config, unit: str, *, listdir: ListDirFn) -> str | None:
    """Newest ``<unit>*.log`` path for an already-known unit (monitor.sh:87)."""
    pattern = f"{unit}*.log"
    names = _glob_names(cfg.log_dir, pattern, listdir=listdir)
    return _newest(cfg.log_dir, names)


def _boot_epoch(proc_stat_path: str) -> int | None:
    """Boot time (epoch seconds) from ``/proc/stat``'s ``btime`` line."""
    try:
        with open(proc_stat_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _start_epoch(
    unit: str,
    *,
    run: RunFn | None,
    log_path: str | None,
    proc_stat_path: str,
) -> int | None:
    """Unit start time as epoch seconds. Best-effort, never raises.

    Primary: ``ActiveEnterTimestampMonotonic`` (microseconds since boot, a
    monotonic clock — immune to wall-clock/NTP jumps) + ``btime`` from
    ``/proc/stat``. Deliberately NOT ``date -d "$(show ActiveEnterTimestamp)"``
    (monitor.sh:123): that string carries a timezone abbreviation (e.g. ``KST``)
    which is not reliably parseable from Python's stdlib, and shelling out to
    ``date`` as a second subprocess for it would add locale/tz landmines this
    box doesn't need. ``0`` means "never activated" (systemd convention) and is
    treated as absent.

    Fallback: the resolved log file's mtime. Final fallback: ``None`` (renderer
    shows "?" for elapsed time — non-fatal per spec).
    """
    mono_raw = _show_value(unit, "ActiveEnterTimestampMonotonic", run=run)
    boot_epoch = _boot_epoch(proc_stat_path)
    if mono_raw not in (None, "0") and boot_epoch is not None:
        try:
            mono_usec = int(mono_raw)
        except ValueError:
            mono_usec = None
        if mono_usec is not None and mono_usec > 0:
            return boot_epoch + mono_usec // 1_000_000

    if log_path:
        try:
            return int(os.stat(log_path).st_mtime)
        except OSError:
            pass
    return None


def find_active_unit(
    cfg: Config,
    *,
    run: RunFn | None = None,
    listdir: ListDirFn | None = None,
    proc_stat_path: str = "/proc/stat",
) -> UnitRef | None:
    """Detect the unit to monitor: running unit first, else newest log (monitor.sh:85-88).

    ``run`` (``argv -> stdout``) and ``listdir`` (``dir -> names``) are
    injectable for deterministic, systemd/filesystem-free tests; both default
    to the real thing. Returns ``None`` only when neither a running unit nor
    any matching log file exists.
    """
    listdir_fn: ListDirFn = listdir if listdir is not None else os.listdir

    unit = _find_running_unit(cfg, run=run)
    if unit is None:
        unit = _find_latest_log_unit(cfg, listdir=listdir_fn)
    if unit is None:
        return None

    log_path = _latest_log_for_unit(cfg, unit, listdir=listdir_fn)
    active = _is_active(unit, run=run)
    result = _show_value(unit, "Result", run=run)
    start_epoch = _start_epoch(
        unit, run=run, log_path=log_path, proc_stat_path=proc_stat_path
    )

    return UnitRef(
        name=unit,
        log_path=log_path,
        active=active,
        result=result,
        start_epoch=start_epoch,
    )


def read_log_text(unit_ref: UnitRef | None, *, max_bytes: int | None = None) -> str:
    """Read ``unit_ref.log_path`` as text. ``""`` if unresolved/unreadable.

    Reads the WHOLE file by default (parity with monitor.sh, which greps the
    whole log): the ``command`` line lives at the top, progress markers at the
    bottom, so a naive tail would miss the command line. ``max_bytes`` is left
    as an escape hatch for very large logs; unused until a memory budget calls
    for it (RSS gate — left to the lead to decide when/if to wire up).
    """
    if unit_ref is None or not unit_ref.log_path:
        return ""
    try:
        with open(unit_ref.log_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read() if max_bytes is None else fh.read(max_bytes)
    except OSError:
        return ""
