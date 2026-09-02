"""Tests for CodingAgentTask: the composition on an injected fake sandbox.

``Task.execute`` takes the sandbox by **injection**, so a test just constructs
a :class:`FakeSandbox` (real local-dir file ops, scripted exec, no Docker) and
passes it — no backend registry, no patching a construction function. The whole
composition (manager → observers → harness) runs docker-free while no agent
process ever spawns.
"""

import dataclasses
from pathlib import Path
from typing import override

from etils import epath

from swe_lab.conversation import Conversation
from swe_lab.conversation.observer import CONVERSATION_NAME
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.harnesses import AgentOutcome, HarnessOutcomeObserver
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.rollout import (
    CodingAgentTask,
    conversation_of,
    OOM_METRIC,
    outcome_of,
    patch_of,
    PROMPT_NAME,
    rollout_outcome,
    RolloutOutcome,
)
from swe_lab.sandbox import (
    ArtifactSchema,
    Mount,
    RunResult,
    RunStatus,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.observers.diff_extract import DiffExtractObserver
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.workflow import AttemptResult

_SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "base")


@dataclasses.dataclass(frozen=True)
class _Instance(TaskInstance[Verdict]):
  """The instance the task binds: a run context and a task statement."""

  instance_id: str = "acme__widget-1"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return _SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return None

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[Verdict]:
    raise NotImplementedError("this instance is only solved, never graded")


@dataclasses.dataclass
class _LocalFakeSandbox(FakeSandbox):
  """A ``FakeSandbox`` that records mount targets and keeps them local.

  Mount targets are recorded so a test can tell a *mount* from a ``write``,
  and an absolute target is redirected under the real workspace dir (writing
  to e.g. ``/opt`` on the host needs root). Exec stays scripted, so the agent
  never actually runs.
  """

  mount_targets: list[str] = dataclasses.field(default_factory=list)

  @override
  def _mount_one(self, target: str, mount: Mount) -> None:
    self.mount_targets.append(target)
    super()._mount_one(target, mount)

  @override
  def _dest(self, target: str) -> epath.Path:
    return epath.Path(self.workspace / target.lstrip("/"))


def test_the_task_wires_and_assembles(tmp_path: Path):
  workspace = tmp_path / "ws"
  sandbox = _LocalFakeSandbox(spec=_SPEC, workspace=epath.Path(workspace))

  result = CodingAgentTask(harness=ClaudeCodeHarness(model="sonnet")).execute(
      sandbox,
      _Instance(),
      output_dir=workspace,
      timeout=60.0,
  )

  # the run wired up and assembled — no agent ran, so the patch/trace are empty
  assert result.run.status is RunStatus.SUCCESS
  extract = patch_of(result)
  assert extract is not None and extract.is_empty is True
  assert extract.patch == ""
  outcome = outcome_of(result)
  assert outcome is not None and outcome.complete is False
  trace = conversation_of(result)
  assert trace is not None and trace.conversation == Conversation(messages=[])
  # the prompt arrived as the task's declared INPUT, built from the instance
  # and written inside the session — not staged as a mount
  assert (workspace / PROMPT_NAME).read_text() == "SOLVE THIS"
  assert PROMPT_NAME not in sandbox.mount_targets
  # …and the harness landed its own copy where it wants it (ADR-0007 §8)
  assert (workspace / "prompt.txt").read_text() == "SOLVE THIS"
  assert "prompt.txt" not in sandbox.mount_targets
  assert (workspace / "run_claude_code.sh").is_file()
  # the canonical conversation + the (empty) patch were written
  assert (workspace / "conversation.json").is_file()
  assert (workspace / "patch.diff").read_text() == ""


# ─── the retry policy: only what happened TO the agent (ADR-0011) ────────────


def _attempt(
    outcome: AgentOutcome,
    *,
    status: RunStatus = RunStatus.SUCCESS,
    artifacts: dict[str, epath.Path] | None = None,
    patch: DiffExtractObserver | None = None,
    extractor: bool = True,
) -> AttemptResult:
  """Build a finished rollout attempt whose agent ended the given way.

  Composes a ``DiffExtractObserver`` by default, because the production task
  always does — a fixture without one is not a task that chose not to look, it
  is a **broken composition**, and ``extractor=False`` is how a test says it
  means that.
  """
  observer = HarnessOutcomeObserver(harness=ClaudeCodeHarness())
  observer.outcome = outcome
  return AttemptResult(
      run=RunResult(
          label="acme__widget-1",
          status=status,
          # `outputs_valid` needs the declared required output present, so the
          # engine half is happy and the agent's own ending is what decides.
          artifacts=artifacts
          if artifacts is not None
          else {CONVERSATION_NAME: epath.Path("/tmp/conversation.json")},
          metrics={},
      ),
      exec_result=None,
      output_schema=(ArtifactSchema(CONVERSATION_NAME),),
      observers=(
          (observer,)
          if not extractor
          else (observer, patch or DiffExtractObserver(patch="", is_empty=True))
      ),
  )


