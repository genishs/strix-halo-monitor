"""Tests for the HALOJSON status-line contract (status_schema.py, ADR-0002)."""

import io
import unittest

import _util  # noqa: F401

from halo_monitor.status_schema import (
    PREFIX,
    SCHEMA_VERSION,
    emit_status,
    iter_status_lines,
    parse_last_status,
)


class TestEmit(unittest.TestCase):
    def test_emit_roundtrip(self):
        buf = io.StringIO()
        emit_status(buf, job="train", phase="training", step=5, total=39, loss=0.7)
        line = buf.getvalue()
        self.assertTrue(line.startswith(PREFIX))
        self.assertTrue(line.endswith("\n"))
        obj = parse_last_status(line)
        self.assertEqual(obj["v"], SCHEMA_VERSION)
        self.assertEqual(obj["phase"], "training")
        self.assertEqual(obj["step"], 5)

    def test_emit_never_raises(self):
        class Boom:
            def write(self, *_):
                raise IOError("broken pipe")

            def flush(self):
                raise IOError("broken pipe")

        # Must swallow the exception (in-flight pipeline safety).
        emit_status(Boom(), job="train", phase="training", step=1)

    def test_emit_non_serializable_is_safe(self):
        buf = io.StringIO()
        emit_status(buf, job="train", weird=object())  # not JSON serializable
        self.assertEqual(buf.getvalue(), "")  # nothing partial written


class TestConsume(unittest.TestCase):
    def test_picks_last_of_many(self):
        text = "\n".join([
            'HALOJSON {"v":1,"job":"train","phase":"training","step":1}',
            "some noise line",
            'HALOJSON {"v":1,"job":"train","phase":"training","step":2}',
        ])
        self.assertEqual(parse_last_status(text)["step"], 2)

    def test_prefix_may_have_leading_log_decoration(self):
        text = '2026-07-18 12:00:00 INFO HALOJSON {"v":1,"phase":"idle"}'
        self.assertEqual(parse_last_status(text)["phase"], "idle")

    def test_ignores_malformed_and_non_object(self):
        text = "\n".join([
            "HALOJSON not-json",
            "HALOJSON [1,2,3]",
            "HALOJSON {}",
        ])
        objs = list(iter_status_lines(text))
        self.assertEqual(objs, [{}])  # only the empty object is a valid dict payload

    def test_none_when_absent(self):
        self.assertIsNone(parse_last_status("no status here\njust logs\n"))

    def test_unknown_version_skipped(self):
        text = 'HALOJSON {"v":999,"phase":"training"}'
        self.assertIsNone(parse_last_status(text))


if __name__ == "__main__":
    unittest.main()
