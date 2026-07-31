"""Parse the job's ``command`` line into a :class:`ModelInfo` (DESIGN §2.2 C).

Mirrors monitor.sh's ``grep -oE '--opt +value'`` extraction. Label mapping is
externalized to :class:`Config` (DESIGN O13) instead of a hardcoded case statement.
Pure and exception-free.
"""

from __future__ import annotations

import os
import re

from ..config import Config
from ..model import ModelInfo
from ._scrape import find_command_line

# Leading (?:^|\s) prevents matching a longer option name as a prefix; monitor.sh
# required a space too.
#
# PATH-valued options (``--base``/``--adapter``) may contain spaces — the model store
# lives at ``/run/media/user/새 볼륨/<model>`` — so their value runs from right after
# the option until the next `` --flag`` or end of line. A naive ``\S+`` truncates at the
# first space, leaving basename ``새`` instead of the real model name (the reported bug).
# Numeric/identifier options keep the simple single-token capture.
_PATH_VALUE = r"(?:^|\s)--{}\s+(.+?)(?=\s+--|\s*$)"
_VALUE_OPTS = {
    "base": re.compile(_PATH_VALUE.format("base")),
    "nbits": re.compile(r"(?:^|\s)--hqq-nbits\s+(\d+)"),
    "seq": re.compile(r"(?:^|\s)--seq\s+(\d+)"),
    "max_new": re.compile(r"(?:^|\s)--max-new\s+(\d+)"),
    "lora_r": re.compile(r"(?:^|\s)--lora-r\s+(\d+)"),
    "epochs": re.compile(r"(?:^|\s)--epochs\s+(\d+)"),
    "adapter": re.compile(_PATH_VALUE.format("adapter")),
    "eval_label": re.compile(r"(?:^|\s)--label\s+(\S+)"),
}
_FLAG_LORA_MLP = re.compile(r"(?:^|\s)--lora-mlp(?:\s|$)")
_FLAG_HELDOUT = re.compile(r"(?:^|\s)--heldout(?:\s|$)")


def _str_opt(cmd: str, key: str) -> str | None:
    m = _VALUE_OPTS[key].search(cmd)
    if m is None:
        return None
    value = m.group(1).strip()   # defensive: never leak surrounding whitespace
    return value or None


def _int_opt(cmd: str, key: str) -> int | None:
    m = _VALUE_OPTS[key].search(cmd)
    return int(m.group(1)) if m else None


def parse_command(cmdline: str | None, cfg: Config) -> ModelInfo:
    """Parse a single ``command ...`` line into ModelInfo."""
    if not cmdline:
        return ModelInfo()

    base_raw = _str_opt(cmdline, "base")
    base_bn = os.path.basename(base_raw) if base_raw else None
    adapter_raw = _str_opt(cmdline, "adapter")
    adapter_bn = os.path.basename(adapter_raw) if adapter_raw else None

    return ModelInfo(
        base_raw=base_raw,
        base_bn=base_bn,
        base_label=cfg.base_label_for(base_bn),
        nbits=_int_opt(cmdline, "nbits"),
        seq=_int_opt(cmdline, "seq"),
        max_new=_int_opt(cmdline, "max_new"),
        lora_r=_int_opt(cmdline, "lora_r"),
        lora_mlp=bool(_FLAG_LORA_MLP.search(cmdline)),
        epochs=_int_opt(cmdline, "epochs"),
        adapter=adapter_bn,
        heldout=bool(_FLAG_HELDOUT.search(cmdline)),
        eval_label=_str_opt(cmdline, "eval_label"),
    )


def parse_model_info(log_text: str, cfg: Config) -> ModelInfo:
    """Find the command line in a full log and parse it (empty ModelInfo if absent)."""
    return parse_command(find_command_line(log_text), cfg)