def test_an_agents_own_budget_ending_is_never_retried():
  # The score-inflation guard: `max_turns` / `max_budget` /
  # `max_output_retries` are the agent spending what it was given. Retrying
  # hands it a second budget a better-behaved agent would not have needed.
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  for spent in (
      AgentOutcome.MAX_TURNS,
      AgentOutcome.MAX_BUDGET,
      AgentOutcome.MAX_OUTPUT_RETRIES,
  ):
    result = _attempt(spent)
    assert task.outputs_valid(result) is True  # it produced its outputs
    assert task.should_retry(result) is False, spent


def test_an_infrastructure_ending_is_retried():
  # The other half of fairness: an API error, a crash out of the turn loop, or
  # a trace cut mid-flight are OURS, and not retrying them penalizes the agent
  # for our problem.
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  for ours in (
      AgentOutcome.EXECUTION_ERROR,
      AgentOutcome.FINISHED_WITH_API_ERROR,
      AgentOutcome.TRUNCATED,
      AgentOutcome.NO_OUTPUT,
  ):
    assert task.should_retry(_attempt(ours)) is True, ours


def test_a_clean_finish_is_not_retried():
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  assert task.should_retry(_attempt(AgentOutcome.FINISHED)) is False


def test_the_engine_half_still_retries_a_missing_output():
  # Narrowing the agent axis must not weaken the infrastructure one: a run
  # that never produced its declared trace is still ours to re-run.
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  result = _attempt(AgentOutcome.FINISHED, artifacts={})
  assert task.outputs_valid(result) is False
  assert task.should_retry(result) is True


def test_the_rollout_predicate_never_reads_the_patch(tmp_path: Path):
  # The fairness invariant: retry is a function of the two OUTCOME axes only.
  # A predicate that re-ran an empty patch (or a failing grade) would re-roll
  # the agent until it got lucky, which inflates pass@1 directly.
  workspace = tmp_path / "ws"
  task = CodingAgentTask(harness=ClaudeCodeHarness(model="sonnet"))
  result = task.execute(
      _LocalFakeSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=60.0,
  )
  extract = patch_of(result)
  assert extract is not None and extract.is_empty is True  # nothing solved
  observer = outcome_of(result)
  assert observer is not None
  # No agent ran, so there is no trace: that is NO_OUTPUT — retried because it
  # is infrastructure, never because the patch came back empty.
  assert observer.outcome is AgentOutcome.NO_OUTPUT
  assert task.should_retry(result) is True

  # Flip ONLY the agent axis and the answer flips, with the same empty patch
  # still sitting there: the patch is not an input to this decision.
  observer.outcome = AgentOutcome.FINISHED
  assert extract.is_empty is True
  assert task.should_retry(result) is False


def test_the_agent_outcome_lands_on_the_record():
  # The retry decision is a function of this value, so it has to be auditable
  # from the manifest afterwards rather than by re-parsing every trace.
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  # Two words, because they answer different questions: `agent_outcome` is what
  # the trace says the loop did, `rollout_outcome` is what the stage produced.
  # Here the actor spent its own turn budget and left an empty patch behind.
  spent = _attempt(
      AgentOutcome.MAX_TURNS, patch=DiffExtractObserver(patch="", is_empty=True)
  )
  assert task.record_extra(spent) == {
      "agent_outcome": "max_turns",
      "rollout_outcome": "no_patch",
  }


# ─── the outcome words, and which are ours (ADR-0015, ADR-0016) ──────────────


def test_a_system_failure_is_not_graded_and_leaves_the_denominator():
  """Our breakage must not be spent on a grading container, nor counted.

  Measured 2026-09-01: an agent that died in 1.4 s still spent the full 1800 s
  grading budget, and the result was rendered as a zero — indistinguishable
  from a hard task.
  """
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  # A crash is one the agent did not choose, so the ending is ours.
  crashed = _attempt(AgentOutcome.TRUNCATED)
  assert rollout_outcome(crashed) is RolloutOutcome.SYSTEM_FAILED
  assert rollout_outcome(crashed).counts_in_denominator is False
  # …and that is what stops the grading entry: a failed entry blocks the rest.
  assert task.outputs_valid(crashed) is False


def test_an_out_of_memory_kill_is_its_own_word():
  """A box too small says nothing about the task, so it cannot read as one."""
  killed = _attempt(AgentOutcome.TRUNCATED)
  killed.run.metrics[OOM_METRIC] = 1.0
  assert rollout_outcome(killed) is RolloutOutcome.OOM_KILLED
  assert rollout_outcome(killed).counts_in_denominator is False
  # Distinct from the plain crash above even though both are ours: an OOM is
  # a capacity fact, and pooling it into `system_failed` would hide it.
  assert rollout_outcome(killed) is not RolloutOutcome.SYSTEM_FAILED


