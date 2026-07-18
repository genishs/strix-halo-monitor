"""Entry point: ``python -m halo_monitor`` / console script ``halo-monitor``.

Phase 0 skeleton. The live dashboard (collectors + loop + renderer) lands in
Phase 2-3; until then this reports migration status so the package is runnable and
the console-script wiring is verifiable. The bash tool in ``legacy/monitor.sh``
remains the working dashboard until Phase 3 parity is confirmed.
"""

from __future__ import annotations

import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv or "-V" in argv:
        print(f"halo-monitor {__version__}")
        return 0
    print(f"halo-monitor {__version__} — Python migration in progress (Phase 0-1).")
    print("The live dashboard is not wired yet; use legacy/monitor.sh meanwhile.")
    print("See DESIGN.md and docs/DEVLOG.md for the migration plan and status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
