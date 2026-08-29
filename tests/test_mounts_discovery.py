"""Tests for mount auto-discovery (collectors/mounts.py, Phase 5.1).

Regression context: the disk widget used to report a hardcoded three-mount list, so
a second external drive was invisible even while mounted. The fixture below is a
trimmed copy of the real ``/proc/mounts`` from the machine where that was found —
two external drives, Korean volume names with an escaped space, snap loops, and the
usual pseudo-filesystem noise. ``test_finds_both_external_drives`` is the bug.

Everything here is pure text -> data: no real mount is touched.
"""

import os
import tempfile
import unittest

import _util  # noqa: F401

from halo_monitor.collectors import mounts as M

# Trimmed from the real /proc/mounts (2 externals, snaps, gvfs, EFI, tmpfs...).
# NOTE the literal ``\040`` escapes — that is exactly how the kernel writes a space.
PROC_MOUNTS = r"""sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
udev /dev devtmpfs rw,nosuid,relatime,size=31799188k,mode=755 0 0
tmpfs /run tmpfs rw,nosuid,nodev,size=12780588k,mode=755 0 0
/dev/nvme0n1p4 / ext4 rw,relatime 0 0
tmpfs /dev/shm tmpfs rw,nosuid,nodev 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev,size=31951468k 0 0
/dev/loop0 /snap/core24/1587 squashfs ro,nodev,relatime 0 0
/dev/loop5 /snap/firefox/8664 squashfs ro,nodev,relatime 0 0
/dev/nvme0n1p2 /mnt/data fuseblk rw,relatime,user_id=0,group_id=0 0 0
/dev/nvme0n1p5 /boot/efi vfat rw,relatime,fmask=0022 0 0
tmpfs /run/user/1000 tmpfs rw,nosuid,nodev,relatime,size=6390292k 0 0
gvfsd-fuse /run/user/1000/gvfs fuse.gvfsd-fuse rw,nosuid,nodev,relatime 0 0
portal /run/user/1000/doc fuse.portal rw,nosuid,nodev,relatime 0 0
/dev/sda2 /run/media/user/새\040볼륨 exfat rw,nosuid,nodev,relatime,uid=1000 0 0
/dev/sdb2 /run/media/user/새\040볼륨1 ntfs3 rw,nosuid,nodev,relatime,uid=1000 0 0
"""

EXT1 = "/run/media/user/새 볼륨"
EXT2 = "/run/media/user/새 볼륨1"


def paths(targets):
    return [t.path for t in targets]


def labels(targets):
    return [t.label for t in targets]


