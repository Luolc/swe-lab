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
import swe_lab.harnesses.grok_build as _grok
from swe_lab.rollout import CodingAgentTask, SupervisionFactory
from swe_lab.sandbox import DockerHostSandboxConfig
from swe_lab.trace_synthesis.channel import supervision
from swe_lab.trace_synthesis.judge import openrouter_transport
from swe_lab.trace_synthesis.oracle import OracleAnalysisTask

from .registry import register_workflow, WorkflowDef
from .workflow import WorkflowEntry

assert _codex.CodexHarness  # the imports above are for their side effects
assert _grok.GrokBuildHarness

# The entry keys, which are also the task segment of every record a run of
# these workflows persists (ADR-0007 §6). Stable: resume trusts them.
ROLLOUT_KEY = "rollout"
UNIT_TEST_KEY = "unit_test"
GIT_INTEGRITY_KEY = "git_integrity"
ORACLE_ANALYSIS_KEY = "oracle_analysis"

# One hour for the agent, half an hour for the suite. The agent's number is set
# against a **p90 rollout wall clock of about one hour** measured by the owner
# elsewhere, not in this repo (2026-09-01): at the previous 1800 s a real slice
# of healthy runs was killed mid-solve, and a killed run reports as an
# infrastructure failure rather than as the reasoning failure it was on its way
# to being — the one confusion this pipeline can least afford. The grading suite
# is not an agent and has never approached its budget, so it keeps 1800 s.
#
# Note for whoever changes these: the timeout is **per attempt**, not a budget
# shared across retries (`run_task.run_task` passes the same value into every
# iteration of `range(retries + 1)`).
_AGENT_TIMEOUT_S = 3600.0
_UNIT_TEST_TIMEOUT_S = 1800.0
# Two extra grading attempts absorb a flaky suite without hiding a real
# failure: the patch is identical on every attempt (ADR-0008).
_UNIT_TEST_RETRIES = 2
# Bounded by `git gc` on the largest repos (~51s observed under emulation),
# plus the image pull. No agent runs, so this needs no agent budget.
_GIT_INTEGRITY_TIMEOUT_S = 900.0
# An agent run like the rollout's, over a smaller job: read a failure, write
# a document. The one live run so far finished in about five minutes.
_ORACLE_ANALYSIS_TIMEOUT_S = 1800.0

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

# The model both supervisor calls go to. Named here, and never defaulted, so
# that every record says who was asked: a rate compared across batches is only
# comparable if the judge is pinned, exactly as the actor is (`agent_model`).
# This is the model the two prior supervision measurements used — the steered
# re-run and the guidebook-as-criterion experiment — so the first supervised
# rollouts are read against calls of the same shape rather than against a new
# unknown.
SUPERVISOR_MODEL = "anthropic/claude-sonnet-5"
# How many corrections one run may carry. No measured value — task 05 owns that
# question — so it is stated rather than derived, and stated once.
SUPERVISOR_BUDGET = 3
# The control arm's budget. Zero rather than a silent policy, because
# `SpeakWhenOffTrack` gates *speech* on the budget and never gates judgement:
# it consults the judge on every boundary and records what it would have said
# before the budget is looked at. So the arms are matched on the *judging*
# side — same calls, same waits, same cost per boundary — and differ on the
# writing side, where a call is what a delivered correction is: the treatment
# pays for the ones it makes and the control for none. That difference is the
# treatment itself. A policy that returned early instead, consulting no judge
# at all, would move the per-boundary calls too, and a paired comparison would
# credit that to the corrections.
CONTROL_BUDGET = 0


