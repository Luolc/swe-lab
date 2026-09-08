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

from swe_lab.conversation.observer import CONVERSATION_NAME
from swe_lab.evaluation.unit_test import (
    ARTIFACT_NAMESPACE,
    gold_patch,
    UnitTestTask,
    VERDICT_NAME,
)
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
from swe_lab.sandbox import (
    ArtifactSchema,
    DockerHostSandboxConfig,
    qualified_name,
)
from swe_lab.sandbox.observers import BASE_REF_NAME, PATCH_NAME
from swe_lab.trace_synthesis.channel import supervision
from swe_lab.trace_synthesis.guidebook import GUIDEBOOK_NAME
from swe_lab.trace_synthesis.judge import (
    DEFAULT_API_KEY_ENV,
    default_supervisor_base_url,
    messages_transport,
    supervising_policy,
)
from swe_lab.trace_synthesis.oracle import OracleAnalysisTask
from swe_lab.trace_synthesis.segmented_loop import SegmentedSupervision
from swe_lab.trace_synthesis.supervisor import SaidVisibility, SpeakPolicy

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
# The from-scratch chain runs the solve + grade pair twice in one workflow, and
# the key is what keeps the two apart: it is the task segment of every store
# key, so the same `patch.diff` written by two rollouts lands under two
# prefixes without any second naming scheme, and the workflow layer refuses a
# repeated key outright (ADR-0023). Read the phase off the key.
BASELINE_ROLLOUT_KEY = "baseline_rollout"
BASELINE_UNIT_TEST_KEY = "baseline_unit_test"
GUIDED_ROLLOUT_KEY = "guided_rollout"
GUIDED_UNIT_TEST_KEY = "guided_unit_test"

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


def _rollout_entry(key: str = ROLLOUT_KEY) -> WorkflowEntry:
  """Build the plain, unsupervised rollout entry under ``key``.

  Args:
    key: The entry key — the task segment of every record the entry persists.

  Returns:
    The rollout entry.
  """
  return WorkflowEntry(
      key,
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
  )


def _unit_test_entry(
    key: str = UNIT_TEST_KEY, *, inputs: tuple[str, ...] = ()
) -> WorkflowEntry:
  """Build the grading entry under ``key``, optionally bound to a producer.

  Args:
    key: The entry key.
    inputs: Explicit edge bindings (``"<producer key>/<input name>"``) —
      needed where two earlier entries produce the same name.

  Returns:
    The grading entry.
  """
  return WorkflowEntry(
      key,
      # The task supplies **no** input of its own (`inputs_builder=None`),
      # which is what lets this one entry serve both modes: run alone, its
      # patch is the caller's (`execute(inputs=…)`); spliced into a chain,
      # the same entry takes the agent's by edge.
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
      inputs=inputs,
  )


ROLLOUT: WorkflowDef = (_rollout_entry(),)

UNIT_TEST: WorkflowDef = (_unit_test_entry(),)

ROLLOUT_AND_UNIT_TEST: WorkflowDef = (*ROLLOUT, *UNIT_TEST)

