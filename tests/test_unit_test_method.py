"""Tests for run_unit_test: the composition on a fake backend (no Docker)."""

import json
from pathlib import Path

from swe_lab.core.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.methods.unit_test import (
    ENTRYSCRIPT_NAME,
    run_unit_test,
)
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Mount, RunStatus, SandboxSpec
from swe_lab.sandbox.testing import FakeBackend

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def _unit_spec(
    required: list[str], passed: list[str]
) -> UnitTestSpec[SweBenchProVerdict]:
  # The fake backend does not run the eval script, so the "results" are the
  # required_tests.json mount + an output.json we stage as if the run wrote it.
  output = json.dumps(
      {"tests": [{"name": n, "status": "PASSED"} for n in passed]}
  )
  return UnitTestSpec(
      eval_script="echo eval\n",
      mounts={
          REQUIRED_TESTS_NAME: Mount(content=json.dumps(required).encode()),
          "output.json": Mount(content=output.encode()),
      },
      grader=SweBenchProGrader(),
  )


def test_run_stages_entryscript_and_grades(tmp_path: Path):
  backend = FakeBackend()
  result, verdict = run_unit_test(
      SPEC,
      _unit_spec(["a", "b"], ["a", "b"]),
      backend=backend,
      workspace=tmp_path / "ws",
  )
  # the eval script is run as entryscript.sh (a workspace file, by name)
  assert backend.scripts == [ENTRYSCRIPT_NAME]
  assert result.status is RunStatus.SUCCESS
  assert isinstance(verdict, SweBenchProVerdict)
  assert verdict.resolved is True
  assert verdict.score == 1.0


def test_run_partial_pass_not_resolved(tmp_path: Path):
  _, verdict = run_unit_test(
      SPEC,
      _unit_spec(["a", "b"], ["a"]),
      backend=FakeBackend(),
      workspace=tmp_path / "ws",
  )
  assert verdict is not None
  assert verdict.resolved is False


def test_grader_runs_even_when_body_exec_fails(tmp_path: Path):
  # a nonzero entryscript still lets before_destroy grade (task-02 semantics)
  from swe_lab.sandbox import ExecResult

  backend = FakeBackend(run_results=[ExecResult(1, "", "boom")])
  result, verdict = run_unit_test(
      SPEC,
      _unit_spec(["a"], ["a"]),
      backend=backend,
      workspace=tmp_path / "ws",
  )
  assert result.status is RunStatus.SUCCESS  # body did not raise; it returned 1
  assert verdict is not None
  assert verdict.resolved is True  # graded from the staged output


def test_setup_failure_is_captured_not_raised(tmp_path: Path):
  from swe_lab.sandbox import SandboxError

  backend = FakeBackend(up_error=SandboxError("no docker"))
  result, verdict = run_unit_test(
      SPEC,
      _unit_spec(["a"], ["a"]),
      backend=backend,
      workspace=tmp_path / "ws",
  )
  assert result.status is RunStatus.SETUP_ERROR
  assert isinstance(result.error, SandboxError)
  assert verdict is None  # grading never ran
