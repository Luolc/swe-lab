"""SWE-Bench Pro: the dataset record (``record``) plus everything specific to
running it — the adapter that builds an ``EvalSpec`` (``execution``) and the
grader that runs it and grades a patch (``grading``). All SWE-Bench-Pro
knowledge lives in this one package; adding another dataset means adding a
sibling package, not touching the general loader/eval/rollout flows.
"""

from __future__ import annotations

from .execution import image_ref, SweBenchProAdapter
from .grading import build_eval_script, EvalResult, evaluate
from .record import COLUMNS, SweBenchProInstance

__all__ = [
    "COLUMNS",
    "EvalResult",
    "SweBenchProAdapter",
    "SweBenchProInstance",
    "build_eval_script",
    "evaluate",
    "image_ref",
]
