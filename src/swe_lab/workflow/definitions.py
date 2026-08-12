"""The shipped workflow definitions: rollout, unit_test, and the two chained.

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

from swe_lab.evaluation.unit_test import gold_patch, UnitTestTask
from swe_lab.git.audit import GitIntegrityAuditTask
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    DEFAULT_MODEL,
    OAUTH_TOKEN_ENV,
)

# Imported for its registration alone. A harness registers itself at import of
# its own package, and `claude_code` gets imported above because a shipped
# definition uses it — `codex` has no shipped definition yet, so without this
# line `--rollout.harness=codex` fails as "unknown harness", which reads as
# "not implemented" rather than "not the default". Selecting an agent by name
# must not depend on whether some definition happens to mention it.
import swe_lab.harnesses.codex as _codex
import swe_lab.harnesses.grok as _grok
from swe_lab.rollout import CodingAgentTask
from swe_lab.sandbox import DockerHostSandboxConfig

from .registry import register_workflow, WorkflowDef
from .workflow import WorkflowEntry

assert _codex.CodexHarness  # the imports above are for their side effects
assert _grok.GrokHarness

# The entry keys, which are also the task segment of every record a run of
# these workflows persists (ADR-0007 §6). Stable: resume trusts them.
ROLLOUT_KEY = "rollout"
UNIT_TEST_KEY = "unit_test"
GIT_INTEGRITY_KEY = "git_integrity"

_AGENT_TIMEOUT_S = 1800.0
_UNIT_TEST_TIMEOUT_S = 1800.0
# Two extra grading attempts absorb a flaky suite without hiding a real
# failure: the patch is identical on every attempt (ADR-0008).
_UNIT_TEST_RETRIES = 2
# Bounded by `git gc` on the largest repos (~51s observed under emulation),
# plus the image pull. No agent runs, so this needs no agent budget.
_GIT_INTEGRITY_TIMEOUT_S = 900.0

ROLLOUT: WorkflowDef = (
    WorkflowEntry(
        ROLLOUT_KEY,
        CodingAgentTask(
            # bare=False explicitly: bare mode reads neither OAuth nor the
            # keychain (verified on 2.1.220 — a bare run with a valid
            # CLAUDE_CODE_OAUTH_TOKEN still fails "Not logged in"), and this
            # definition authenticates by that token. A composition using
            # ANTHROPIC_API_KEY should leave the default alone.
            harness=ClaudeCodeHarness(model=DEFAULT_MODEL, bare=False)
        ),
        timeout=_AGENT_TIMEOUT_S,
        # The agent needs the network, and its credential travels by name so
        # the value never reaches a command line.
        sandbox=DockerHostSandboxConfig(
            network=True, pass_env=(OAUTH_TOKEN_ENV,)
        ),
    ),
)

UNIT_TEST: WorkflowDef = (
    WorkflowEntry(
        UNIT_TEST_KEY,
        # The task supplies **no** input of its own (`inputs_builder=None`),
        # which is what lets this one entry serve both modes: run alone, its
        # patch is the caller's (`execute(inputs=…)`); spliced into the chain
        # below, the same entry takes the agent's by edge.
        #
        # Grading the *gold* patch is therefore a different definition, not a
        # flag on this one: it needs `inputs_builder=gold_patch`, and a task
        # that builds its own patch cannot also be handed one — the collision
        # is refused on purpose. It lands with the command that invokes it.
        UnitTestTask(),
        timeout=_UNIT_TEST_TIMEOUT_S,
        # Online, like every other entry: real suites fetch things, and a
        # backend that cannot cut the network (the GH job is already running
        # when we get it) could not honor an offline declaration anyway.
        sandbox=DockerHostSandboxConfig(network=True),
        retries=_UNIT_TEST_RETRIES,
    ),
)

ROLLOUT_AND_UNIT_TEST: WorkflowDef = (*ROLLOUT, *UNIT_TEST)

GOLD_UNIT_TEST: WorkflowDef = (
    WorkflowEntry(
        UNIT_TEST_KEY,
        # The dataset's own reference solution, built from the instance — so
        # this one runs from a name alone. It is a *separate* definition and
        # not a flag on ``UNIT_TEST`` precisely because a task that builds its
        # own patch cannot also be handed one: the two suppliers collide, on
        # purpose, and the collision is the reason there are two names.
        UnitTestTask(inputs_builder=gold_patch),
        timeout=_UNIT_TEST_TIMEOUT_S,
        sandbox=DockerHostSandboxConfig(network=True),
        retries=_UNIT_TEST_RETRIES,
    ),
)

GIT_INTEGRITY_AUDIT: WorkflowDef = (
    WorkflowEntry(
        GIT_INTEGRITY_KEY,
        GitIntegrityAuditTask(),
        timeout=_GIT_INTEGRITY_TIMEOUT_S,
        # Offline on purpose. Nothing here needs egress, and running the audit
        # exactly as constrained as the rollout should be keeps it honest.
        sandbox=DockerHostSandboxConfig(network=False),
    ),
)

register_workflow("git_integrity_audit", GIT_INTEGRITY_AUDIT)
register_workflow("rollout", ROLLOUT)
register_workflow("unit_test", UNIT_TEST)
register_workflow("rollout_and_unit_test", ROLLOUT_AND_UNIT_TEST)
register_workflow("gold_unit_test", GOLD_UNIT_TEST)
