"""Oracle failures: cached rollout failures, runnable as their own dataset.

One record per failed rollout of an instance from another registered dataset.
The record **delegates** the instance itself — run context, prompt, gold patch,
grading — to that dataset's record, and adds only the failure: the typed
conversation, the grader's verdict and the submitted patch, staged into every
run of it through ``TaskInstance.mounts``. ``record`` is the loader half;
``build`` turns a finished ``rollout_and_unit_test`` run into a row.
"""

from __future__ import annotations

from .record import COLUMNS, OracleFailureInstance

__all__ = [
    "COLUMNS",
    "OracleFailureInstance",
]
