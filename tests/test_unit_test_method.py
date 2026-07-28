"""Tests for run_unit_test: the composition on a fake backend (no Docker).

``run_unit_test`` now selects its sandbox by a registered *name* (not a backend
object), so these tests register a throwaway backend name whose factory builds
(and records) a :class:`FakeSandbox` — real local-dir file ops, scripted exec,
no Docker. That keeps every test's original intent while going through the real
``run_unit_test`` composition and ``build_sandbox`` seam.
"""

import json
from pathlib import Path

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
    register_sandbox,
    RunStatus,
    SandboxError,
    SandboxSpec,
)
from swe_lab.sandbox.backends import SandboxConfig
from swe_lab.sandbox.testing import FakeSandbox

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def _register_fake(
    name: str,
    *,
    run_results: list[ExecResult] | None = None,
    up_error: Exception | None = None,
) -> list[FakeSandbox]:
  """Register a backend name that builds and records a FakeSandbox.

  Returns the (initially empty) list the built sandbox is appended to, so a
  test can read back what the run drove (e.g. ``.scripts``).
  """
  built: list[FakeSandbox] = []

  def factory(
      spec: SandboxSpec, workspace: Path, config: SandboxConfig
  ) -> FakeSandbox:
    del config
    sandbox = FakeSandbox(
        spec=spec,
        workspace=workspace,
        run_results=list(run_results or []),
        up_error=up_error,
    )
    built.append(sandbox)
    return sandbox

  register_sandbox(name, factory)
  return built


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
  built = _register_fake("fake-stages")
  result, verdict = run_unit_test(
      SPEC,
      _unit_test_spec(["a", "b"], ["a", "b"]),
      backend="fake-stages",
      workspace=tmp_path / "ws",
  )
  # the eval script is run as entryscript.sh (a workspace file, by name)
  assert built[0].scripts == [ENTRYSCRIPT_NAME]
  assert result.status is RunStatus.SUCCESS
  assert isinstance(verdict, SweBenchProVerdict)
  assert verdict.resolved is True
  assert verdict.score == 1.0


def test_run_partial_pass_not_resolved(tmp_path: Path):
  _register_fake("fake-partial")
  _, verdict = run_unit_test(
      SPEC,
      _unit_test_spec(["a", "b"], ["a"]),
      backend="fake-partial",
      workspace=tmp_path / "ws",
  )
  assert verdict is not None
  assert verdict.resolved is False


def test_grader_runs_even_when_body_exec_fails(tmp_path: Path):
  # a nonzero entryscript still lets before_destroy grade (task-02 semantics)
  _register_fake("fake-bodyfail", run_results=[ExecResult(1, "", "boom")])
  result, verdict = run_unit_test(
      SPEC,
      _unit_test_spec(["a"], ["a"]),
      backend="fake-bodyfail",
      workspace=tmp_path / "ws",
  )
  assert result.status is RunStatus.SUCCESS  # body did not raise; it returned 1
  assert verdict is not None
  assert verdict.resolved is True  # graded from the staged output


def test_setup_failure_is_captured_not_raised(tmp_path: Path):
  _register_fake("fake-setupfail", up_error=SandboxError("no docker"))
  result, verdict = run_unit_test(
      SPEC,
      _unit_test_spec(["a"], ["a"]),
      backend="fake-setupfail",
      workspace=tmp_path / "ws",
  )
  assert result.status is RunStatus.SETUP_ERROR
  assert isinstance(result.error, SandboxError)
  assert verdict is None  # grading never ran
