"""Configuration layer (DESIGN §2.2 E).

Layered merge, later wins: **defaults -> config file (TOML, later phase) -> env
(HALO_*) -> CLI**. This module implements defaults + env now; it is 100%
backward-compatible with the bash tool's env-only interface (``HALO_LOG_DIR`` etc.).
TOML support is additive and lands in a later phase (DESIGN O2).

``Config`` is immutable and depends on nothing — every other part reads it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Mapping

#: base-model directory name -> display label (DESIGN O13: externalized, was the
#: hardcoded ``base_label_for`` case statement in monitor.sh). Override via Config.
DEFAULT_LABEL_MAP: dict[str, str] = {
    "mistral-large-2411": "Mistral-Large 123B",
    "qwen2.5-72b-instruct": "Qwen2.5 72B",
    "qwen2.5-coder-32b": "Qwen2.5-Coder 32B",
    "qwen2.5-coder-14b": "Qwen2.5-Coder 14B",
}


@dataclass(frozen=True)
class DiskTarget:
    """One filesystem mount the disk widget reports on (DESIGN §3 "새 지표 추가").

    ``label`` is the display name; when ``None`` the renderer shows ``path`` itself.
    Immutable so ``Config`` stays frozen/hashable.
    """

    path: str
    label: str | None = None


#: Cap on *auto-discovered* mounts, so a machine with many volumes cannot push the
#: rest of the frame off-screen. When more are found, the largest by capacity win
#: (see ``collectors/disk.py``). An explicit ``HALO_DISK_MOUNTS`` list is never
#: capped — if the user named those mounts, they get all of them.
DEFAULT_DISK_MAX_MOUNTS = 8

#: How often auto-discovery re-reads ``/proc/mounts``, in seconds. This bounds
#: hot-plug latency: a drive connected mid-run appears within this window instead of
#: needing a restart, without re-scanning on every ~2s tick.
DEFAULT_DISK_RESCAN_S = 5.0


@dataclass(frozen=True)
class NetTarget:
    """One network interface the throughput widget reports on (DESIGN §3, Phase 5).

    ``label`` is the display name; when ``None`` the renderer shows the raw ``name``
    (e.g. ``eth0``). Immutable so ``Config`` stays frozen/hashable.
    """

    name: str
    label: str | None = None


#: How the network collector picks interfaces when none are configured explicitly
#: (``net_ifaces is None``): "default" = the default-route interface (what actually
#: reaches the internet), falling back to every non-loopback interface if no default
#: route is found; "all" = every non-loopback interface unconditionally.
NET_AUTO_DEFAULT = "default"
NET_AUTO_ALL = "all"


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration. Field defaults mirror monitor.sh."""

    log_dir: str = os.path.expanduser("~/gpu_jobs/logs")
    unit_glob: str = "gpujob-*"
    title: str = "Strix Halo Train/Score Monitor"
    pool_gb: int = 60
    heldout_total: int = 7
    lang: str = "ko"                     # "ko" | "en"
    interval_s: float = 2.0
    sysfs_root: str = "/"                # injectable for tests (collectors, Phase 2)
    label_map: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_LABEL_MAP))
    # --- disk widget (Phase 5) ---
    #: Explicit mounts to show. ``None`` = auto-discover every real mounted
    #: filesystem (``collectors/mounts.py``); an empty tuple = widget disabled; a
    #: non-empty tuple = show exactly these. Mirrors ``net_ifaces`` below.
    disk_mounts: tuple[DiskTarget, ...] | None = None
    disk_warn_free_gb: float = 10.0      # warn when free space < this many GiB ...
    disk_warn_free_pct: float = 5.0      # ... OR free fraction < this percent
    disk_max_mounts: int = DEFAULT_DISK_MAX_MOUNTS   # cap on auto-discovered mounts
    disk_rescan_s: float = DEFAULT_DISK_RESCAN_S     # auto-discovery cache TTL (s)
    # --- network throughput widget (Phase 5) ---
    #: Explicit interfaces to show. ``None`` = auto-detect via ``net_auto``; an empty
    #: tuple = widget disabled; a non-empty tuple = show exactly these.
    net_ifaces: tuple[NetTarget, ...] | None = None
    net_auto: str = NET_AUTO_DEFAULT     # auto-detect strategy when net_ifaces is None

    def base_label_for(self, base_bn: str | None) -> str | None:
        """Map a base-model directory basename to a display label.

        Falls back to the basename itself when unmapped (monitor.sh behaviour), and
        to ``None`` for an empty/absent base.
        """
        if not base_bn:
            return None
        return self.label_map.get(base_bn, base_bn)


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(env[key])
    except (KeyError, ValueError):
        return default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env[key])
    except (KeyError, ValueError):
        return default


