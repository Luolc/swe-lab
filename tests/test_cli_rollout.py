"""Tests for the rollout CLI wiring (Typer CliRunner, composition mocked)."""

import json
from pathlib import Path
from typing import cast, final, override

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.rollout as rollout_mod
from swe_lab.conversation import Conversation
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.rollout import RolloutOutcome
from swe_lab.sandbox import RunStatus, SandboxSpec

runner = CliRunner()
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY = "ANTHROPIC_API_KEY"
_SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "abc")


@final
class _Instance(
    TaskInstance[Verdict]
):  # a runnable instance, no concrete dataset
  instance_id: str = "acme__widget-1"
  problem_statement: str = "fix it"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return _SPEC

  @override
  def solve_prompt(self) -> str:
    return f"PROMPT: {self.problem_statement}"

  @override
  def gold_patch(self) -> str:
    return "GOLD"

  @override
  def unit_test_spec(
      self,
      *,
      patch: str | None,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[Verdict]:
    del patch, checkout_golden_tests
    return cast(UnitTestSpec[Verdict], object())


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


def _outcome(
    *, is_empty: bool, patch: str, artifacts: dict[str, Path] | None = None
) -> RolloutOutcome:
  return RolloutOutcome(
      instance_id="acme__widget-1",
      patch=patch,
      is_empty=is_empty,
      binary_stripped=False,
      complete=True,
      conversation=Conversation(messages=[]),
      status=RunStatus.SUCCESS,
      workspace=Path("/tmp/ws"),
      artifacts=artifacts or {},
      metrics={},
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

  def fake_run_rollout(
      spec: object,
      *,
      prompt: str,
      model: str,
      backend: object,
      workspace: object,
      timeout: object,
      capture: object,
      **kwargs: object,
  ) -> RolloutOutcome:
    del spec, workspace, timeout
    calls["prompt"] = prompt
    calls["model"] = model
    calls["capture"] = capture
    calls["backend"] = backend
    calls["bare"] = kwargs.get("bare")
    calls["pass_env"] = kwargs.get("pass_env")
    return outcome

  monkeypatch.setenv(TOKEN, "tok")
  monkeypatch.setattr(rollout_mod, "load_dataset", fake_load)
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
  # non-bare passes the OAuth token by reference
  assert calls["bare"] is False
  assert calls["pass_env"] == (TOKEN,)


def test_bare_requires_api_key_env(monkeypatch: pytest.MonkeyPatch):
  _ = _wire(monkeypatch, outcome=_outcome(is_empty=False, patch="D"))
  monkeypatch.delenv(API_KEY, raising=False)
  result = runner.invoke(app, ["rollout", "acme__widget-1", "--bare"])
  assert result.exit_code != 0
  assert API_KEY in result.output


def test_bare_passes_api_key_env_by_reference(monkeypatch: pytest.MonkeyPatch):
  calls = _wire(monkeypatch, outcome=_outcome(is_empty=False, patch="D"))
  monkeypatch.setenv(API_KEY, "sk-x")
  result = runner.invoke(app, ["rollout", "acme__widget-1", "--bare"])
  assert result.exit_code == 0
  assert calls["bare"] is True
  # the key is passed by NAME (like the OAuth token), never its value
  assert calls["pass_env"] == (API_KEY,)


def test_persist_writes_a_manifest_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  art = tmp_path / "patch.diff"
  _ = art.write_text("DIFF")
  _ = _wire(
      monkeypatch,
      outcome=_outcome(is_empty=False, patch="D", artifacts={"patch": art}),
  )
  monkeypatch.setattr(rollout_mod, "find_repo_root", lambda: tmp_path)
  result = runner.invoke(
      app, ["rollout", "acme__widget-1", "--persist", "--sweep", "sw1"]
  )
  assert result.exit_code == 0
  shards = list((tmp_path / ".cache" / "store").rglob("run.json"))
  assert len(shards) == 1  # one per-run shard under the sweep
  assert (tmp_path / ".cache" / "store" / "runs" / "sw1").is_dir()
  assert "persisted" in json.loads(result.output)


def test_no_persist_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _ = _wire(monkeypatch, outcome=_outcome(is_empty=False, patch="D"))
  monkeypatch.setattr(rollout_mod, "find_repo_root", lambda: tmp_path)
  result = runner.invoke(app, ["rollout", "acme__widget-1"])
  assert result.exit_code == 0
  assert not (tmp_path / ".cache" / "store").exists()  # debug persists nothing
  assert "persisted" not in json.loads(result.output)


def test_empty_patch_graded_exits_one(monkeypatch: pytest.MonkeyPatch):
  _ = _wire(monkeypatch, outcome=_outcome(is_empty=True, patch=""))
  result = runner.invoke(app, ["rollout", "acme__widget-1", "--grade"])
  assert result.exit_code == 1
  payload = json.loads(result.output)
  assert payload["outcome"] == "empty_patch"  # never grades as a pass
  assert payload["grade"]["reason"] == "empty_patch"
