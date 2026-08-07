"""Tests for HarnessOutcomeObserver: byproducts + completion → Contribution."""

from collections.abc import Mapping
from pathlib import Path
from typing import final, override

from etils import epath

from swe_lab.conversation import Conversation
from swe_lab.harnesses import (
    AgentOutcome,
    COMPLETE_METRIC,
    Harness,
    HarnessOutcomeObserver,
)
from swe_lab.sandbox import (
    Contribution,
    ExecResult,
    Mounts,
    NAME_SEPARATOR,
    qualified_name,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.result import merge_contributions
from swe_lab.sandbox.testing import FakeSandbox

EVENT_STREAM = "event_stream.jsonl"
STDERR = "agent.stderr.log"


class _StubHarness(Harness):
  """A harness that declares two byproducts and a scripted outcome."""

  def __init__(self, *, outcome: AgentOutcome = AgentOutcome.FINISHED) -> None:
    self._outcome: AgentOutcome = outcome
    self.seen: SandboxFs | None = None

  @property
  @override
  def name(self) -> str:
    return "stub"

  @override
  def mounts(self, workdir: str) -> Mounts:
    del workdir
    return {}

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    return ()  # this test drives HarnessOutcomeObserver directly

  @override
  def run(
      self,
      sb: SandboxFs,
      *,
      prompt: str,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    del prompt, timeout, env
    return sb.run_script("noop", timeout=0.0)

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"event_stream.jsonl": EVENT_STREAM, "stderr.log": STDERR}

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    del sb
    return Conversation(messages=[])

  @override
  def outcome(self, sb: SandboxFs) -> AgentOutcome:
    self.seen = sb
    return self._outcome


def _sandbox(workspace: Path) -> FakeSandbox:
  return FakeSandbox(
      spec=SandboxSpec("acme__widget-1", "img:tag", "/app", "abc"),
      workspace=epath.Path(workspace),
  )


def test_registers_every_byproduct_that_landed(tmp_path: Path):
  _ = (tmp_path / EVENT_STREAM).write_text('{"type":"x"}\n')
  _ = (tmp_path / STDERR).write_text("some stderr\n")
  harness = _StubHarness()
  observer = HarnessOutcomeObserver(harness=harness)
  sb = _sandbox(tmp_path)

  contribution = observer.before_destroy(sb)

  assert harness.seen is sb  # the completion signal is read from the sandbox
  assert contribution is not None
  # namespaced by the harness: the roles themselves are generic, so unqualified
  # they would collide between harnesses and lose their provenance
  assert contribution.artifacts == {
      "stub.event_stream.jsonl": EVENT_STREAM,
      "stub.stderr.log": STDERR,
  }
  assert observer.collected == contribution.artifacts


def test_absent_byproducts_are_skipped_best_effort(tmp_path: Path):
  _ = (tmp_path / EVENT_STREAM).write_text('{"type":"x"}\n')  # stderr missing
  observer = HarnessOutcomeObserver(harness=_StubHarness())

  contribution = observer.before_destroy(_sandbox(tmp_path))

  assert contribution is not None
  # a run that died early yields fewer artifacts, never a broken reference
  assert contribution.artifacts == {"stub.event_stream.jsonl": EVENT_STREAM}


def test_completion_is_kept_and_exported_as_a_metric(tmp_path: Path):
  observer = HarnessOutcomeObserver(
      harness=_StubHarness(outcome=AgentOutcome.FINISHED)
  )
  contribution = observer.before_destroy(_sandbox(tmp_path))
  assert observer.complete is True  # readable by the composition
  assert contribution is not None
  assert contribution.metrics == {COMPLETE_METRIC: 1.0}  # and persisted


def test_incomplete_run_is_recorded_not_dropped(tmp_path: Path):
  observer = HarnessOutcomeObserver(
      harness=_StubHarness(outcome=AgentOutcome.EXECUTION_ERROR)
  )
  contribution = observer.before_destroy(_sandbox(tmp_path))
  assert observer.complete is False
  assert contribution is not None
  # 0.0, not absent: "the agent crashed" must be distinguishable downstream
  # from "this run reported no completion metric at all".
  assert contribution.metrics == {COMPLETE_METRIC: 0.0}


def test_two_harnesses_sharing_a_role_do_not_collide(tmp_path: Path):
  # Without namespacing both would claim "agent_stderr" and the engine would
  # refuse the merge; qualified by harness they coexist.
  @final
  class _Other(_StubHarness):

    @property
    @override
    def name(self) -> str:
      return "other_agent"

  _ = (tmp_path / STDERR).write_text("err\n")
  sb = _sandbox(tmp_path)
  first = HarnessOutcomeObserver(harness=_StubHarness())
  second = HarnessOutcomeObserver(harness=_Other())
  _ = first.before_destroy(sb)
  _ = second.before_destroy(sb)
  # merge just the artifact halves: the completion *metric* stays unqualified on
  # purpose (one run has one agent), so it is not what this asserts.
  merged = merge_contributions(
      [
          Contribution(artifacts=first.collected),
          Contribution(artifacts=second.collected),
      ]
  )  # would raise if the names collided
  assert "stub.stderr.log" in merged.artifacts
  assert "other_agent.stderr.log" in merged.artifacts


def test_qualified_name_uses_a_dot_not_a_path_separator():
  # These are logical names in a manifest, not store key paths.
  assert qualified_name("claude_code", "stderr.log") == "claude_code.stderr.log"
  assert "/" not in NAME_SEPARATOR


def test_outcome_defaults_to_no_output_before_the_hook_runs():
  # A run whose sandbox never came up never fires before_destroy. NO_OUTPUT is
  # the honest reading — there is no trace — rather than an optimistic default.
  observer = HarnessOutcomeObserver(harness=_StubHarness())
  assert observer.outcome is AgentOutcome.NO_OUTPUT
  assert observer.complete is False