# The model both supervisor calls go to. Named here, and never defaulted, so
# that every record says who was asked: a rate compared across batches is only
# comparable if the judge is pinned, exactly as the actor is (`agent_model`).
# The two prior supervision measurements — the steered re-run and the
# guidebook-as-criterion experiment — used this model through OpenRouter. The
# model stays pinned for continuity of the model choice, while the default
# transport goes wherever the environment points it and otherwise to
# Anthropic's native Messages wire — either way not the same measurement
# condition.
#
# **One name, whatever the upstream.** OpenRouter's Messages endpoint takes
# this bare name and namespaces it itself, so pointing a run there is a change
# of endpoint and nothing else — measured 2026-09-07, evidence in
# `docs/conventions.md` (Secrets).
SUPERVISOR_MODEL = "claude-sonnet-5"
# Where the supervisor's calls go, and which variable holds the key. Two
# strings the caller owns: `ANTHROPIC_BASE_URL` when the environment sets it,
# the Anthropic root otherwise. **The default is not what a paid experiment
# spends** — the rule is in `AGENTS.md` (Boundaries) and an invocation honours
# it by naming the pool's endpoint and a variable holding one live key, both of
# which reach a command line through `--<entry>.harness.segmented.…`.
# Captured **here, as this module imports** — so is the identical default on
# the shipped segmented plan below. A variable set after that does not reach
# either; the contract and its reasoning are at `default_supervisor_base_url`.
SUPERVISOR_BASE_URL = default_supervisor_base_url()
SUPERVISOR_TRANSPORT = functools.partial(
    messages_transport,
    base_url=SUPERVISOR_BASE_URL,
    api_key_env=DEFAULT_API_KEY_ENV,
)
# How many corrections one run may carry. No measured value — task 05 owns that
# question — so it is stated rather than derived, and stated once.
SUPERVISOR_BUDGET = 3
# Who is shown what the supervisor has already said: the writer, and not the
# judge (ADR-0024). Named here and never read from the environment, because an
# arm whose setting the record cannot show is not an arm: a definition wanting
# another value states it in its own `supervision(...)` call, the way
# `CONTROL_ROLLOUT` states its budget, and every decision row records which
# one it ran under.
SUPERVISOR_SAID_VISIBILITY: SaidVisibility = "writer"
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
# signature defaults, so that both carriers below read one value from one home:
# a value with two homes is a value that drifts in one of them without failing
# anywhere.
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
        said_visibility=SUPERVISOR_SAID_VISIBILITY,
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
        said_visibility=SUPERVISOR_SAID_VISIBILITY,
    )
)


def _segmented_policy(
    cooldown: int, base_url: str, api_key_env: str
) -> SpeakPolicy:
  """Build the segmented loop's policy for one run, against one upstream.

  A named function rather than the lambda this used to be: the upstream is two
  further per-run arguments, and a three-argument lambda spanning a dozen lines
  inside a nested constructor is where a reader stops being able to see which
  values are per-run and which are the pinned ones. The model is one of the
  pinned ones and stays so wherever the run is pointed — see
  :data:`SUPERVISOR_MODEL`.

  Args:
    cooldown: Boundaries required between two interventions, from the run's
      :class:`~swe_lab.trace_synthesis.segmented_loop.SegmentedSupervision`.
    base_url: Where this invocation's supervisor calls go.
    api_key_env: The name of the variable holding this invocation's key.

  Returns:
    The policy for this run.
  """
  return supervising_policy(
      model=SUPERVISOR_MODEL,
      transport=functools.partial(
          messages_transport, base_url=base_url, api_key_env=api_key_env
      ),
      budget=SUPERVISOR_BUDGET,
      cooldown=cooldown,
      window=SUPERVISOR_WINDOW,
      said_visibility=SUPERVISOR_SAID_VISIBILITY,
      # The one thing only a live run can record: how many turns late each
      # correction was.
      locate_deviation=True,
  )


