"""The workflow layer: tasks above the sandbox engine (ADR-0007).

``Task`` is the generic unit — one sandbox, three total hooks, one
``execute`` — and (in a later task) ``Workflow`` will chain declared tasks by
matching output to input store names. The shipped concrete tasks live with
their domains: ``swe_lab.rollout.CodingAgentTask`` and
``swe_lab.evaluation.methods.unit_test.UnitTestEvalTask``.
"""

from .run_task import (
    read_marker,
    run_task,
    TaskAddress,
    TaskOutcome,
    TaskRunOutcome,
    TerminalMarker,
)
from .task import Task, TaskResult

__all__ = [
    "Task",
    "TaskAddress",
    "TaskOutcome",
    "TaskResult",
    "TaskRunOutcome",
    "TerminalMarker",
    "read_marker",
    "run_task",
]
