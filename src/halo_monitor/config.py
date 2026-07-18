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
    cfg = Config(
        log_dir=env.get("HALO_LOG_DIR", os.path.expanduser("~/gpu_jobs/logs")),
        unit_glob=env.get("HALO_UNIT_GLOB", "gpujob-*"),
        title=env.get("HALO_TITLE", "Strix Halo Train/Score Monitor"),
        pool_gb=_int(env, "HALO_POOL_GB", 60),
        heldout_total=_int(env, "HALO_HELDOUT_TOTAL", 7),
        lang=lang,
    )
    return cfg


def with_overrides(cfg: Config, **kwargs) -> Config:
    """Return a copy of ``cfg`` with selected fields replaced (CLI layer helper)."""
    return replace(cfg, **{k: v for k, v in kwargs.items() if v is not None})
