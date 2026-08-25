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
    outcome_of,
    patch_of,
    PROMPT_NAME,
)
from swe_lab.sandbox import (
    ArtifactSchema,
    Mount,
    RunResult,
    RunStatus,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
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
) -> AttemptResult:
  """Build a finished rollout attempt whose agent ended the given way."""
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
      observers=(observer,),
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
  assert task.record_extra(_attempt(AgentOutcome.MAX_TURNS)) == {
      "agent_outcome": "max_turns"
  }
