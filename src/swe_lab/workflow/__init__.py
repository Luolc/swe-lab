"""The workflow layer: tasks above the sandbox engine (ADR-0007).

``Task`` is the generic unit — one sandbox, three total hooks, one ``execute``
— and ``Workflow`` chains declared tasks by matching output to input store
names. Everything here is instance-agnostic: the instance binds at
``execute``, which is what lets a definition be written statically and
registered by name (``swe_lab.workflow.definitions``, imported on demand — it
names the concrete tasks, which live with their domains:
``swe_lab.rollout.CodingAgentTask`` and
``swe_lab.evaluation.unit_test.UnitTestTask``).
"""

from .registry import (
    build_workflow,
    register_workflow,
    registered_workflows,
    workflow_definition,
    WorkflowDef,
)
from .run_task import (
    read_marker,
    run_task,
    TaskAddress,
    TaskOutcome,
    TaskRunOutcome,
    TerminalMarker,
)
from .task import AttemptResult, InputsBuilder, Task
from .workflow import (
    EntryOutcome,
    EntryStatus,
    validate_declaration,
    Workflow,
    WorkflowEntry,
    WorkflowError,
    WorkflowOutcome,
)

__all__ = [
    "AttemptResult",
    "EntryOutcome",
    "EntryStatus",
    "InputsBuilder",
    "Task",
    "TaskAddress",
    "TaskOutcome",
    "TaskRunOutcome",
    "TerminalMarker",
    "Workflow",
    "WorkflowDef",
    "WorkflowEntry",
    "WorkflowError",
    "WorkflowOutcome",
    "build_workflow",
    "read_marker",
    "register_workflow",
    "registered_workflows",
    "run_task",
    "validate_declaration",
    "workflow_definition",
]
