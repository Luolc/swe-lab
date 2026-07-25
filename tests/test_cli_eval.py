"""Tests for the `eval` CLI wiring (Typer CliRunner, engine mocked)."""

import json
from pathlib import Path
from typing import final

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.eval as eval_mod
from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    SweBenchProVerdict,
)
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

  @final
  class _Instance:
    instance_id: str = "acme__widget-1"
    patch: str = "GOLD DIFF"

  @final
  class _Dataset:

    def require(self, instance_id: str) -> _Instance:
      calls["required"] = instance_id
      return _Instance()

  def fake_load_dataset(name: str) -> _Dataset:
    calls["dataset"] = name
    return _Dataset()

  def fake_compile(
      instance: object, *, patch: str, repo_root: Path | None = None
  ) -> tuple[SandboxSpec, object]:
    del instance, repo_root
    calls["patch"] = patch
    return SandboxSpec("acme__widget-1", "img:tag", "/app", "abc"), object()

  def fake_run(
      sandbox_spec: object,
      unit_spec: object,
      *,
      backend: object,
      workspace: object,
      timeout: object,
  ) -> tuple[RunResult, SweBenchProVerdict | None]:
    del sandbox_spec, unit_spec, backend, workspace, timeout
    calls["ran"] = True
    result = RunResult(
        label="acme__widget-1",
        status=RunStatus.SUCCESS,
        artifacts={},
        metrics={},
    )
    return result, verdict

  monkeypatch.setattr(eval_mod, "load_dataset", fake_load_dataset)
  # bypass the SweBenchProInstance isinstance guard
  monkeypatch.setattr(eval_mod, "SweBenchProInstance", _Instance)
  monkeypatch.setattr(eval_mod, "compile_unit_test", fake_compile)
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
  ) -> tuple[RunResult, None]:
    del sandbox_spec, unit_spec, backend, workspace, timeout
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
