"""Tests for the rollout CLI wiring (Typer CliRunner, composition mocked)."""

import json
from pathlib import Path
from typing import final

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.rollout as rollout_mod
from swe_lab.conversation import Conversation
from swe_lab.sandbox import RunStatus
from swe_lab.solve import RolloutOutcome

runner = CliRunner()
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"


@final
class _Instance:
  instance_id: str = "acme__widget-1"
  problem_statement: str = "fix it"
  requirements: str = ""
  interface: str = ""


def test_help_lists_rollout():
  top = runner.invoke(app, ["--help"])
  assert top.exit_code == 0
  assert "rollout" in top.output  # the subcommand is listed
  sub = runner.invoke(app, ["rollout", "--help"])
  assert sub.exit_code == 0


def test_requires_oauth_token(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.delenv(TOKEN, raising=False)
  result = runner.invoke(app, ["rollout", "some-id"])
  assert result.exit_code != 0
  assert "not set" in result.output


def _outcome(*, is_empty: bool, patch: str) -> RolloutOutcome:
  return RolloutOutcome(
      instance_id="acme__widget-1",
      patch=patch,
      is_empty=is_empty,
      binary_stripped=False,
      complete=True,
      conversation=Conversation(messages=[]),
      status=RunStatus.SUCCESS,
      workspace=Path("/tmp/ws"),
  )


def _wire(
    monkeypatch: pytest.MonkeyPatch, *, outcome: RolloutOutcome
) -> dict[str, object]:
  """Mock the dataset + composition so the CLI runs without Docker."""
  calls: dict[str, object] = {}

  @final
  class _Dataset:

    def require(self, instance_id: str) -> _Instance:
      calls["required"] = instance_id
      return _Instance()

  def fake_load(name: str) -> _Dataset:
    calls["dataset"] = name
    return _Dataset()

  def fake_spec(instance: object) -> object:
    del instance
    return object()

  def fake_prompt(
      problem: str, *, requirements: str = "", interface: str = ""
  ) -> str:
    del requirements, interface
    return f"PROMPT: {problem}"

  def fake_run_rollout(
      spec: object,
      *,
      prompt: str,
      model: str,
      backend: object,
      workspace: object,
      timeout: object,
  ) -> RolloutOutcome:
    del spec, backend, workspace, timeout
    calls["prompt"] = prompt
    calls["model"] = model
    return outcome

  monkeypatch.setenv(TOKEN, "tok")
  monkeypatch.setattr(rollout_mod, "load_dataset", fake_load)
  monkeypatch.setattr(rollout_mod, "SweBenchProInstance", _Instance)
  monkeypatch.setattr(rollout_mod, "compile_sandbox_spec", fake_spec)
  monkeypatch.setattr(rollout_mod, "build_solve_prompt", fake_prompt)
  monkeypatch.setattr(rollout_mod, "run_rollout", fake_run_rollout)
  return calls


def test_solve_not_graded_exits_zero(monkeypatch: pytest.MonkeyPatch):
  calls = _wire(monkeypatch, outcome=_outcome(is_empty=False, patch="D"))
  result = runner.invoke(app, ["rollout", "acme__widget-1"])
  assert result.exit_code == 0
  payload = json.loads(result.output)
  assert payload["outcome"] == "solved_not_graded"
  assert (
      calls["prompt"] == "PROMPT: fix it"
  )  # dataset-derived, threaded through


def test_empty_patch_graded_exits_one(monkeypatch: pytest.MonkeyPatch):
  _ = _wire(monkeypatch, outcome=_outcome(is_empty=True, patch=""))
  result = runner.invoke(app, ["rollout", "acme__widget-1", "--grade"])
  assert result.exit_code == 1
  payload = json.loads(result.output)
  assert payload["outcome"] == "empty_patch"  # never grades as a pass
  assert payload["grade"]["reason"] == "empty_patch"
