"""HALO status-line contract (O4 hybrid).

This module is the **stable machine-readable interface** between the ML scripts
(``train_directml.py``, ``eval_hard_tsc.py`` ...) and the consumers of their logs:
the halo-monitor dashboard AND the unattended monitoring agents. Treat the schema
below as an external contract — additive changes only, bump ``SCHEMA_VERSION`` for
anything breaking.

Design goals:
  * **Exception-safe emit.** ``emit_status()`` NEVER raises and NEVER prints a
    partial/garbled line. Dropping it into a live training script must be unable to
    break the run (see the in-flight-pipeline safety rule).
  * **Zero dependencies, single file.** Imports only the stdlib so an ML script in
    another repo can vendor *just this file* (copy it next to the script) or import
    it. Do not add cross-imports from the rest of halo_monitor here.
  * **Unambiguous to scrape.** Every status line starts with the ``HALOJSON``
    sentinel so a consumer can pick it out of noisy ML stdout without accidentally
    parsing other JSON the framework prints.

Wire format (one line, newline-terminated)::

    HALOJSON {"v":1,"job":"train","phase":"training","step":18,"total":39,"loss":0.6,"sstep":473.0}

Fields (v1)
-----------
Always:
  v      int    schema version (== SCHEMA_VERSION)
  job    str    JobType key: "train" | "score"
  phase  str    Phase key: see model.Phase (idle/quantizing/first_step/training/
                eval_save/score_prep/scoring/finished)
Optional, ``ts`` recommended:
  ts     number emitter wall-clock epoch seconds (float)
Phase-specific progress (emit what applies; omit the rest):
  quant_done, quant_total   int    quantization progress (quantizing / score_prep)
  step, total               int    optimizer step / total steps (training / eval_save)
  loss                      number training loss (avg) (training)
  sstep                     number seconds per optimizer step (training)
  gen_done, heldout_total   int    heldout tasks generated / total (scoring)
  last_gen                  str    short label of the last generated task (scoring)

Consumers MUST tolerate missing/extra keys and never crash on a malformed line.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterator

#: Bump only for a breaking change; add fields freely without bumping.
SCHEMA_VERSION = 1

#: Line sentinel. A status line is exactly ``PREFIX + <compact json object>``.
PREFIX = "HALOJSON "


# --------------------------------------------------------------------------- #
# Emitter — for the ML scripts. Keep this dead simple and exception-safe.
# --------------------------------------------------------------------------- #
def emit_status(stream: Any = None, **fields: Any) -> None:
    """Print one HALO status line. Never raises.

    Intended usage inside a training/scoring loop::

        from halo_monitor.status_schema import emit_status
        emit_status(job="train", phase="training", step=i, total=n, loss=l, sstep=s)

    ``v`` (schema version) is injected automatically. Any exception (serialization,
    broken pipe, closed stdout) is swallowed so the surrounding ML run is never
    affected.
    """
    try:
        payload = {"v": SCHEMA_VERSION}
        payload.update(fields)
        line = PREFIX + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        out = stream if stream is not None else sys.stdout
        out.write(line + "\n")
        try:
            out.flush()
        except Exception:
            pass
    except Exception:
        # In-flight pipeline safety: a status line must never break the ML run.
        pass


# --------------------------------------------------------------------------- #
# Consumer — for the parsers (jobs/status.py).
# --------------------------------------------------------------------------- #
def iter_status_lines(text: str) -> Iterator[dict[str, Any]]:
    """Yield every well-formed status payload found in ``text``, in order.

    Silently skips lines that don't start with :data:`PREFIX`, aren't valid JSON,
    aren't a JSON object, or carry an unknown schema version.
    """
    for raw in text.splitlines():
        # The sentinel may be preceded by a log timestamp/level prefix, so search
        # rather than require the line to start with it.
        idx = raw.find(PREFIX)
        if idx == -1:
            continue
        blob = raw[idx + len(PREFIX):].strip()
        try:
            obj = json.loads(blob)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        v = obj.get("v")
        if v is not None and v != SCHEMA_VERSION:
            # Unknown/newer schema: don't guess. Consumers fall back to regex.
            continue
        yield obj


def parse_last_status(text: str) -> dict[str, Any] | None:
    """Return the most recent well-formed status payload in ``text``, or ``None``."""
    last: dict[str, Any] | None = None
    for obj in iter_status_lines(text):
        last = obj
    return last
