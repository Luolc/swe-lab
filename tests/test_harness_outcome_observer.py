"""Tests for HarnessOutcomeObserver: byproducts + completion → Contribution."""

from collections.abc import Mapping
from pathlib import Path
from typing import final, override

from etils import epath

from swe_lab.conversation import Conversation
from swe_lab.harnesses import COMPLETE_METRIC, Harness, HarnessOutcomeObserver
from swe_lab.sandbox import Mounts, SandboxFs, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

EVENT_STREAM = "event_stream.jsonl"
STDERR = "agent.stderr"


@final
class _StubHarness(Harness):
  """A harness that declares two byproducts and a scripted completion."""

  def __init__(self, *, complete: bool = True) -> None:
    self._complete = complete
    self.seen: SandboxFs | None = None

  @override
  def mounts(self, workdir: str) -> Mounts:
    del workdir
    return {}

  @override
  def run(
      self,
      sb: SandboxFs,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> None:
    del sb, timeout, env

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"event_stream": EVENT_STREAM, "agent_stderr": STDERR}

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    del sb
    return Conversation(messages=[])

  @override
  def completed(self, sb: SandboxFs) -> bool:
    self.seen = sb
    return self._complete


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
  assert contribution.artifacts == {
      "event_stream": EVENT_STREAM,
      "agent_stderr": STDERR,
  }
  assert observer.collected == contribution.artifacts


def test_absent_byproducts_are_skipped_best_effort(tmp_path: Path):
  _ = (tmp_path / EVENT_STREAM).write_text('{"type":"x"}\n')  # stderr missing
  observer = HarnessOutcomeObserver(harness=_StubHarness())

  contribution = observer.before_destroy(_sandbox(tmp_path))

  assert contribution is not None
  # a run that died early yields fewer artifacts, never a broken reference
  assert contribution.artifacts == {"event_stream": EVENT_STREAM}


def test_completion_is_kept_and_exported_as_a_metric(tmp_path: Path):
  observer = HarnessOutcomeObserver(harness=_StubHarness(complete=True))
  contribution = observer.before_destroy(_sandbox(tmp_path))
  assert observer.complete is True  # readable by the composition
  assert contribution is not None
  assert contribution.metrics == {COMPLETE_METRIC: 1.0}  # and persisted


def test_incomplete_run_is_recorded_not_dropped(tmp_path: Path):
  observer = HarnessOutcomeObserver(harness=_StubHarness(complete=False))
  contribution = observer.before_destroy(_sandbox(tmp_path))
  assert observer.complete is False
  assert contribution is not None
  # 0.0, not absent: "the agent crashed" must be distinguishable downstream
  # from "this run reported no completion metric at all".
  assert contribution.metrics == {COMPLETE_METRIC: 0.0}


def test_complete_defaults_false_before_the_hook_runs():
  # A run whose sandbox never came up never fires before_destroy; the outcome
  # must read as incomplete rather than as an optimistic default.
  assert HarnessOutcomeObserver(harness=_StubHarness()).complete is False
