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

import functools

from swe_lab.evaluation.unit_test import gold_patch, UnitTestTask
from swe_lab.git.audit import GitIntegrityAuditTask
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    ANTHROPIC_API,
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
from swe_lab.sandbox import ArtifactSchema, DockerHostSandboxConfig
from swe_lab.trace_synthesis.channel import supervision
from swe_lab.trace_synthesis.guidebook import GUIDEBOOK_NAME
from swe_lab.trace_synthesis.judge import (
    messages_transport,
    supervising_policy,
)
from swe_lab.trace_synthesis.native_supervision import (
    API_KEY_ENV as SUPERVISOR_API_KEY_ENV,
)
from swe_lab.trace_synthesis.native_supervision import (
    Blocking,
    NativeSupervision,
)
from swe_lab.trace_synthesis.oracle import OracleAnalysisTask
from swe_lab.trace_synthesis.segmented_loop import SegmentedSupervision

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
# The two prior supervision measurements — the steered re-run and the
# guidebook-as-criterion experiment — used this model through OpenRouter. The
# model stays pinned for continuity of the model choice, while this transport
# now uses Anthropic's native Messages wire and therefore is not the same
# measurement condition.
SUPERVISOR_MODEL = "claude-sonnet-5"
SUPERVISOR_BASE_URL = ANTHROPIC_API
SUPERVISOR_TRANSPORT = functools.partial(
    messages_transport,
    base_url=SUPERVISOR_BASE_URL,
    api_key_env=SUPERVISOR_API_KEY_ENV,
)
# How many corrections one run may carry. No measured value — task 05 owns that
# question — so it is stated rather than derived, and stated once.
SUPERVISOR_BUDGET = 3
# The control arm's budget. Zero rather than a silent policy, because
# `SpeakWhenOffTrack` gates *speech* on the budget and never gates judgement:
# it consults the judge on every boundary carrying evidence and records what it
# would have said before the budget is looked at. (A boundary whose evidence
# window is empty is judged in neither arm — that skip reads the evidence
# window alone, so two arms fed one stream skip the same boundaries and the
# matching below is untouched.) So the arms are matched on the *judging*
# side — same calls, same waits, same cost per boundary — and differ on the
# writing side, where a call is what a delivered correction is: the treatment
# pays for the ones it makes and the control for none. That difference is the
# treatment itself. A policy that returned early instead, consulting no judge
# at all, would move the per-boundary calls too, and a paired comparison would
# credit that to the corrections. **This is the one statement of how the arms
# differ**; the other sites point here rather than repeating it, because a
# repeated claim is one that goes stale in four places without failing in any.
CONTROL_BUDGET = 0
# Boundaries required between two interventions, and how many of the actor's
# records the judge sees. Named here rather than left to `supervision()`'s
# signature defaults because the native runtime needs the same two numbers and
# takes them as required arguments: a value with two homes is a value that
# drifts in one of them without failing anywhere.
SUPERVISOR_COOLDOWN = 4
SUPERVISOR_WINDOW = 8


