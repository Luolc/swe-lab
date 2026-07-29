"""Tests for run_unit_test: the composition on an injected FakeSandbox.

`run_unit_test` takes the sandbox by **injection**, so a test just constructs a
:class:`FakeSandbox` (real local-dir file ops, scripted exec, no Docker) and
passes it — no backend registry, no patching a construction function.
"""

import json
from pathlib import Path

from etils import epath

from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.methods.unit_test import (
    ENTRYSCRIPT_NAME,
    run_unit_test,
)
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import (
    ExecResult,
    Inline,
    Mount,
    RunStatus,
    SandboxError,
    SandboxSpec,
)
from swe_lab.sandbox.testing import FakeSandbox

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def _fake(
    tmp_path: Path,
    *,
    run_results: list[ExecResult] | None = None,
    up_error: Exception | None = None,
) -> FakeSandbox:
  return FakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      run_results=list(run_results or []),
      up_error=up_error,
  )


def _unit_test_spec(
    required: list[str], passed: list[str]
) -> UnitTestSpec[SweBenchProVerdict]:
  # The fake sandbox does not run the eval script, so the "results" are the
  # required_tests.json mount + an output.json we stage as if the run wrote it.
  output = json.dumps(
      {"tests": [{"name": n, "status": "PASSED"} for n in passed]}
  )
  return UnitTestSpec(
      eval_script="echo eval\n",
      mounts={
          REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(required).encode())),
          "output.json": Mount(Inline(output.encode())),
      },
      grader=SweBenchProGrader(),
  )


def test_run_stages_entryscript_and_grades(tmp_path: Path):
  sandbox = _fake(tmp_path)
  result, verdict = run_unit_test(
      sandbox,
      _unit_test_spec(["a", "b"], ["a", "b"]),
      output_dir=tmp_path / "out",
  )
  # the eval script is run as entryscript.sh (a workspace file, by name)
  assert sandbox.scripts == [ENTRYSCRIPT_NAME]
  assert result.status is RunStatus.SUCCESS
  assert isinstance(verdict, SweBenchProVerdict)
  assert verdict.resolved is True
  assert verdict.score == 1.0


def test_run_partial_pass_not_resolved(tmp_path: Path):
  _, verdict = run_unit_test(
      _fake(tmp_path),
      _unit_test_spec(["a", "b"], ["a"]),
      output_dir=tmp_path / "out",
  )
  assert verdict is not None
  assert verdict.resolved is False


def test_grader_runs_even_when_body_exec_fails(tmp_path: Path):
  # a nonzero entryscript still lets before_destroy grade (task-02 semantics)
  result, verdict = run_unit_test(
      _fake(tmp_path, run_results=[ExecResult(1, "", "boom")]),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
  )
  assert result.status is RunStatus.SUCCESS  # body did not raise; it returned 1
  assert verdict is not None
  assert verdict.resolved is True  # graded from the staged output


def test_setup_failure_is_captured_not_raised(tmp_path: Path):
  result, verdict = run_unit_test(
      _fake(tmp_path, up_error=SandboxError("no docker")),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
  )
  assert result.status is RunStatus.SETUP_ERROR
  assert isinstance(result.error, SandboxError)
  assert verdict is None  # grading never ran
