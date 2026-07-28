"""SWE-Bench Pro: the dataset record plus everything specific to running it.

The record (and its runnable surface) lives in ``record``; fetching the
per-instance auxiliary files (run_script + parser) in ``auxiliary``; compiling
an instance into a runnable unit-test evaluation (and its grader) in
``unit_test``. All SWE-Bench-Pro knowledge lives in this one package; adding
another dataset means adding a sibling package, not touching the general
loader/eval/rollout flows.
"""

from __future__ import annotations

from .record import COLUMNS, SweBenchProInstance

__all__ = [
    "COLUMNS",
    "SweBenchProInstance",
]
