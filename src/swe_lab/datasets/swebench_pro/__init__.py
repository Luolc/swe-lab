"""SWE-Bench Pro: the dataset record plus everything specific to running it.

The record lives in ``record``; run setup (image ref + harness fetch) in
``execution``; compiling an instance into a runnable unit-test evaluation (and
its grader) in ``unit_test``. All SWE-Bench-Pro knowledge lives in this one
package; adding another dataset means adding a sibling package, not touching the
general loader/eval/rollout flows.
"""

from __future__ import annotations

from .execution import image_ref
from .record import COLUMNS, SweBenchProInstance

__all__ = [
    "COLUMNS",
    "SweBenchProInstance",
    "image_ref",
]
