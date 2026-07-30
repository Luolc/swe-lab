"""Tests for run_unit_test: the composition on an injected FakeSandbox.

`run_unit_test` takes the sandbox by **injection**, so a test just constructs a
:class:`FakeSandbox` (real local-dir file ops, scripted exec, no Docker) and
passes it — no backend registry, no patching a construction function.
"""

from dataclasses import replace
import json
from pathlib import Path
from typing import final, override

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
    SandboxFs,
    SandboxObserver,
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


def _spec_with_outputs(
    required: list[str], passed: list[str]
) -> UnitTestSpec[SweBenchProVerdict]:
  spec = _unit_test_spec(required, passed)
  return replace(
      spec, native_outputs={"output.json": "output.json", "logs": "stdout.log"}
  )


def test_registers_the_entryscript_and_the_datasets_outputs(tmp_path: Path):
  # Observability: what explains a grade after the fact — the script that ran
  # and the files it produced — is registered, namespaced by the method.
  sandbox = _fake(tmp_path)
  _ = (sandbox.workspace).mkdir(parents=True, exist_ok=True)
  _ = (sandbox.workspace / "stdout.log").write_text("test output")
  result, _ = run_unit_test(
      sandbox, _spec_with_outputs(["a"], ["a"]), output_dir=tmp_path / "out"
  )
  assert "eval.entryscript.sh" in result.artifacts
  assert "eval.output.json" in result.artifacts  # staged by the spec's mounts
  assert "eval.logs" in result.artifacts


def test_absent_outputs_are_skipped_best_effort(tmp_path: Path):
  # A run that died mid-script registers fewer files, never a broken reference.
  result, _ = run_unit_test(
      _fake(tmp_path),
      _spec_with_outputs(["a"], ["a"]),
      output_dir=tmp_path / "o",
  )
  assert "eval.logs" not in result.artifacts  # stdout.log never written
  assert "eval.entryscript.sh" in result.artifacts  # this one did land


def test_metrics_carry_the_verdict_and_the_execution(tmp_path: Path):
  result, _ = run_unit_test(
      _fake(tmp_path, run_results=[ExecResult(3, "", "boom")]),
      _unit_test_spec(["a", "b"], ["a"]),
      output_dir=tmp_path / "out",
  )
  m = result.metrics
  assert m["eval.score"] == 0.0 and m["eval.resolved"] == 0.0
  assert m["eval.passed"] == 1.0  # one test reported PASSED
  assert m["eval.missing"] == 1.0  # of two required
  assert m["eval.required"] == 2.0
  assert m["eval.exit_code"] == 3.0  # the entryscript's own outcome, kept
  assert m["eval.timed_out"] == 0.0
  assert m["eval.wall_seconds"] >= 0.0


def test_a_timed_out_eval_is_reported_as_timeout(tmp_path: Path):
  # Nothing raises on a timeout, so the engine assembles SUCCESS; the method
  # knows better. Without this a killed eval looked like one that produced
  # nothing.
  result, _ = run_unit_test(
      _fake(tmp_path, run_results=[ExecResult(124, "", "", timed_out=True)]),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
  )
  assert result.status is RunStatus.TIMEOUT
  assert result.metrics["eval.timed_out"] == 1.0


def test_eval_env_reaches_the_entryscript(tmp_path: Path):
  # Mirrors run_rollout's agent_env: extra env for the thing being run.
  sandbox = _fake(tmp_path)
  _, _ = run_unit_test(
      sandbox,
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
      eval_env={"MY_FLAG": "1"},
  )
  assert sandbox.script_envs == [{"MY_FLAG": "1"}]


def test_extra_observers_run_after_the_methods_own(tmp_path: Path):
  # Composed after the eval-parse observer, so an injected observer (e.g. a
  # persist observer) sees the run once the method has post-processed.
  seen: list[str] = []

  @final
  class _Probe(SandboxObserver):

    @override
    def before_destroy(self, sb: SandboxFs) -> None:
      del sb
      seen.append("probe")

  _, verdict = run_unit_test(
      _fake(tmp_path),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
      observers=[_Probe()],
  )
  assert seen == ["probe"]  # it ran
  assert verdict is not None and verdict.resolved is True  # grading unaffected


def test_setup_failure_is_captured_not_raised(tmp_path: Path):
  result, verdict = run_unit_test(
      _fake(tmp_path, up_error=SandboxError("no docker")),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
  )
  assert result.status is RunStatus.SETUP_ERROR
  assert isinstance(result.error, SandboxError)
  assert verdict is None  # grading never ran
