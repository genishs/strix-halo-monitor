"""Tests for jobs/detect.py — systemd unit detection (read-only, Phase 3).

No dependency on a real systemd/filesystem: ``run`` is a fake ``argv ->
stdout`` callable fed canned systemctl output, and log directories are real
``tempfile`` dirs so mtime-based "newest log" selection is exercised for
real. One optional live/read-only smoke test is included at the bottom,
guarded to call the real systemd exactly once and never a state-changing verb.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

import _util  # noqa: F401  (sets up sys.path)

from halo_monitor.config import config_from_env
from halo_monitor.jobs.detect import (
    _systemctl,
    find_active_unit,
    read_log_text,
)


def make_run(table: dict[tuple[str, ...], str]):
    """Fake systemctl runner: exact-argv-match table -> stdout. Unmatched -> ''."""

    def run(argv: list[str]) -> str:
        return table.get(tuple(argv), "")

    return run


def touch(path: str, mtime: float | None = None, content: str = "") -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class TestSystemctlReadOnlyGuard(unittest.TestCase):
    """C2 hard-gate: only list-units/is-active/show may ever reach a subprocess."""

    def test_allowed_verbs_pass_through(self):
        run = make_run({("systemctl", "--user", "is-active", "x.service"): "active\n"})
        self.assertEqual(_systemctl("is-active", "x.service", run=run), "active\n")

    def test_write_verbs_raise_value_error(self):
        for verb in ("start", "stop", "restart", "kill", "enable", "disable", "reload"):
            with self.subTest(verb=verb):
                with self.assertRaises(ValueError):
                    _systemctl(verb, "gpujob-train72bq4short.service")

    def test_empty_args_raise(self):
        with self.assertRaises(ValueError):
            _systemctl()

    def test_guard_fires_before_run_is_ever_called(self):
        calls = []

        def spy_run(argv):
            calls.append(argv)
            return ""

        with self.assertRaises(ValueError):
            _systemctl("stop", "x.service", run=spy_run)
        self.assertEqual(calls, [])  # subprocess never invoked


class TestFindActiveUnit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="halo-detect-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, ignore_errors=True))
        self.cfg = config_from_env(env={"HALO_LOG_DIR": self.tmpdir, "HALO_UNIT_GLOB": "gpujob-*"})

    def test_running_unit_preferred_over_logs(self):
        # Two logs on disk, but list-units reports one of them as running -- that
        # one must win even though it is not the newest log (monitor.sh:85-86).
        old_log = os.path.join(self.tmpdir, "gpujob-idle-1.log")
        touch(old_log, mtime=time.time() - 100)
        running_log = os.path.join(self.tmpdir, "gpujob-train72b-1.log")
        touch(running_log, mtime=time.time() - 200)  # older, yet must still be picked

        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): (
                "  gpujob-idle-1.service        loaded active exited  GPU job idle\n"
                "  gpujob-train72b-1.service    loaded active running GPU job train\n"
            ),
            ("systemctl", "--user", "is-active", "gpujob-train72b-1.service"): "active\n",
            ("systemctl", "--user", "show", "gpujob-train72b-1.service", "-p", "Result", "--value"): "",
        })

        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.name, "gpujob-train72b-1")  # .service suffix stripped
        self.assertEqual(ref.log_path, running_log)
        self.assertEqual(ref.active, "active")
        self.assertIsNone(ref.result)  # empty show output -> None, not ""

    def test_falls_back_to_newest_log_when_nothing_running(self):
        older = os.path.join(self.tmpdir, "gpujob-a.log")
        newer = os.path.join(self.tmpdir, "gpujob-b.log")
        touch(older, mtime=time.time() - 500)
        touch(newer, mtime=time.time() - 10)

        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): "",
            ("systemctl", "--user", "is-active", "gpujob-b.service"): "inactive\n",
            ("systemctl", "--user", "show", "gpujob-b.service", "-p", "Result", "--value"): "success\n",
        })

        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.name, "gpujob-b")
        self.assertEqual(ref.log_path, newer)
        self.assertEqual(ref.active, "inactive")
        self.assertEqual(ref.result, "success")

    def test_log_path_picks_newest_among_unit_prefixed_logs(self):
        # Same unit can have >1 matching log; newest by mtime wins (monitor.sh:87).
        unit = "gpujob-multi-20260101-000000-1"
        old = os.path.join(self.tmpdir, f"{unit}.log")
        newer = os.path.join(self.tmpdir, f"{unit}-retry.log")
        touch(old, mtime=time.time() - 300)
        touch(newer, mtime=time.time() - 5)

        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): (
                f"  {unit}.service loaded active running GPU job\n"
            ),
            ("systemctl", "--user", "is-active", f"{unit}.service"): "active\n",
            ("systemctl", "--user", "show", f"{unit}.service", "-p", "Result", "--value"): "",
        })

        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir)
        self.assertEqual(ref.log_path, newer)

    def test_none_when_no_running_unit_and_no_logs(self):
        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): "",
        })
        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir)
        self.assertIsNone(ref)

    def test_start_epoch_via_monotonic_and_btime(self):
        unit = "gpujob-mono-1"
        log = os.path.join(self.tmpdir, f"{unit}.log")
        touch(log, mtime=time.time() - 1000)  # decoy -- monotonic path must win

        proc_stat = os.path.join(self.tmpdir, "proc_stat")
        boot_epoch = 1_784_205_226
        touch(proc_stat, content=f"cpu  0 0 0 0\nbtime {boot_epoch}\nprocesses 1\n")

        mono_usec = 147_133_943_181  # ~147133.94s after boot
        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): (
                f"  {unit}.service loaded active running GPU job\n"
            ),
            ("systemctl", "--user", "is-active", f"{unit}.service"): "active\n",
            ("systemctl", "--user", "show", f"{unit}.service", "-p", "Result", "--value"): "",
            ("systemctl", "--user", "show", f"{unit}.service", "-p",
             "ActiveEnterTimestampMonotonic", "--value"): f"{mono_usec}\n",
        })

        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir, proc_stat_path=proc_stat)
        self.assertEqual(ref.start_epoch, boot_epoch + mono_usec // 1_000_000)

    def test_start_epoch_falls_back_to_log_mtime_when_monotonic_absent(self):
        unit = "gpujob-nomono-1"
        log = os.path.join(self.tmpdir, f"{unit}.log")
        mtime = int(time.time()) - 42
        touch(log, mtime=mtime)

        missing_proc_stat = os.path.join(self.tmpdir, "does-not-exist")
        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): (
                f"  {unit}.service loaded active running GPU job\n"
            ),
            ("systemctl", "--user", "is-active", f"{unit}.service"): "active\n",
            ("systemctl", "--user", "show", f"{unit}.service", "-p", "Result", "--value"): "",
            # no ActiveEnterTimestampMonotonic entry -> table default "" -> absent
        })

        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir, proc_stat_path=missing_proc_stat)
        self.assertEqual(ref.start_epoch, mtime)

    def test_start_epoch_zero_monotonic_treated_as_never_activated(self):
        # systemd convention: ActiveEnterTimestampMonotonic == 0 means "never
        # entered active state" -- must not be treated as a real epoch of 0.
        unit = "gpujob-zeromono-1"
        log = os.path.join(self.tmpdir, f"{unit}.log")
        mtime = int(time.time()) - 7
        touch(log, mtime=mtime)

        proc_stat = os.path.join(self.tmpdir, "proc_stat")
        touch(proc_stat, content="btime 1784205226\n")

        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): (
                f"  {unit}.service loaded active running GPU job\n"
            ),
            ("systemctl", "--user", "is-active", f"{unit}.service"): "active\n",
            ("systemctl", "--user", "show", f"{unit}.service", "-p", "Result", "--value"): "",
            ("systemctl", "--user", "show", f"{unit}.service", "-p",
             "ActiveEnterTimestampMonotonic", "--value"): "0\n",
        })

        ref = find_active_unit(self.cfg, run=run, listdir=os.listdir, proc_stat_path=proc_stat)
        self.assertEqual(ref.start_epoch, mtime)  # fell back to log mtime

    def test_start_epoch_none_when_nothing_available(self):
        unit = "gpujob-nothing-1"
        # No log file at all for this unit, no monotonic, no readable proc_stat.
        run = make_run({
            ("systemctl", "--user", "list-units", "gpujob-*", "--no-legend"): (
                f"  {unit}.service loaded active running GPU job\n"
            ),
            ("systemctl", "--user", "is-active", f"{unit}.service"): "active\n",
            ("systemctl", "--user", "show", f"{unit}.service", "-p", "Result", "--value"): "",
        })
        ref = find_active_unit(
            self.cfg, run=run, listdir=os.listdir,
            proc_stat_path=os.path.join(self.tmpdir, "does-not-exist"),
        )
        self.assertIsNotNone(ref)
        self.assertIsNone(ref.log_path)
        self.assertIsNone(ref.start_epoch)


class TestReadLogText(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="halo-detect-readlog-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, ignore_errors=True))

    def test_reads_full_file(self):
        from halo_monitor.jobs.base import UnitRef

        path = os.path.join(self.tmpdir, "u.log")
        text = "command python train.py --base /m/x\nstep 1 |\n"
        touch(path, content=text)
        ref = UnitRef(name="u", log_path=path)
        self.assertEqual(read_log_text(ref), text)

    def test_none_or_missing_path_returns_empty(self):
        from halo_monitor.jobs.base import UnitRef

        self.assertEqual(read_log_text(None), "")
        self.assertEqual(read_log_text(UnitRef(name="u", log_path=None)), "")
        self.assertEqual(
            read_log_text(UnitRef(name="u", log_path=os.path.join(self.tmpdir, "missing.log"))),
            "",
        )

    def test_max_bytes_truncates(self):
        from halo_monitor.jobs.base import UnitRef

        path = os.path.join(self.tmpdir, "u.log")
        touch(path, content="0123456789")
        ref = UnitRef(name="u", log_path=path)
        self.assertEqual(read_log_text(ref, max_bytes=4), "0123")


class TestLiveSmokeReadOnly(unittest.TestCase):
    """Optional: exercise the real systemd once. Read-only verbs only, no repeats.

    Skips itself gracefully if this box has no ``systemctl --user`` (e.g. CI
    without a user session) rather than failing the suite.
    """

    def test_real_find_active_unit_once(self):
        import shutil

        if shutil.which("systemctl") is None:
            self.skipTest("systemctl not available in this environment")

        cfg = config_from_env(env={})
        try:
            ref = find_active_unit(cfg)  # single call, default real run/listdir
        except Exception as exc:  # pragma: no cover - environment-dependent
            self.skipTest(f"live systemd query failed in this sandbox: {exc}")
            return

        # No assertions on specific unit identity (box-dependent) -- just type
        # sanity so this documents/records the live shape without being fragile.
        if ref is not None:
            self.assertIsInstance(ref.name, str)


if __name__ == "__main__":
    unittest.main()
