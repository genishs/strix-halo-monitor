"""Application assembly (DESIGN §4.1 app.py).

Dependency-injection wiring: config -> backend -> collectors + job provider ->
loop -> renderer. This is the only module that knows how all the parts fit together;
everything else stays decoupled and unit-testable.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Callable

from .collectors.backends import select_backend
from .collectors.battery import BatteryCollector
from .collectors.clocks import ClockCollector
from .collectors.disk import DiskCollector
from .collectors.memory import MemoryCollector
from .collectors.network import NetworkCollector
from .collectors.power import PowerCollector
from .collectors.temperature import TemperatureCollector
from .config import Config, config_from_env
from .jobs import detect
from .jobs.base import parse_job
from .loop import UpdateLoop
from .model import JobState
from .ui.render import make_renderer


def make_job_provider(cfg: Config) -> Callable[[float], "JobState | None"]:
    """Job provider for the loop: detect the unit (read-only), read its log, parse.

    Returns ``None`` when no unit is running and no log exists (renderer shows idle).
    Never raises — the loop also guards, but detection/parse are already best-effort.
    """
    def provider(now_wall: float) -> "JobState | None":
        unit = detect.find_active_unit(cfg)
        if unit is None:
            return None
        text = detect.read_log_text(unit)
        return parse_job(cfg, text, unit, now_wall)

    return provider


def build_loop(cfg: Config, *, out=None) -> UpdateLoop:
    """Assemble a fully-wired :class:`UpdateLoop` for the given config."""
    backend = select_backend(SimpleNamespace(root=cfg.sysfs_root))
    return UpdateLoop(
        cfg,
        backend=backend,
        memory=MemoryCollector(),
        power=PowerCollector(),
        clocks=ClockCollector(),
        disk=DiskCollector(),
        network=NetworkCollector(),
        battery=BatteryCollector(),
        temperature=TemperatureCollector(),
        job_provider=make_job_provider(cfg),
        renderer=make_renderer(cfg, out=out),
    )


def run(cfg: Config) -> int:
    """Run the live dashboard until Ctrl-C. Blocking."""
    loop = build_loop(cfg)
    loop.run(clock_mono=time.monotonic, clock_wall=time.time, sleep=time.sleep)
    return 0


def run_from_args(argv: list[str]) -> int:
    """Parse the bash-compatible flags and run. ``--english``/``-e`` forces English."""
    lang_override = "en" if ("--english" in argv or "-e" in argv) else None
    cfg = config_from_env(lang_override=lang_override)
    return run(cfg)
