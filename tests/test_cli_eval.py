"""Tests for the `eval` CLI wiring (Typer CliRunner, on the fake backend).

Nothing about the run is mocked out any more: the command builds the real eval
task and runs it through ``run_task``, on the registered ``fake`` **backend**
(``--backend fake``) whose sandboxes do real file operations over a local
directory and script their execs. Only the dataset is a stand-in.
"""

import json
from pathlib import Path
from typing import final, override

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.eval as eval_mod
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Inline, Mount, SandboxSpec
from swe_lab.sandbox.observers import PATCH_NAME

# Importing the doubles registers the `fake` backend the CLI runs on here.
from swe_lab.sandbox.testing import FakeSandboxConfig

runner = CliRunner()

_SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "abc")


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


@final
class _Instance(TaskInstance[SweBenchProVerdict]):
  """A runnable instance with no concrete dataset behind it.

  Its compiled spec is gradeable on the fake backend: the "results" are the
  expectation mount plus an ``output.json`` staged as if the run had written
  it, so the real grader produces a real verdict.
  """

  instance_id = "acme__widget-1"

  def __init__(self, *, gold: str | None, passed: list[str]) -> None:
    self._gold = gold
    self._passed = passed
    self.graded: list[str] = []

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return _SPEC

  @override
  def prompt(self) -> str:
    return "PROMPT"

  @override
  def gold_patch(self) -> str | None:
    return self._gold

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    del checkout_golden_tests
    assert apply_patch  # the CLI always grades a patch
    output = json.dumps(
        {"tests": [{"name": n, "status": "PASSED"} for n in self._passed]}
    )
    return UnitTestSpec(
        eval_script="echo eval\n",
        mounts={
            REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(["a"]).encode())),
            "output.json": Mount(Inline(output.encode())),
        },
        grader=SweBenchProGrader(),
        patch_name=patch_name,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    passed: list[str],
    gold: str | None = "GOLD DIFF",
) -> _Instance:
  """Point the CLI at a stand-in dataset and a throwaway repo root."""
  instance = _Instance(gold=gold, passed=passed)

  @final
  class _Dataset:

    def require(self, instance_id: str) -> _Instance:
      assert instance_id == "acme__widget-1"
      return instance

  def fake_load_dataset(name: str) -> _Dataset:
    del name
    return _Dataset()

  monkeypatch.setattr(eval_mod, "load_dataset", fake_load_dataset)
  monkeypatch.setattr(eval_mod, "find_repo_root", lambda: tmp_path)
  return instance


def _staged_patch(tmp_path: Path) -> str:
  """Return the patch the graded attempt actually had in its workspace."""
  run_dir = tmp_path / ".cache" / "eval_workspaces" / "acme__widget-1"
  return (run_dir / "ws" / "a0" / PATCH_NAME).read_text()


def test_gold_resolved_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _ = _wire(monkeypatch, tmp_path, passed=["a"])
  result = runner.invoke(
      app, ["eval", "acme__widget-1", "--gold", "--backend", "fake"]
  )
  assert result.exit_code == 0
  payload = json.loads(result.output)
  assert payload["resolved"] is True
  assert payload["score"] == 1.0
  assert payload["output_state"] == "ok"
  assert payload["attempts"] == 1
  # --gold fed the instance's own patch through the task's declared input
  assert _staged_patch(tmp_path) == "GOLD DIFF"


def test_gold_on_a_dataset_without_one_is_refused_not_graded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # `gold_patch()` returning None is *not* the "grade the base commit" request:
  # falling through would grade the wrong tree and report it as the gold patch
  # failing.
  _ = _wire(monkeypatch, tmp_path, passed=["a"], gold=None)
  result = runner.invoke(
      app, ["eval", "acme__widget-1", "--gold", "--backend", "fake"]
  )
  assert result.exit_code != 0
  assert "no gold patch" in result.output
  # refused before anything ran: no workspace was ever allocated
  assert not (tmp_path / ".cache" / "eval_workspaces").exists()


def test_persist_writes_a_manifest_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _ = _wire(monkeypatch, tmp_path, passed=["a"])
  result = runner.invoke(
      app,
      [
          "eval",
          "acme__widget-1",
          "--gold",
          "--backend",
          "fake",
          "--persist",
          "--sweep",
          "sw1",
      ],
  )
  assert result.exit_code == 0
  shards = list((tmp_path / ".cache" / "store" / "runs").rglob("run.json"))
  assert len(shards) == 1  # the grading run's shard
  record = json.loads(shards[0].read_text())
  assert record["sweep_id"] == "sw1" and record["task"] == "unit_test"
  assert "persisted" in json.loads(result.output)


def test_without_persist_nothing_reaches_the_shared_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # Running always persists — that is what makes every attempt evidence — so
  # `--persist` decides *where*: the shared T1 store, or a throwaway one under
  # the run's own directory.
  _ = _wire(monkeypatch, tmp_path, passed=["a"])
  result = runner.invoke(
      app, ["eval", "acme__widget-1", "--gold", "--backend", "fake"]
  )
  assert result.exit_code == 0
  assert not (tmp_path / ".cache" / "store" / "runs").exists()
  assert "persisted" not in json.loads(result.output)


def test_unresolved_exits_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
  _ = _wire(monkeypatch, tmp_path, passed=[])
  result = runner.invoke(
      app,
      [
          "eval",
          "acme__widget-1",
          "--gold",
          "--backend",
          "fake",
          "--retries",
          "0",
      ],
  )
  assert result.exit_code == 1
  assert json.loads(result.output)["resolved"] is False


def test_patch_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
  _ = _wire(monkeypatch, tmp_path, passed=["a"])
  diff = tmp_path / "cand.diff"
  _ = diff.write_text("CANDIDATE DIFF")
  result = runner.invoke(
      app,
      [
          "eval",
          "acme__widget-1",
          "--patch-file",
          str(diff),
          "--backend",
          "fake",
      ],
  )
  assert result.exit_code == 0
  assert _staged_patch(tmp_path) == "CANDIDATE DIFF"


def test_a_backend_that_cannot_come_up_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # An infrastructure failure is captured, reported, and exits non-zero — it
  # never looks like a graded "no".
  _ = _wire(monkeypatch, tmp_path, passed=["a"])

  def exploding_config(backend: str, **kwargs: object) -> FakeSandboxConfig:
    del backend, kwargs
    return FakeSandboxConfig(up_errors=99)

  monkeypatch.setattr(eval_mod, "invocation_config", exploding_config)
  result = runner.invoke(
      app, ["eval", "acme__widget-1", "--gold", "--backend", "fake"]
  )
  assert result.exit_code == 1
  payload = json.loads(result.output)
  assert payload["resolved"] is False
  assert payload["status"] == "setup_error"
  assert "error" in payload
