"""GPU backends — amdgpu (Phase 2) and nvidia stub (Phase 5). DESIGN §2.2 B."""

from __future__ import annotations

from typing import Any

from .base import GpuBackend


def select_backend(ctx: Any) -> GpuBackend:
    """Pick a ``GpuBackend`` for ``ctx.root``: amdgpu if detected, else the nvidia stub.

    ``ctx`` only needs a ``.root`` attribute (a full ``CollectContext`` works, but
    so does anything duck-typed) — kept loosely typed here to avoid importing
    ``collectors.base`` and creating an import cycle (``collectors/base.py``
    already imports ``collectors.backends.base``). Local imports of the concrete
    backends keep this module import-order-safe too.
    """
    from .amdgpu import AmdgpuBackend
    from .nvidia import NvidiaBackend

    amdgpu = AmdgpuBackend(ctx.root)
    if amdgpu.detect():
        return amdgpu
    return NvidiaBackend(ctx.root)