def write_fixture_root(tmpdir, text):
    """Materialise ``<tmpdir>/proc/mounts`` so ``discover(root=tmpdir)`` can read it."""
    proc = os.path.join(tmpdir, "proc")
    os.makedirs(proc, exist_ok=True)
    with open(os.path.join(proc, "mounts"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return tmpdir


class TestUnescape(unittest.TestCase):
    def test_space_escape_is_decoded(self):
        # The whole bug class: an undecoded path can never be stat'd.
        self.assertEqual(M.unescape(r"/run/media/user/새\040볼륨1"), EXT2)

    def test_tab_newline_backslash(self):
        self.assertEqual(M.unescape(r"a\011b"), "a\tb")
        self.assertEqual(M.unescape(r"a\012b"), "a\nb")
        self.assertEqual(M.unescape(r"a\134b"), "a\\b")

    def test_escaped_backslash_is_not_re_interpreted(self):
        # A literal backslash followed by "040" must stay text, not become a space.
        self.assertEqual(M.unescape(r"a\134040b"), r"a\040b")

    def test_plain_path_untouched(self):
        self.assertEqual(M.unescape("/mnt/data"), "/mnt/data")


class TestParse(unittest.TestCase):
    def test_fields_and_order(self):
        rows = M.parse_proc_mounts(PROC_MOUNTS)
        root = [r for r in rows if r.path == "/"][0]
        self.assertEqual((root.device, root.fstype), ("/dev/nvme0n1p4", "ext4"))

    def test_escaped_path_decoded_during_parse(self):
        self.assertIn(EXT2, [r.path for r in M.parse_proc_mounts(PROC_MOUNTS)])

    def test_short_and_blank_lines_skipped_not_raised(self):
        self.assertEqual(M.parse_proc_mounts("\n\ngarbage\na b\n"), [])


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.found = M.discover_from_text(PROC_MOUNTS)

    def test_finds_both_external_drives(self):
        """THE BUG: two drives mounted, both must be reported (was: only one)."""
        self.assertIn(EXT1, paths(self.found))
        self.assertIn(EXT2, paths(self.found))

    def test_finds_fixed_mounts(self):
        self.assertIn("/mnt/data", paths(self.found))
        self.assertIn("/", paths(self.found))

    def test_exact_set_is_the_four_real_filesystems(self):
        self.assertEqual(set(paths(self.found)), {"/mnt/data", EXT1, EXT2, "/"})

    def test_pseudo_filesystems_excluded(self):
        got = paths(self.found)
        for noise in ("/sys", "/proc", "/dev", "/dev/shm", "/tmp", "/run"):
            self.assertNotIn(noise, got)

    def test_snap_squashfs_loops_excluded(self):
        self.assertFalse([p for p in paths(self.found) if p.startswith("/snap/")])

    def test_boot_efi_excluded(self):
        self.assertNotIn("/boot/efi", paths(self.found))

    def test_gvfs_and_runtime_dirs_excluded_but_media_kept(self):
        got = paths(self.found)
        self.assertNotIn("/run/user/1000/gvfs", got)
        self.assertNotIn("/run/user/1000/doc", got)
        self.assertIn(EXT1, got)          # /run/media survives the /run rule

    def test_fuseblk_is_a_real_disk_not_pseudo(self):
        # ntfs-3g reports "fuseblk"; excluding all fuse.* must not swallow it.
        self.assertIn("/mnt/data", paths(self.found))

    def test_labels_removable_by_volume_name_fixed_by_path(self):
        by_path = {t.path: t.label for t in self.found}
        self.assertEqual(by_path[EXT1], "새 볼륨")
        self.assertEqual(by_path[EXT2], "새 볼륨1")
        self.assertEqual(by_path["/mnt/data"], "/mnt/data")
        self.assertEqual(by_path["/"], "/")

    def test_display_order_fixed_then_removable_then_root(self):
        self.assertEqual(paths(self.found), ["/mnt/data", EXT1, EXT2, "/"])

    def test_duplicate_volume_names_fall_back_to_full_path(self):
        text = (
            "/dev/sda2 /run/media/user/DATA exfat rw 0 0\n"
            "/dev/sdb2 /run/media/other/DATA exfat rw 0 0\n"
        )
        self.assertEqual(
            labels(M.discover_from_text(text)),
            ["/run/media/other/DATA", "/run/media/user/DATA"],
        )

    def test_bind_mount_duplicate_device_deduped(self):
        text = (
            "/dev/nvme0n1p2 /mnt/data ext4 rw 0 0\n"
            "/dev/nvme0n1p2 /mnt/data/bind ext4 rw 0 0\n"
        )
        self.assertEqual(paths(M.discover_from_text(text)), ["/mnt/data"])

    def test_network_filesystems_excluded(self):
        # statvfs on a stale NFS/CIFS mount can block and freeze the whole TUI tick.
        text = (
            "server:/vol /mnt/nfs nfs4 rw 0 0\n"
            "//host/share /mnt/smb cifs rw 0 0\n"
            "/dev/sda1 /mnt/local ext4 rw 0 0\n"
        )
        self.assertEqual(paths(M.discover_from_text(text)), ["/mnt/local"])

    def test_empty_or_unreadable_table_yields_nothing(self):
        self.assertEqual(M.discover_from_text(""), [])

    def test_discover_reads_under_injected_root(self):
        with tempfile.TemporaryDirectory() as td:
            write_fixture_root(td, PROC_MOUNTS)
            self.assertEqual(paths(M.discover(td)), ["/mnt/data", EXT1, EXT2, "/"])

    def test_discover_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(M.discover(td), [])   # must not raise


if __name__ == "__main__":
    unittest.main()
