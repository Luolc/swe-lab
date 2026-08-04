"""The shipped workflow definitions: solve, grade, and the chain of both.

Statically written, registered at import, invoked by name against any
instance. This module is the one place where a shipped workflow names a
concrete agent — the layers below it (``Task``, ``Workflow``, the two
compositions) stay harness-agnostic, and swapping the agent is a registry
question this repo will answer when a second harness exists.

Deliberately **not** imported by ``swe_lab.workflow``: the engine must not
depend on the tasks, and the tasks import the engine. Whoever wants the
built-ins imports this module (the CLI does), exactly as a downstream user
imports their own.
"""

from __future__ import annotations

from swe_lab.evaluation.methods.unit_test import UnitTestEvalTask
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    DEFAULT_MODEL,
    OAUTH_TOKEN_ENV,
)
from swe_lab.rollout import CodingAgentTask
from swe_lab.sandbox import SandboxConfig

from .registry import register_workflow, WorkflowDef
from .workflow import WorkflowEntry

# The entry keys, which are also the task segment of every record a run of
# these workflows persists (ADR-0007 §6). Stable: resume trusts them.
SOLVE_KEY = "rollout"
GRADE_KEY = "eval"

_AGENT_TIMEOUT_S = 1800.0
_EVAL_TIMEOUT_S = 1800.0
# One extra grading attempt absorbs a harness flake without hiding a real
# failure: the patch is identical on every attempt (ADR-0008).
_EVAL_RETRIES = 1

SOLVE: WorkflowDef = (
    WorkflowEntry(
        SOLVE_KEY,
        CodingAgentTask(harness=ClaudeCodeHarness(model=DEFAULT_MODEL)),
        timeout=_AGENT_TIMEOUT_S,
        # The agent needs the network, and its credential travels by name so
        # the value never reaches a command line.
        sandbox=SandboxConfig(network=True, pass_env=(OAUTH_TOKEN_ENV,)),
    ),
)

GRADE: WorkflowDef = (
    WorkflowEntry(
        GRADE_KEY,
        # Its patch input comes from whoever runs it: an earlier entry in a
        # chain, or the caller's own bytes in this standalone definition.
        UnitTestEvalTask(),
        timeout=_EVAL_TIMEOUT_S,
        # Grading is offline on purpose: a test suite that reaches the network
        # is measuring something other than the patch.
        sandbox=SandboxConfig(network=False),
        retries=_EVAL_RETRIES,
    ),
)

SOLVE_AND_GRADE: WorkflowDef = (*SOLVE, *GRADE)

register_workflow("solve", SOLVE)
register_workflow("grade", GRADE)
register_workflow("solve_and_grade", SOLVE_AND_GRADE)
