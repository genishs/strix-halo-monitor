"""Entry point: ``python -m halo_monitor`` / console script ``halo-monitor``.

Runs the live dashboard (collectors + loop + renderer, Phase 3). Flags mirror the
bash tool: ``--english``/``-e`` for the English UI, plus ``--version``. The bash
tool in ``legacy/monitor.sh`` remains the default until Phase 4 cutover; running
this in parallel is read-only and safe.
"""

from __future__ import annotations

import sys

from . import __version__
from .app import run_from_args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv or "-V" in argv:
        print(f"halo-monitor {__version__}")
        return 0
    try:
        return run_from_args(argv)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
