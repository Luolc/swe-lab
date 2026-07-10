"""SWE-bench Pro: the dataset record (``record``) plus everything specific to
running/grading it (``execution``). All SWE-bench-Pro knowledge lives in this
one package; adding another dataset means adding a sibling package, not touching
the general loader/eval/rollout flows.
"""

from __future__ import annotations

from .execution import image_ref, SweBenchProAdapter
from .record import COLUMNS, SweBenchProInstance

__all__ = [
    "COLUMNS",
    "SweBenchProAdapter",
    "SweBenchProInstance",
    "image_ref",
]
