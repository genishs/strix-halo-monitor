"""Renderer: Snapshot -> one screen of text, byte-for-byte with monitor.sh.

Each line below is transcribed from the corresponding bash ``printf``/``echo``.
The renderer depends only on ``model`` + ``i18n`` + ``theme`` + ``widgets`` — never
on collectors/parsers (DESIGN §2.2 A). Returns the frame body (12 lines, no trailing
newline, no screen-clear); the output driver prepends the clear for live display.
"""

from __future__ import annotations

import time
from typing import Callable

from ..config import Config
from ..model import JobState, JobType, Phase, Snapshot
from . import i18n, widgets
from .theme import DEFAULT, Theme

_IDLE_JOB = JobState(job_type=JobType.TRAIN, phase=Phase.IDLE)


def _sclk(mhz: int | None) -> str:
    return "?" if mhz is None else str(mhz)


def render_frame(
    snapshot: Snapshot,
    cfg: Config,
    *,
    theme: Theme = DEFAULT,
    localtime: Callable = time.localtime,
) -> str:
    lang = cfg.lang
    job = snapshot.job if snapshot.job is not None else _IDLE_JOB
    mem, power, clk = snapshot.memory, snapshot.power, snapshot.clocks
    is_score = job.job_type is JobType.SCORE

    eq, dash, star, pipe = theme.eq, theme.dash, theme.star, theme.pipe

    # 1. header
    header = (
        f"{theme.tl}{eq * theme.hdr_eq_left} "
        f"{snapshot.title} ({snapshot.gfx}) "
        f"{eq * theme.hdr_eq_right}{theme.tr}"
    )

    # 2. progress
    l_progress = f"  {i18n.t(lang, 'progress')}:  {i18n.phase_text(lang, job)}"

    # 3. elapsed / ETA / errors
    l_elapsed = (
        f"  {i18n.t(lang, 'elapsed')}:  {widgets.elapsed(job.elapsed_seconds):<14}   "
        f"{i18n.t(lang, 'eta')}: {widgets.done_time(job, snapshot.ts, localtime)} "
        f"({i18n.t(lang, 'remaining')} {widgets.eta_display(lang, job)})     "
        f"{i18n.t(lang, 'errors')}: {job.error_count}"
    )

    # 4. model
    l_model = f"  {i18n.t(lang, 'model')}:  {widgets.model_line(lang, job.model_info, is_score)}"

    # 5. unified-memory separator
    sep_umem = (
        f"  {dash * theme.umem_dash_left} {i18n.t(lang, 'unified_memory')} "
        f"{dash * theme.umem_dash_right}"
    )

    # 6. GTT
    pct = widgets.pct_int(mem.gtt_used_bytes, mem.gtt_total_bytes)
    l_gtt = (
        f"  {star}{i18n.t(lang, 'gtt')}:  {widgets.gb1(mem.gtt_used_bytes):>5} / "
        f"{widgets.gb0(mem.gtt_total_bytes)}GB  [{widgets.bar(pct, theme)}] {pct}%   "
        f"{i18n.t(lang, 'rate')} {widgets.rate(mem.gtt_rate_mb_s)} MB/s"
    )

    # 7. VRAM
    l_vram = (
        f"   {i18n.t(lang, 'vram')}:   {widgets.gb1(mem.vram_used_bytes):>5}GB "
        f"{i18n.t(lang, 'nvtop_note')}     {i18n.pool_note(lang, cfg.pool_gb)}"
    )

    # 8. host RAM / swap
    l_ram = (
        f"   {i18n.t(lang, 'host_ram')}: {widgets.gb_float(mem.ram_free_gb)}GB "
        f"{widgets.ram_flag(mem.ram_free_gb, lang, theme)}     "
        f"swap: {widgets.gb_float(mem.swap_used_gb)}GB"
    )

    # 9. power separator
    sep_power = (
        f"  {dash * theme.power_dash_left} {i18n.t(lang, 'power_gpu')} "
        f"{dash * theme.power_dash_right}"
    )

    # 10. power split
    l_power = (
        f"   {star}{i18n.t(lang, 'power')}:  "
        f"CPU {widgets.watt(power.cpu_w):>3}W  {pipe}  "
        f"GPU {widgets.watt(power.gpu_w):>3}W  {pipe}  "
        f"{i18n.t(lang, 'total')} {widgets.watt(power.total_w):>3}W    "
        f"{i18n.t(lang, 'power_note')}"
    )

    # 11. sclk / unit
    l_sclk = (
        f"   sclk: {_sclk(clk.sclk_mhz)}Mhz      "
        f"{i18n.t(lang, 'unit')}: {job.unit_active or '?'}"
    )

    # 11b. disk section (Phase 5) — additive: only when the snapshot carries disk
    # data, so frames assembled without it (e.g. the byte-parity golden fixtures)
    # stay identical to the legacy 12-line layout.
    disk_block: list[str] = []
    if snapshot.disks:
        sep_disk = (
            f"  {dash * theme.disk_dash_left} {i18n.t(lang, 'disk')} "
            f"{dash * theme.disk_dash_right}"
        )
        disk_block = [sep_disk, *widgets.disk_lines(snapshot.disks, lang, theme)]

    # 11c. network section (Phase 5) — additive, same rule as the disk block: only
    # rendered when the snapshot carries interface data, so legacy/disk-only frames
    # keep their exact layout.
    net_block: list[str] = []
    if snapshot.net:
        sep_net = (
            f"  {dash * theme.net_dash_left} {i18n.t(lang, 'network')} "
            f"{dash * theme.net_dash_right}"
        )
        net_block = [sep_net, *widgets.net_lines(snapshot.net, lang, theme)]

    # 12. footer
    now = time.strftime("%H:%M:%S", localtime(snapshot.ts))
    footer = (
        f"{theme.bl}{eq * theme.ftr_eq_left} {now} · {i18n.t(lang, 'quit')} "
        f"{eq * theme.ftr_eq_right}{theme.br}"
    )

    return "\n".join([
        header, l_progress, l_elapsed, l_model, sep_umem, l_gtt,
        l_vram, l_ram, sep_power, l_power, l_sclk, *disk_block, *net_block, footer,
    ])


def make_renderer(cfg: Config, *, theme: Theme = DEFAULT, out=None) -> Callable[[Snapshot], None]:
    """Build a live renderer callable for loop.py: clear-then-redraw to ``out``."""
    import sys

    stream = out if out is not None else sys.stdout

    def _render(snapshot: Snapshot) -> None:
        stream.write("\x1b[H\x1b[2J")  # cursor home + clear (monitor.sh `clear`)
        stream.write(render_frame(snapshot, cfg, theme=theme))
        stream.write("\n")
        stream.flush()

    return _render
