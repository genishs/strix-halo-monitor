"""Test helpers: make ``src/`` importable without installation, and load fixtures."""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

FIXTURES = os.path.join(_HERE, "fixtures")
LOGS = os.path.join(FIXTURES, "logs")


def load_log(name: str) -> str:
    """Read a fixture log file's text."""
    with open(os.path.join(LOGS, name), encoding="utf-8") as fh:
        return fh.read()
