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
import pytest

from swe_lab.conversation import Conversation
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.rollout import (
    CodingAgentTask,
    conversation_of,
    outcome_of,
    patch_of,
    PROMPT_NAME,
)
from swe_lab.sandbox import Mount, RunStatus, SandboxSpec
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.testing import FakeSandbox

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
  ) -> UnitTestSpec[Verdict]:
    raise NotImplementedError("this instance is only solved, never graded")


@dataclasses.dataclass
class _LocalFakeSandbox(FakeSandbox):
  """A ``FakeSandbox`` that keeps absolute mounts inside the workspace.

  The harness stages its pinned binary at a fixed absolute path (``/opt/...``);
  writing there on the host needs root, so redirect every mount under the real
  workspace dir. Exec stays scripted, so the agent never actually runs.
  Mount targets are recorded so a test can tell a *mount* from a ``write``.
  """

  mount_targets: list[str] = dataclasses.field(default_factory=list)

  @override
  def _mount_one(self, target: str, mount: Mount) -> None:
    self.mount_targets.append(target)
    super()._mount_one(target, mount)

  @override
  def _dest(self, target: str) -> epath.Path:
    return epath.Path(self.workspace / target.lstrip("/"))


def test_the_task_wires_and_assembles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  binary = tmp_path / "claude"
  _ = binary.write_bytes(b"BIN")
  # avoid provisioning (network): the harness's mounts() calls this
  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.harness.ensure_claude_binary",
      lambda: binary,
  )
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