def test_a_clean_run_that_produced_nothing_stays_in_the_denominator():
  """Giving up cheaply is a result, not an excuse to leave the accounting.

  The mirror of the bug this whole split exists to fix: excluding it would
  raise the measured success rate, and raise it most for the weakest actor.
  """
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  gave_up = _attempt(
      AgentOutcome.FINISHED,
      # It really looked: an extraction that ran and came back empty, which is
      # not the same input as no extraction at all.
      patch=DiffExtractObserver(patch="", is_empty=True),
  )
  assert rollout_outcome(gave_up) is RolloutOutcome.NO_PATCH
  assert rollout_outcome(gave_up).counts_in_denominator is True
  # It is a real result, so it is not refused here — the empty patch it left
  # is stopped by the edge one step later, which costs no container.
  assert task.outputs_valid(gave_up) is True


def test_an_unclassifiable_ending_stays_in_the_denominator():
  """Pins the default direction, so nobody later flips it to "exclude".

  An ending nobody classified can only understate a rate by staying in. The
  opposite default lets the excluded set grow unwatched, in the direction that
  makes results look better.
  """
  assert all(
      outcome.counts_in_denominator
      for outcome in RolloutOutcome
      if outcome
      not in (RolloutOutcome.OOM_KILLED, RolloutOutcome.SYSTEM_FAILED)
  )
  # A budget the actor spent is the actor's, per ADR-0011.
  assert RolloutOutcome.TIMED_OUT.counts_in_denominator is True
  # …and the ending that was attributed to nobody is the one that most needs
  # the default to hold, since nothing positively identified it.
  assert RolloutOutcome.UNCLASSIFIED.counts_in_denominator is True


def test_an_ending_with_no_evidence_is_not_booked_as_the_actors():
  """An absence of evidence must not read as evidence the actor produced none.

  The distinction the enum previously could not make: ``NO_PATCH`` says an
  extraction ran and came back empty. This says the harness supplied no outcome
  at all — and folding the two together charges the actor for instrumentation
  we did not get.
  """
  # A patch, but no harness outcome to read: a crash and a clean stop are
  # indistinguishable from here, so neither attribution is earned.
  extraction = DiffExtractObserver(patch="diff --git a/a b/a", is_empty=False)
  no_outcome = dataclasses.replace(
      _attempt(AgentOutcome.FINISHED), observers=(extraction,)
  )
  assert outcome_of(no_outcome) is None
  assert rollout_outcome(no_outcome) is RolloutOutcome.UNCLASSIFIED
  # …and it is not booked as the actor producing nothing, which is the word it
  # would otherwise have landed on.
  assert rollout_outcome(no_outcome) is not RolloutOutcome.NO_PATCH


def test_the_task_always_composes_the_extractor_it_requires():
  """The premise that makes a missing patch a defect rather than a case.

  `rollout_outcome` treats an absent `DiffExtractObserver` as ours. That is
  only correct because this task composes one unconditionally and declares the
  patch a required output — if a caller could legitimately omit it, the same
  branch would be charging a normal configuration to the system.
  """
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  composed = task.observers(_Instance())
  assert any(isinstance(o, DiffExtractObserver) for o in composed)
  assert PATCH_NAME in {
      schema.name for o in composed for schema in o.output_schema()
  }


def test_a_broken_composition_is_ours_rather_than_an_empty_patch():
  """A missing extractor is our defect, and it must stop the run.

  Distinct from `UNCLASSIFIED`: there is no ambiguity about who owns it. The
  task guarantees the observer, so its absence is broken wiring — and booking
  it as `NO_PATCH` would both charge the actor for it and let grading proceed
  on an attempt that produced no patch at all.
  """
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  broken = _attempt(AgentOutcome.FINISHED, extractor=False)
  assert patch_of(broken) is None
  assert rollout_outcome(broken) is RolloutOutcome.SYSTEM_FAILED
  assert rollout_outcome(broken).ours is True
  assert task.outputs_valid(broken) is False


def test_the_unclassified_count_is_reportable_apart_from_the_excluded_one():
  """The exclusion set is watched by construction; this set is not.

  Every rate is reported with its excluded count (ADR-0015 §5) and this one
  beside it (ADR-0016). The excluded count covers only endings that something
  positively identified, so a failure mode we have not named yet stays silent
  inside the denominator. `unclassified` is the second number that makes it a
  growing figure instead of silence — the property is what a reporter reads, so
  it is asserted to be exactly one word wide.
  """
  assert [o for o in RolloutOutcome if o.unclassified] == [
      RolloutOutcome.UNCLASSIFIED
  ]
  # It is reported *and* counted: the two are independent, and this one is
  # deliberately both — in the denominator, and visible.
  assert RolloutOutcome.UNCLASSIFIED.ours is False
  assert RolloutOutcome.UNCLASSIFIED.counts_in_denominator is True
  # An ending that IS attributed is never counted here, or the number would
  # stop meaning "nobody could attribute this".
  assert RolloutOutcome.NO_PATCH.unclassified is False
  assert RolloutOutcome.SYSTEM_FAILED.unclassified is False