# The supervised carrier of record (ADR-0025): the actor is stopped every
# configured number of turns, judged, and resumed, instead of being spoken to on
# a live stdin. Its own definition rather than a flag on the two above, because
# it takes no `supervision_factory` (the policy travels on the harness, since
# the loop drives `run()` rather than bracketing it) and it cannot use the
# correction channel, which owns the actor's stdin.
#
# `capture="stream"`, which is also what makes the run readable: with
# `--replay-user-messages` the event stream echoes the messages the actor
# received, so an injected correction is visible in the trace beside what the
# actor did next.
def _segmented_rollout(
    *, guidebook_name: str | None = None, key: str = ROLLOUT_KEY
) -> WorkflowDef:
  """Build the segmented rollout, optionally with a guidebook input.

  Args:
    guidebook_name: The phase-B artifact to give the supervisor, or ``None``.
    key: The entry key; a chain that also runs an unguided rollout gives this
      one its own.

  Returns:
    The one-entry segmented rollout definition.
  """
  return (
      WorkflowEntry(
          key,
          CodingAgentTask(
              harness=ClaudeCodeHarness(
                  model=DEFAULT_MODEL,
                  bare=False,
                  capture="stream",
                  segmented=SegmentedSupervision(
                      policy_factory=_segmented_policy,
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


def _oracle_analysis_entry(
    *, failure_inputs: bool = False, inputs: tuple[str, ...] = ()
) -> WorkflowEntry:
  """Build phase B's entry, reading the failure from where ``inputs`` says.

  Args:
    failure_inputs: Whether the failure arrives as declared inputs (a chain
      that ran phase A first) rather than as the instance's own mounts.
    inputs: Explicit edge bindings for those inputs.

  Returns:
    The Oracle entry. The agent is the same shipped harness, under the same
    authentication, as the rollout's.
  """
  return WorkflowEntry(
      ORACLE_ANALYSIS_KEY,
      OracleAnalysisTask(
          harness=ClaudeCodeHarness(model=DEFAULT_MODEL, bare=False),
          failure_inputs=failure_inputs,
      ),
      timeout=_ORACLE_ANALYSIS_TIMEOUT_S,
      sandbox=DockerHostSandboxConfig(
          network=True, pass_env=(OAUTH_TOKEN_ENV,)
      ),
      inputs=inputs,
  )


# Phase B of trace synthesis, on its own: the instance is an `oracle_failures`
# record, which brings the failed conversation, verdict and patch along as its
# own mounts, so this one entry runs from a name alone — `run oracle_analysis
# <id> --dataset oracle_failures`.
ORACLE_ANALYSIS: WorkflowDef = (_oracle_analysis_entry(),)

ORACLE_GUIDED_TRACE: WorkflowDef = (
    *ORACLE_ANALYSIS,
    *_GUIDEBOOK_SEGMENTED_ROLLOUT,
    *UNIT_TEST,
)


def _edge(producer: str, name: str) -> str:
  """Spell one explicit binding, ``"<producer key>/<input name>"``."""
  return f"{producer}/{name}"


# The whole pipeline from nothing, over a plain instance (ADR-0023): solve and
# grade blind, let the Oracle explain that attempt, solve again under the
# guidebook's supervision, grade again. **Unconditional** — an instance the
# blind rollout already solves still gets its guidebook and its guided rollout,
# because the reading this chain exists for is a 2×2 over the two verdicts, and
# a chain that stopped early could never fill the cell where the guidebook made
# things worse. `guided_gain` is that reading.
#
# Every binding is written out, including the ones name matching would have
# resolved alone, so the graph can be read off the definition. Two of them the
# engine *demands*: by the time the guided grading entry binds, two earlier
# entries produce `patch.diff` and `patch.base_ref.txt`, and an unbound name
# with two producers is refused rather than resolved nearest-wins.
FROM_SCRATCH_GUIDED_TRACE: WorkflowDef = (
    _rollout_entry(BASELINE_ROLLOUT_KEY),
    _unit_test_entry(
        BASELINE_UNIT_TEST_KEY,
        inputs=(
            _edge(BASELINE_ROLLOUT_KEY, PATCH_NAME),
            _edge(BASELINE_ROLLOUT_KEY, BASE_REF_NAME),
        ),
    ),
    _oracle_analysis_entry(
        failure_inputs=True,
        inputs=(
            _edge(BASELINE_ROLLOUT_KEY, CONVERSATION_NAME),
            _edge(BASELINE_ROLLOUT_KEY, PATCH_NAME),
            _edge(BASELINE_ROLLOUT_KEY, BASE_REF_NAME),
            _edge(
                BASELINE_UNIT_TEST_KEY,
                qualified_name(ARTIFACT_NAMESPACE, VERDICT_NAME),
            ),
        ),
    ),
    *_segmented_rollout(guidebook_name=GUIDEBOOK_NAME, key=GUIDED_ROLLOUT_KEY),
    _unit_test_entry(
        GUIDED_UNIT_TEST_KEY,
        inputs=(
            _edge(GUIDED_ROLLOUT_KEY, PATCH_NAME),
            _edge(GUIDED_ROLLOUT_KEY, BASE_REF_NAME),
        ),
    ),
)

register_workflow("git_integrity_audit", GIT_INTEGRITY_AUDIT)
register_workflow("oracle_analysis", ORACLE_ANALYSIS)
register_workflow("oracle_guided_trace", ORACLE_GUIDED_TRACE)
register_workflow("from_scratch_guided_trace", FROM_SCRATCH_GUIDED_TRACE)
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