def _supervised_rollout(supervision_factory: SupervisionFactory) -> WorkflowDef:
  """Build a rollout entry whose actor can be spoken to while it runs.

  The treatment arm and its control are given the same harness, the same flags
  and the same invocation script, so nothing about the actor's environment
  distinguishes them; how their supervision sides differ is stated once, at
  :data:`CONTROL_BUDGET`. That is why this is a function of the supervision
  rather than a flag on :data:`ROLLOUT`: a boolean would hide the difference
  between the arms inside a parameter instead of leaving it in two readable
  definitions.

  Args:
    supervision_factory: What watches the actor, given the task text.

  Returns:
    The one-entry definition.
  """
  return (
      WorkflowEntry(
          ROLLOUT_KEY,
          CodingAgentTask(
              # Proxy capture is a choice about evidence here, not something
              # the channel requires (ADR-0017): a run from these two
              # definitions is read as evidence *about* supervision, and the
              # wire is the only record of the request bodies it produced.
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
        transport=SUPERVISOR_TRANSPORT,
        budget=SUPERVISOR_BUDGET,
        cooldown=SUPERVISOR_COOLDOWN,
        window=SUPERVISOR_WINDOW,
    )
)

# The same policy, the same criterion, the same judge on every boundary either
# arm judges at all — with nothing left to spend. What the actor experiences
# differs by the corrections alone, and the supervision side differs only past
# the point where a correction was decided on, which is the whole of what a
# paired arm is for.
CONTROL_ROLLOUT: WorkflowDef = _supervised_rollout(
    supervision(
        model=SUPERVISOR_MODEL,
        transport=SUPERVISOR_TRANSPORT,
        budget=CONTROL_BUDGET,
        cooldown=SUPERVISOR_COOLDOWN,
        window=SUPERVISOR_WINDOW,
    )
)

# Zero rather than `SUPERVISOR_COOLDOWN`, and stated so the inertness is
# deliberate: cooldown is measured in consumed stream events, and consecutive
# seams are many events apart, so the gate never closes on this path. Spacing
# here is `SegmentedSupervision.turns_per_segment`.
SEGMENT_COOLDOWN = 0


# The second supervision carrier: the actor is stopped every configured number
# of turns, judged, and resumed, instead of being spoken to on a live stdin. Its
# own definition rather than a flag on the two above, for the same reason the
# native runtime has one: it takes no `supervision_factory` (the policy travels
# on the harness, since the loop drives `run()` rather than bracketing it) and
# it cannot use the correction channel, which owns the actor's stdin.
#
# `capture="stream"`, which is also what makes the run readable: with
# `--replay-user-messages` the event stream echoes the messages the actor
# received, so an injected correction is visible in the trace beside what the
# actor did next.
def _segmented_rollout(*, guidebook_name: str | None = None) -> WorkflowDef:
  """Build the segmented rollout, optionally with a guidebook input.

  Args:
    guidebook_name: The phase-B artifact to give the supervisor, or ``None``.

  Returns:
    The one-entry segmented rollout definition.
  """
  return (
      WorkflowEntry(
          ROLLOUT_KEY,
          CodingAgentTask(
              harness=ClaudeCodeHarness(
                  model=DEFAULT_MODEL,
                  bare=False,
                  capture="stream",
                  segmented=SegmentedSupervision(
                      policy_factory=lambda: supervising_policy(
                          model=SUPERVISOR_MODEL,
                          transport=SUPERVISOR_TRANSPORT,
                          budget=SUPERVISOR_BUDGET,
                          cooldown=SEGMENT_COOLDOWN,
                          window=SUPERVISOR_WINDOW,
                          # The one thing only a live run can record: how many
                          # turns late each correction was.
                          locate_deviation=True,
                      ),
                      guidebook_name=guidebook_name,
                  ),
              ),
              extra_inputs=(
                  (
                      ArtifactSchema(
                          guidebook_name,
                          description="the Oracle's phase-B guidebook",
                      ),
                  )
                  if guidebook_name is not None
                  else ()
              ),
          ),
          timeout=_AGENT_TIMEOUT_S,
          sandbox=DockerHostSandboxConfig(
              network=True, pass_env=(OAUTH_TOKEN_ENV,)
          ),
      ),
  )


SEGMENTED_ROLLOUT: WorkflowDef = _segmented_rollout()
_GUIDEBOOK_SEGMENTED_ROLLOUT: WorkflowDef = _segmented_rollout(
    guidebook_name=GUIDEBOOK_NAME
)

SEGMENTED_ROLLOUT_AND_UNIT_TEST: WorkflowDef = (
    *SEGMENTED_ROLLOUT,
    *UNIT_TEST,
)


# How many of the actor's assistant messages pass between judgements on the
# native runtime. One — the setting that judges the most — because whether
# batching them is worth anything is the open question #382 is measuring, and a
# shipped definition is the wrong place to quietly answer it.
NATIVE_JUDGE_EVERY_N = 1


# The wrapper watches the actor from inside the sandbox instead of from the
# host. **Its own definition rather than a flag on the two above**, because it
# is not those arms with a different supervisor: it cannot use the correction
# channel (the wrapper owns the actor's stdin, and so does the channel's FIFO),
# it takes no `supervision_factory`, and its sandbox must carry a second
# credential. A boolean on `_supervised_rollout` would have to switch all three
# and would read as a smaller difference than it is.
#
# The knob values are the ones the prior supervision measurements used, so the
# first native runs are read against calls of the same shape rather than a new
# unknown. That is the same reasoning as `SUPERVISOR_MODEL` and **not** a claim
# that the two runtimes agree: they deliberately diverge (#380, #381, #383).
NATIVE_SUPERVISED_ROLLOUT: WorkflowDef = (
    WorkflowEntry(
        ROLLOUT_KEY,
        CodingAgentTask(
            harness=ClaudeCodeHarness(
                model=DEFAULT_MODEL,
                bare=False,
                # Proxy capture for the same reason as the arms above: the wire
                # is the only record of the request bodies a run produced. The
                # supervisor's own calls go through a second instance of it.
                capture="proxy",
                native_supervision=NativeSupervision(
                    model=SUPERVISOR_MODEL,
                    budget=SUPERVISOR_BUDGET,
                    cooldown=SUPERVISOR_COOLDOWN,
                    window=SUPERVISOR_WINDOW,
                    judge_every_n_assistant_messages=NATIVE_JUDGE_EVERY_N,
                    # Stop reading the actor's stdout while a judgement is in
                    # flight: the pipe fills and the actor waits. The absence
                    # of a read, so it self-releases if the wrapper dies —
                    # unlike SIGSTOP, which leaves a state someone must undo.
                    block_actor_while_judging=Blocking.STDOUT,
                ),
            ),
        ),
        timeout=_AGENT_TIMEOUT_S,
        sandbox=DockerHostSandboxConfig(
            # Two credentials now, both by name: the actor's and the
            # supervisor's. The supervisor's endpoint is *not* here — the
            # harness exports it, because it addresses a forwarder the harness
            # starts inside this sandbox and a host variable of that name could
            # otherwise aim a credential-bearing request anywhere.
            network=True,
            pass_env=(OAUTH_TOKEN_ENV, SUPERVISOR_API_KEY_ENV),
        ),
    ),
)

NATIVE_SUPERVISED_ROLLOUT_AND_UNIT_TEST: WorkflowDef = (
    *NATIVE_SUPERVISED_ROLLOUT,
    *UNIT_TEST,
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

ORACLE_GUIDED_TRACE: WorkflowDef = (
    *ORACLE_ANALYSIS,
    *_GUIDEBOOK_SEGMENTED_ROLLOUT,
    *UNIT_TEST,
)

register_workflow("git_integrity_audit", GIT_INTEGRITY_AUDIT)
register_workflow("oracle_analysis", ORACLE_ANALYSIS)
register_workflow("oracle_guided_trace", ORACLE_GUIDED_TRACE)
register_workflow("rollout", ROLLOUT)
register_workflow("unit_test", UNIT_TEST)
register_workflow("rollout_and_unit_test", ROLLOUT_AND_UNIT_TEST)
register_workflow("gold_unit_test", GOLD_UNIT_TEST)
register_workflow("segmented_rollout", SEGMENTED_ROLLOUT)
register_workflow(
    "segmented_rollout_and_unit_test", SEGMENTED_ROLLOUT_AND_UNIT_TEST
)
register_workflow(
    "supervised_rollout_and_unit_test", SUPERVISED_ROLLOUT_AND_UNIT_TEST
)
register_workflow(
    "control_rollout_and_unit_test", CONTROL_ROLLOUT_AND_UNIT_TEST
)
register_workflow(
    "native_supervised_rollout_and_unit_test",
    NATIVE_SUPERVISED_ROLLOUT_AND_UNIT_TEST,
)
