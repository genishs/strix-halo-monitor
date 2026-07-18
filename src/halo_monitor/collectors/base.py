"""Collector interface + registry (DESIGN §2.2 B).

A ``Collector`` probes one metric family and returns a *partial* domain dataclass.
Collectors are **read-only** and **stateless** — no deltas, no prev-values (the loop
owns time and mutable state). They must degrade gracefully: on missing files/permission
they return partial/empty results rather than raising.

Testability: every collector reads under an injected root (``cfg.sysfs_root``, default
``/``). Tests point that at ``tests/fixtures/sysfs/`` for deterministic results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import Config
from .backends.base import GpuBackend


@dataclass
class CollectContext:
    """Immutable inputs handed to every collector on each tick.

    Deliberately carries NO mutable/delta state — rate and energy-delta math live in
    the loop. ``root`` is ``cfg.sysfs_root`` (injectable for tests).
    """

    cfg: Config
    backend: GpuBackend | None
    root: str  # filesystem root to read sysfs/procfs under (default "/")


@runtime_checkable
class Collector(Protocol):
    """Probe for one metric family. Read-only, non-raising, best-effort."""

    name: str

    def available(self, ctx: CollectContext) -> bool:
        """Cheap check that this collector can read what it needs in this env."""
        ...

    def collect(self, ctx: CollectContext):
        """Return a partial domain dataclass (e.g. MemoryStats). Never raise —
        return partial/empty on missing data (fields left None)."""
        ...
