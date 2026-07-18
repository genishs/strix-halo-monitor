"""Job parser protocol, unit reference, and parser registry (DESIGN §2.2 C)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import Config
from ..model import JobState


@dataclass
class UnitRef:
    """A reference to the systemd --user unit whose job we are monitoring.

    Populated by ``jobs/detect.py`` (Phase 2, read-only systemd queries). In Phase 1
    parser tests this is constructed directly so parsing stays HW/systemd-independent.
    Every field is optional so parsers degrade gracefully when systemd data is absent.
    """

    name: str                          # unit name WITHOUT the ".service" suffix
    log_path: str | None = None        # resolved newest log file for this unit
    active: str | None = None          # systemd `is-active`: active/inactive/failed/...
    result: str | None = None          # systemd Result: success/exit-code/...
    start_epoch: int | None = None     # ActiveEnterTimestamp as epoch seconds


@runtime_checkable
class JobParser(Protocol):
    """A parser for one kind of job. First matching parser in the registry wins."""

    name: str

    def matches(self, unit: UnitRef) -> bool:
        """True if this parser handles ``unit`` (usually by unit-name pattern)."""
        ...

    def parse(self, log_text: str, unit: UnitRef, now: float) -> JobState:
        """Turn log text + unit into a JobState. Must not raise on malformed input."""
        ...


# --- registry -------------------------------------------------------------- #
# Parser *factories* keyed by insertion order; first whose instance .matches()
# returns True is used. Score is registered before Train because Train is the
# catch-all fallback (mirrors monitor.sh: `*score*` => scoring, else training).
_REGISTRY: list[type] = []


def register(parser_cls: type) -> type:
    """Class decorator: add a JobParser implementation to the registry."""
    _REGISTRY.append(parser_cls)
    return parser_cls


def build_parsers(cfg: Config) -> list[JobParser]:
    """Instantiate all registered parsers with ``cfg`` (import side-effect safe)."""
    # Import implementations so their @register decorators run. Local import avoids
    # a circular import at module load time.
    from . import score as _score  # noqa: F401
    from . import train as _train  # noqa: F401

    return [cls(cfg) for cls in _REGISTRY]


def select_parser(parsers: list[JobParser], unit: UnitRef) -> JobParser | None:
    """Return the first parser that matches ``unit``, or None."""
    for p in parsers:
        if p.matches(unit):
            return p
    return None


def parse_job(cfg: Config, log_text: str, unit: UnitRef, now: float) -> JobState | None:
    """Facade: build parsers, pick the one matching ``unit``, and parse. None if no match."""
    parser = select_parser(build_parsers(cfg), unit)
    if parser is None:
        return None
    return parser.parse(log_text, unit, now)
