"""Tests for run_rollout: the composition on an injected fake sandbox.

``run_rollout`` takes the sandbox by **injection**, so a test just constructs a
:class:`FakeSandbox` (real local-dir file ops, scripted exec, no Docker) and
passes it — no backend registry, no patching a construction function. The whole
composition (manager → observers → harness) runs docker-free while no agent
process ever spawns.
"""

from pathlib import Path
from typing import override

from etils import epath
import pytest

from swe_lab.conversation import Conversation
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.rollout import run_rollout
from swe_lab.sandbox import RunStatus, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox


class _LocalFakeSandbox(FakeSandbox):
  """A ``FakeSandbox`` that keeps absolute mounts inside the workspace.

  The harness stages its pinned binary at a fixed absolute path (``/opt/...``);
  writing there on the host needs root, so redirect every mount under the real
  workspace dir. Exec stays scripted, so the agent never actually runs.
  """

  @override
  def _dest(self, target: str) -> epath.Path:
    return epath.Path(self.workspace / target.lstrip("/"))


def test_run_rollout_wires_and_assembles(
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
  spec = SandboxSpec("acme__widget-1", "img:tag", "/app", "base")
  sandbox = _LocalFakeSandbox(spec=spec, workspace=epath.Path(workspace))

  outcome = run_rollout(
      sandbox,
      ClaudeCodeHarness(model="sonnet"),
      prompt="SOLVE THIS",
      output_dir=workspace,
      timeout=60.0,
  )

  # the run wired up and assembled — no agent ran, so the patch/trace are empty
  assert outcome.instance_id == "acme__widget-1"
  assert outcome.status is RunStatus.SUCCESS
  assert outcome.is_empty is True
  assert outcome.patch == ""
  assert outcome.complete is False
  assert outcome.conversation == Conversation(messages=[])
  # the dataset-derived prompt was staged as prompt.txt (not a harness mount);
  # the harness's own run script was staged too
  assert (workspace / "prompt.txt").read_text() == "SOLVE THIS"
  assert (workspace / "run_claude_code.sh").is_file()
  # the canonical conversation + the (empty) patch were written
  assert (workspace / "conversation.json").is_file()
  assert (workspace / "patch.diff").read_text() == ""
