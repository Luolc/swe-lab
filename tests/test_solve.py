"""Tests for run_rollout: the composition on a fake backend (no Docker)."""

from pathlib import Path

import pytest

from swe_lab.conversation import Conversation
from swe_lab.sandbox import RunStatus, SandboxSpec
from swe_lab.sandbox.testing import FakeBackend
from swe_lab.solve import run_rollout


def test_run_rollout_wires_and_assembles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  binary = tmp_path / "claude"
  _ = binary.write_bytes(b"BIN")
  # avoid provisioning (network): the harness assets() calls this
  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.harness.ensure_claude_binary",
      lambda: binary,
  )
  workspace = tmp_path / "ws"
  spec = SandboxSpec("acme__widget-1", "img:tag", "/app", "base")

  outcome = run_rollout(
      spec,
      prompt="SOLVE THIS",
      model="sonnet",
      backend=FakeBackend(),
      workspace=workspace,
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
  # the harness's own agent.sh was staged too
  assert (workspace / "prompt.txt").read_text() == "SOLVE THIS"
  assert (workspace / "agent.sh").is_file()
  # the canonical conversation + the (empty) patch were written
  assert (workspace / "conversation.json").is_file()
  assert (workspace / "patch.diff").read_text() == ""