def _parse_disk_mounts(spec: str | None) -> tuple[DiskTarget, ...] | None:
    """Parse ``HALO_DISK_MOUNTS`` into a tuple of ``DiskTarget`` (or ``None``).

    Format: ``;``-separated entries, each ``label=path`` or a bare ``path`` (label
    then defaults to the path). ``;`` is the separator because mount paths may
    contain spaces (e.g. ``/run/media/user/새 볼륨``) but not semicolons. An empty
    value (``HALO_DISK_MOUNTS=``) disables the widget (returns an empty tuple).
    ``None`` (unset) means "auto-discover the mounted filesystems".
    """
    if spec is None:
        return None
    entries: list[DiskTarget] = []
    for raw in spec.split(";"):
        entry = raw.strip()
        if not entry:
            continue
        if "=" in entry:
            label, _, path = entry.partition("=")
            label, path = label.strip(), path.strip()
            entries.append(DiskTarget(path=path, label=label or path))
        else:
            entries.append(DiskTarget(path=entry, label=entry))
    return tuple(entries)


def _parse_net_ifaces(spec: str | None) -> tuple[NetTarget, ...] | None:
    """Parse ``HALO_NET_IFACES`` into a tuple of ``NetTarget`` (or ``None``).

    Format mirrors ``HALO_DISK_MOUNTS``: ``;``-separated entries, each ``label=name``
    or a bare ``name`` (label then defaults to the name). Interface names never
    contain spaces, but ``;`` stays the separator for symmetry with the disk widget.
    An empty value (``HALO_NET_IFACES=``) disables the widget (empty tuple). ``None``
    (unset) means "auto-detect" (see ``net_auto``).
    """
    if spec is None:
        return None
    entries: list[NetTarget] = []
    for raw in spec.split(";"):
        entry = raw.strip()
        if not entry:
            continue
        if "=" in entry:
            label, _, name = entry.partition("=")
            label, name = label.strip(), name.strip()
            entries.append(NetTarget(name=name, label=label or name))
        else:
            entries.append(NetTarget(name=entry, label=entry))
    return tuple(entries)


def config_from_env(
    env: Mapping[str, str] | None = None,
    *,
    lang_override: str | None = None,
) -> Config:
    """Build a Config from defaults + ``HALO_*`` environment variables.

    ``lang_override`` (from a ``--english``/``-e`` CLI flag) wins over ``HALO_LANG``,
    matching monitor.sh's "flag beats env" precedence.
    """
    env = os.environ if env is None else env
    lang = "en" if env.get("HALO_LANG", "ko") == "en" else "ko"
    if lang_override in ("ko", "en"):
        lang = lang_override
    # None (unset) stays None all the way through: that is the "auto-discover" signal.
    mounts = _parse_disk_mounts(env.get("HALO_DISK_MOUNTS"))
    net_ifaces = _parse_net_ifaces(env.get("HALO_NET_IFACES"))
    net_auto = NET_AUTO_ALL if env.get("HALO_NET_AUTO") == NET_AUTO_ALL else NET_AUTO_DEFAULT
    cfg = Config(
        log_dir=env.get("HALO_LOG_DIR", os.path.expanduser("~/gpu_jobs/logs")),
        unit_glob=env.get("HALO_UNIT_GLOB", "gpujob-*"),
        title=env.get("HALO_TITLE", "Strix Halo Train/Score Monitor"),
        pool_gb=_int(env, "HALO_POOL_GB", 60),
        heldout_total=_int(env, "HALO_HELDOUT_TOTAL", 7),
        lang=lang,
        disk_mounts=mounts,
        disk_warn_free_gb=_float(env, "HALO_DISK_WARN_GB", 10.0),
        disk_warn_free_pct=_float(env, "HALO_DISK_WARN_PCT", 5.0),
        disk_max_mounts=_int(env, "HALO_DISK_MAX", DEFAULT_DISK_MAX_MOUNTS),
        disk_rescan_s=_float(env, "HALO_DISK_RESCAN_S", DEFAULT_DISK_RESCAN_S),
        net_ifaces=net_ifaces,
        net_auto=net_auto,
    )
    return cfg


def with_overrides(cfg: Config, **kwargs) -> Config:
    """Return a copy of ``cfg`` with selected fields replaced (CLI layer helper)."""
    return replace(cfg, **{k: v for k, v in kwargs.items() if v is not None})