def _supervised_rollout(supervision_factory: SupervisionFactory) -> WorkflowDef:
  """Build a rollout entry whose actor can be spoken to while it runs.

  The treatment arm and its control differ **only** in the policy they are
  given: same harness, same flags, same invocation script, so what separates
  the two runs is what was said and nothing else. That is why this is a
  function of the supervision rather than a flag on :data:`ROLLOUT` — a boolean
  would hide the difference between the arms inside a parameter instead of
  leaving it in two readable definitions.

  Args:
    supervision_factory: What watches the actor, given the task text.

  Returns:
    The one-entry definition.
  """
  return (
      WorkflowEntry(
          ROLLOUT_KEY,
          CodingAgentTask(
              # Proxy capture is required by the channel and refused otherwise:
              # a stream-derived trace of a supervised run asserts a turn the
              # model never saw.
              harness=ClaudeCodeHarness(
                  model=DEFAULT_MODEL,
                  bare=False,
                  capture="proxy",
                  correction_channel=True,
              ),
              supervision_factory=supervision_factory,
          ),
          timeout=_AGENT_TIMEOUT_S,
          sandbox=DockerHostSandboxConfig(
              network=True, pass_env=(OAUTH_TOKEN_ENV,)
          ),
      ),
  )


SUPERVISED_ROLLOUT: WorkflowDef = _supervised_rollout(
    supervision(
        model=SUPERVISOR_MODEL,
        transport=openrouter_transport,
        budget=SUPERVISOR_BUDGET,
    )
)

# The same policy, the same criterion, the same judge on every boundary — with
# nothing left to spend. What the actor experiences differs by the corrections
# alone, which is the whole of what a paired arm is for.
CONTROL_ROLLOUT: WorkflowDef = _supervised_rollout(
    supervision(
        model=SUPERVISOR_MODEL,
        transport=openrouter_transport,
        budget=CONTROL_BUDGET,
    )
)

SUPERVISED_ROLLOUT_AND_UNIT_TEST: WorkflowDef = (
    *SUPERVISED_ROLLOUT,
    *UNIT_TEST,
)

CONTROL_ROLLOUT_AND_UNIT_TEST: WorkflowDef = (*CONTROL_ROLLOUT, *UNIT_TEST)

GOLD_UNIT_TEST: WorkflowDef = (
    WorkflowEntry(
        UNIT_TEST_KEY,
        # The dataset's own reference solution, built from the instance — so
        # this one runs from a name alone. It is a *separate* definition and
        # not a flag on ``UNIT_TEST`` precisely because a task that builds its
        # own patch cannot also be handed one: the two suppliers collide, on
        # purpose, and the collision is the reason there are two names.
        # `patch_baseline=False` against the default (ADR-0014): the
        # dataset's gold patch is authored against `base_commit`, so
        # `base_commit` is its base — there is no pre-agent tree here,
        # and no recorded base ref for a verify to compare against.
        UnitTestTask(inputs_builder=gold_patch, patch_baseline=False),
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

ORACLE_ANALYSIS: WorkflowDef = (
    WorkflowEntry(
        ORACLE_ANALYSIS_KEY,
        # Phase B of trace synthesis, on its own: the instance is an
        # `oracle_failures` record, which brings the failed conversation,
        # verdict and patch along as its own mounts, so this one entry runs
        # from a name alone — `run oracle_analysis <id> --dataset
        # oracle_failures`. The agent is the same shipped harness, under the
        # same authentication, as the rollout's.
        OracleAnalysisTask(
            harness=ClaudeCodeHarness(model=DEFAULT_MODEL, bare=False)
        ),
        timeout=_ORACLE_ANALYSIS_TIMEOUT_S,
        sandbox=DockerHostSandboxConfig(
            network=True, pass_env=(OAUTH_TOKEN_ENV,)
        ),
    ),
)

register_workflow("git_integrity_audit", GIT_INTEGRITY_AUDIT)
register_workflow("oracle_analysis", ORACLE_ANALYSIS)
register_workflow("rollout", ROLLOUT)
register_workflow("unit_test", UNIT_TEST)
register_workflow("rollout_and_unit_test", ROLLOUT_AND_UNIT_TEST)
register_workflow("gold_unit_test", GOLD_UNIT_TEST)
register_workflow(
    "supervised_rollout_and_unit_test", SUPERVISED_ROLLOUT_AND_UNIT_TEST
)
register_workflow(
    "control_rollout_and_unit_test", CONTROL_ROLLOUT_AND_UNIT_TEST
)
