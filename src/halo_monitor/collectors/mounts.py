"""Mount auto-discovery for the disk widget (Phase 5, DESIGN §2.2 B).

Why this exists: the disk widget used to report a *hardcoded* list of three mounts
(``/mnt/data``, one named external drive, ``/``). Any other mounted volume — a
second USB/external disk — was simply invisible, which is exactly how the bug was
found (two external drives connected, one shown). This module replaces that list
with a scan of what is *actually* mounted.

C2 INVARIANT (training must not be disturbed): discovery reads the single procfs
text file ``/proc/mounts`` and nothing else. No ``df``, no ``lsblk``, no subprocess,
no directory walk — so it is a few microseconds and zero disk I/O. Do not add a
shell-out here; besides the cost, mount paths contain spaces (``/run/media/user/새
볼륨1``) and quoting them through a shell is how this project has been bitten before.

Everything here is pure text -> data. The only I/O is :func:`read_mounts_text`,
which reads under an injected root so tests run against a fixture tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import DiskTarget

#: procfs table of currently mounted filesystems, relative to the injected root.
MOUNTS_REL_PATH = "proc/mounts"

#: Mount points under these prefixes are removable media managed by udisks2/GNOME
#: (``/run/media/<user>/<volume-label>``) or the older ``/media/<label>`` layout.
#: They are the drives that come and go, and the ones we label by volume name.
REMOVABLE_PREFIXES: tuple[str, ...] = ("/run/media/", "/media/")

#: Pseudo/virtual filesystems that carry no user-meaningful capacity. ``squashfs``
#: covers the ~16 snap loop mounts; ``overlay`` covers container layers. NOTE:
#: ``fuseblk`` is deliberately absent — that is ntfs-3g, i.e. a real data disk
#: (``/mnt/data`` here). Only dotted ``fuse.*`` names (fuse.portal, fuse.gvfsd-fuse,
#: fuse.snapfuse) are virtual; see :func:`_is_pseudo_fstype`.
EXCLUDED_FSTYPES: frozenset[str] = frozenset({
    "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs", "debugfs",
    "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs", "mqueue", "nsfs",
    "overlay", "pstore", "proc", "ramfs", "rpc_pipefs", "securityfs", "selinuxfs",
    "squashfs", "sysfs", "tmpfs", "tracefs",
})

#: Network filesystems are skipped on purpose: ``os.statvfs`` on a stale NFS/CIFS
#: mount can block for the server timeout, which would freeze the whole TUI tick.
#: A local hardware monitor has nothing to say about a remote share anyway.
NETWORK_FSTYPES: frozenset[str] = frozenset({
    "9p", "afs", "beegfs", "ceph", "cifs", "fuse.sshfs", "glusterfs", "lustre",
    "nfs", "nfs4", "smb3", "smbfs", "sshfs",
})

#: Mount points under these prefixes are noise even when the fstype looks real:
#: the EFI system partition, snap squashfs targets, and the runtime dirs. NOTE the
#: ``/run`` entry is overridden for ``/run/media`` (see :func:`_is_excluded_path`) —
#: removable media live under ``/run`` and must survive this filter.
EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    "/proc", "/sys", "/dev", "/boot", "/snap", "/var/snap", "/var/lib/docker",
    "/var/lib/snapd", "/run",
)

#: ``/proc/mounts`` octal escapes for characters that would break field splitting.
_ESCAPES = (("\\011", "\t"), ("\\012", "\n"), ("\\040", " "), ("\\134", "\\"))


@dataclass(frozen=True)
class Mount:
    """One row of ``/proc/mounts``: where it is mounted, from what, and what type."""

    path: str
    device: str
    fstype: str


def unescape(field: str) -> str:
    r"""Decode ``/proc/mounts`` octal escapes (``\040`` -> space, etc.).

    The kernel escapes space, tab, newline and backslash in the device and mount-point
    columns so the table stays whitespace-splittable. Without this, the external drive
    at ``/run/media/user/새 볼륨1`` parses as the nonexistent path
    ``/run/media/user/새\040볼륨1`` and every stat of it fails.

    ``\134`` (backslash) is decoded last so a literal ``\`` in a name cannot be
    re-interpreted as the lead-in of another escape.
    """
    for token, char in _ESCAPES:
        if token != "\\134":
            field = field.replace(token, char)
    return field.replace("\\134", "\\")


def parse_proc_mounts(text: str) -> list[Mount]:
    """Parse ``/proc/mounts`` text into :class:`Mount` rows, in kernel order.

    Rows with fewer than three fields are skipped rather than raising — this is a
    best-effort collector path (DESIGN: collectors never raise).
    """
    out: list[Mount] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        device, path, fstype = fields[0], fields[1], fields[2]
        out.append(Mount(path=unescape(path), device=unescape(device), fstype=fstype))
    return out


def is_removable(path: str) -> bool:
    """True for udisks2-style removable media mount points (``/run/media/...``)."""
    return any(path.startswith(p) for p in REMOVABLE_PREFIXES)


def _is_pseudo_fstype(fstype: str) -> bool:
    """True for virtual filesystems, including dotted ``fuse.*`` but NOT ``fuseblk``."""
    return fstype in EXCLUDED_FSTYPES or fstype.startswith("fuse.") or fstype == "fuse"


def _is_excluded_path(path: str) -> bool:
    """True when the mount point itself is uninteresting (EFI, snap, runtime dirs)."""
    if is_removable(path):
        return False  # removable media live under /run — they beat the /run rule
    return any(path == p or path.startswith(p + "/") for p in EXCLUDED_PATH_PREFIXES)


def is_noise(mount: Mount) -> bool:
    """True when a mount should not be shown to the user."""
    if _is_pseudo_fstype(mount.fstype) or mount.fstype in NETWORK_FSTYPES:
        return True
    return _is_excluded_path(mount.path)


def _order_key(mount: Mount) -> tuple[int, int, str]:
    """Display order: fixed data mounts, then removable media, then ``/`` last.

    This preserves the ordering the widget shipped with (``/mnt/data``, external, ``/``)
    so the frame does not visually reshuffle when auto-discovery takes over.
    """
    return (mount.path == "/", is_removable(mount.path), mount.path)


def _dedup_by_device(mounts: list[Mount]) -> list[Mount]:
    """Drop bind-mount duplicates: the same block device mounted at several paths.

    Only real ``/dev/*`` devices are de-duplicated — virtual sources share names like
    ``none``/``tmpfs`` and are filtered out by fstype anyway. First occurrence wins,
    which after :func:`_order_key` is the shortest/most canonical path.
    """
    seen: set[str] = set()
    out: list[Mount] = []
    for m in mounts:
        if m.device.startswith("/dev/"):
            if m.device in seen:
                continue
            seen.add(m.device)
        out.append(m)
    return out


def _labels_for(mounts: list[Mount]) -> list[str]:
    """Label per mount: volume folder name for removable media, path for fixed ones.

    A removable drive shows as ``새 볼륨1`` rather than the unreadably long
    ``/run/media/user/새 볼륨1``. If two drives happen to share a volume name, both
    fall back to their full path so the rows stay distinguishable.
    """
    raw = [os.path.basename(m.path) if is_removable(m.path) else m.path for m in mounts]
    dupes = {lbl for lbl in raw if raw.count(lbl) > 1}
    return [m.path if lbl in dupes else lbl for m, lbl in zip(mounts, raw)]


def discover_from_text(text: str) -> list[DiskTarget]:
    """Pure core: ``/proc/mounts`` text -> ordered, filtered, labelled targets."""
    kept = _dedup_by_device(sorted(
        (m for m in parse_proc_mounts(text) if not is_noise(m)), key=_order_key
    ))
    return [DiskTarget(path=m.path, label=lbl) for m, lbl in zip(kept, _labels_for(kept))]


def read_mounts_text(root: str = "/") -> str:
    """Read ``<root>/proc/mounts``; empty string when unreadable (never raises)."""
    try:
        with open(os.path.join(root, MOUNTS_REL_PATH), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def discover(root: str = "/") -> list[DiskTarget]:
    """Every user-meaningful mounted filesystem under ``root``, in display order."""
    return discover_from_text(read_mounts_text(root))
