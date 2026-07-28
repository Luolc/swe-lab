"""Tests for the `eval` CLI wiring (Typer CliRunner, engine mocked)."""

import json
from pathlib import Path
from typing import cast, final, override

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.eval as eval_mod
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    SweBenchProVerdict,
)
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.sandbox import RunResult, RunStatus, SandboxSpec

runner = CliRunner()


def test_help_lists_eval_and_shows_docstring():
  # Rich renders help with ANSI + width-dependent wrapping, so assert only on
  # robust content (option-name rendering is Typer's job, exercised by the
  # functional tests below).
  top = runner.invoke(app, ["--help"])
  assert top.exit_code == 0
  assert "eval" in top.output  # the subcommand is listed
  sub = runner.invoke(app, ["eval", "--help"])
  assert sub.exit_code == 0
  assert "Grade one instance" in sub.output  # the docstring became the help


def test_requires_exactly_one_patch_source():
  neither = runner.invoke(app, ["eval", "some-id"])
  assert neither.exit_code != 0  # BadParameter
  assert "exactly one" in neither.output


def _wire(
    monkeypatch: pytest.MonkeyPatch, *, verdict: SweBenchProVerdict | None
) -> dict[str, object]:
  """Mock the dataset + engine so the CLI runs without Docker; record calls."""
  calls: dict[str, object] = {}

  spec = SandboxSpec("acme__widget-1", "img:tag", "/app", "abc")

  @final
  class _Instance(
      TaskInstance[Verdict]
  ):  # a runnable instance, no concrete dataset
    instance_id: str = "acme__widget-1"

    @override
    def sandbox_spec(self) -> SandboxSpec:
      return spec

    @override
    def solve_prompt(self) -> str:
      return "PROMPT"

    @override
    def gold_patch(self) -> str:
      return "GOLD DIFF"

    @override
    def unit_test_spec(
        self,
        *,
        patch: str | None,
        checkout_golden_tests: bool = True,
    ) -> UnitTestSpec[Verdict]:
      del checkout_golden_tests
      calls["patch"] = patch  # the spec is discarded by the mocked run
      return cast(UnitTestSpec[Verdict], object())

  @final
  class _Dataset:

    def require(self, instance_id: str) -> _Instance:
      calls["required"] = instance_id
      return _Instance()

  def fake_load_dataset(name: str) -> _Dataset:
    calls["dataset"] = name
    return _Dataset()

  def fake_run(
      sandbox_spec: object,
      unit_spec: object,
      *,
      backend: object,
      workspace: object,
      timeout: object,
      **kwargs: object,
  ) -> tuple[RunResult, SweBenchProVerdict | None]:
    del sandbox_spec, unit_spec, workspace, timeout, kwargs
    calls["ran"] = True
    calls["backend"] = backend
    result = RunResult(
        label="acme__widget-1",
        status=RunStatus.SUCCESS,
        artifacts={},
        metrics={},
    )
    return result, verdict

  monkeypatch.setattr(eval_mod, "load_dataset", fake_load_dataset)
  monkeypatch.setattr(eval_mod, "run_unit_test", fake_run)
  return calls


def test_gold_resolved_exits_zero(monkeypatch: pytest.MonkeyPatch):
  verdict = SweBenchProVerdict(
      passed=frozenset({"a"}), missing=frozenset(), output_state=OutputState.OK
  )
  calls = _wire(monkeypatch, verdict=verdict)
  result = runner.invoke(app, ["eval", "acme__widget-1", "--gold"])
  assert result.exit_code == 0
  payload = json.loads(result.output)
  assert payload["resolved"] is True
  assert payload["score"] == 1.0
  assert payload["output_state"] == "ok"
  assert calls["patch"] == "GOLD DIFF"  # --gold used the instance's patch


def test_persist_writes_a_manifest_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  verdict = SweBenchProVerdict(
      passed=frozenset({"a"}), missing=frozenset(), output_state=OutputState.OK
  )
  _ = _wire(monkeypatch, verdict=verdict)
  monkeypatch.setattr(eval_mod, "find_repo_root", lambda: tmp_path)
  result = runner.invoke(
      app, ["eval", "acme__widget-1", "--gold", "--persist", "--sweep", "sw1"]
  )
  assert result.exit_code == 0
  shards = list((tmp_path / ".cache" / "store").rglob("run.json"))
  assert len(shards) == 1  # the grading run's shard (the verdict is in extra)
  assert "persisted" in json.loads(result.output)


def test_unresolved_exits_one(monkeypatch: pytest.MonkeyPatch):
  verdict = SweBenchProVerdict(
      passed=frozenset(),
      missing=frozenset({"a"}),
      output_state=OutputState.OK,
  )
  _ = _wire(monkeypatch, verdict=verdict)
  result = runner.invoke(app, ["eval", "acme__widget-1", "--gold"])
  assert result.exit_code == 1
  assert json.loads(result.output)["resolved"] is False


def test_patch_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
  verdict = SweBenchProVerdict(
      passed=frozenset({"a"}), missing=frozenset(), output_state=OutputState.OK
  )
  calls = _wire(monkeypatch, verdict=verdict)
  diff = tmp_path / "cand.diff"
  _ = diff.write_text("CANDIDATE DIFF")
  result = runner.invoke(
      app, ["eval", "acme__widget-1", "--patch-file", str(diff)]
  )
  assert result.exit_code == 0
  assert calls["patch"] == "CANDIDATE DIFF"


def test_setup_failure_none_verdict_exits_one(
    monkeypatch: pytest.MonkeyPatch,
):
  _ = _wire(monkeypatch, verdict=None)

  def fake_run_err(
      sandbox_spec: object,
      unit_spec: object,
      *,
      backend: object,
      workspace: object,
      timeout: object,
      **kwargs: object,
  ) -> tuple[RunResult, None]:
    del sandbox_spec, unit_spec, backend, workspace, timeout, kwargs
    result = RunResult(
        label="acme__widget-1",
        status=RunStatus.SETUP_ERROR,
        artifacts={},
        metrics={},
        error=RuntimeError("no docker"),
    )
    return result, None

  monkeypatch.setattr(eval_mod, "run_unit_test", fake_run_err)
  result = runner.invoke(app, ["eval", "acme__widget-1", "--gold"])
  assert result.exit_code == 1
  payload = json.loads(result.output)
  assert payload["resolved"] is False
  assert payload["status"] == "setup_error"
  assert "error" in payload
