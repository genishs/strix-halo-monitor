"""Theme: glyphs and the fixed box/separator geometry (DESIGN §2.2 A).

The default theme reproduces ``legacy/monitor.sh`` byte-for-byte. The separator
and box-border rune counts are **fixed literals copied from the bash source** (they
do NOT scale with the label text — e.g. the power separator is intentionally
asymmetric 31/33), so ko and en frames differ in total width exactly as bash's do.

``--ascii`` / ``--no-emoji`` / ``--no-color`` variants are a later phase (DESIGN O1);
for Phase-3 parity there is one theme and it matches the bash unicode output.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    # progress bar
    bar_fill: str = "█"
    bar_empty: str = "░"
    bar_width: int = 20
    star: str = "★"
    # box borders (top/bottom corners + horizontal)
    tl: str = "╔"
    tr: str = "╗"
    bl: str = "╚"
    br: str = "╝"
    eq: str = "═"
    dash: str = "─"
    pipe: str = "│"
    # fixed rune counts, measured from monitor.sh output
    hdr_eq_left: int = 18
    hdr_eq_right: int = 18
    ftr_eq_left: int = 31
    ftr_eq_right: int = 31
    umem_dash_left: int = 32
    umem_dash_right: int = 32
    power_dash_left: int = 31     # intentionally asymmetric (matches bash source)
    power_dash_right: int = 33
    disk_dash_left: int = 34      # Phase 5 disk section separator (matches legacy)
    disk_dash_right: int = 34
    net_dash_left: int = 33       # Phase 5 network section separator ("네트워크"=8 cols)
    net_dash_right: int = 33
    net_down: str = "↓"           # RX / download arrow
    net_up: str = "↑"             # TX / upload arrow
    eval_dash_left: int = 34      # Phase 5 eval/grading section separator
    eval_dash_right: int = 34
    battery_dash_left: int = 34   # Phase 6 battery/power section separator
    battery_dash_right: int = 34
    # status flags
    ram_ok: str = "✓"
    ram_low_prefix: str = "⚠️"
    battery_crit_prefix: str = "🚨"  # distinct from ram_low_prefix: the unattended-run
                                     # "look at the screen NOW" marker (Phase 6)


DEFAULT = Theme()
