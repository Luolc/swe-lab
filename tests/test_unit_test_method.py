"""Tests for run_unit_test: the composition on an injected FakeSandbox.

`run_unit_test` takes the sandbox by **injection**, so a test just constructs a
:class:`FakeSandbox` (real local-dir file ops, scripted exec, no Docker) and
passes it — no backend registry, no patching a construction function.
"""

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import final, override

from etils import epath
import pytest

from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.methods.unit_test import (
    ENTRYSCRIPT_NAME,
    run_unit_test,
)
from swe_lab.evaluation.verdict import Grader, UnitTestSpec
from swe_lab.sandbox import (
    Contribution,
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


# ─── retry on a failed grade (ADR-0005) ──────────────────────────────────────


def _flaky_spec(*, passes_on_attempt: int) -> UnitTestSpec[SweBenchProVerdict]:
  """Build a spec that only grades as passing from the Nth attempt on.

  The fake sandbox never runs the entryscript, so the flake is simulated in the
  grader rather than in the workspace — see ``_RewritingGrader``.
  """
  return UnitTestSpec(
      eval_script="echo eval\n",
      mounts={
          REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(["a"]).encode())),
          "output.json": Mount(Inline(json.dumps({"tests": []}).encode())),
      },
      grader=_RewritingGrader(passes_on_attempt),
  )


@dataclass
class _RewritingGrader(Grader[SweBenchProVerdict]):
  """Grades a fail until ``passes_on_attempt``, then a pass — like a flake.

  Counts its own calls, which is exactly how many times the composition has
  graded: once per attempt.
  """

  passes_on_attempt: int
  calls: int = 0

  @override
  def grade(self, sb: SandboxFs) -> SweBenchProVerdict:
    self.calls += 1
    passed = (
        frozenset({"a"})
        if self.calls >= self.passes_on_attempt
        else frozenset()
    )
    return SweBenchProVerdict(
        passed=passed,
        missing=frozenset({"a"}) - passed,
        output_state=OutputState.OK,
        required=frozenset({"a"}),
    )


def test_no_retry_runs_the_entryscript_exactly_once(tmp_path: Path):
  # The default path must not silently double every eval.
  sandbox = _fake(tmp_path)
  _, verdict = run_unit_test(
      sandbox,
      _flaky_spec(passes_on_attempt=99),
      output_dir=tmp_path / "out",
      retries=0,
  )
  assert sandbox.scripts == [ENTRYSCRIPT_NAME]
  assert verdict is not None
  assert verdict.attempts == 1
  assert verdict.resolved is False
  assert verdict.flaky is False


def test_a_first_attempt_pass_is_never_retried(tmp_path: Path):
  sandbox = _fake(tmp_path)
  _, verdict = run_unit_test(
      sandbox,
      _flaky_spec(passes_on_attempt=1),
      output_dir=tmp_path / "out",
      retries=2,
  )
  assert sandbox.scripts == [ENTRYSCRIPT_NAME]  # budget unspent
  assert verdict is not None
  assert (verdict.attempts, verdict.resolved, verdict.flaky) == (1, True, False)


def test_a_pass_after_a_retry_is_resolved_and_marked_flaky(tmp_path: Path):
  # The headline behaviour: same patch, second attempt, credited but flagged.
  sandbox = _fake(tmp_path)
  _, verdict = run_unit_test(
      sandbox,
      _flaky_spec(passes_on_attempt=2),
      output_dir=tmp_path / "out",
      retries=1,
  )
  assert sandbox.scripts == [ENTRYSCRIPT_NAME] * 2
  assert verdict is not None
  assert (verdict.attempts, verdict.resolved, verdict.flaky) == (2, True, True)


def test_failing_every_attempt_is_failed_not_flaky(tmp_path: Path):
  # `flaky` is derived, so exhausting the budget can never be mislabelled.
  sandbox = _fake(tmp_path)
  _, verdict = run_unit_test(
      sandbox,
      _flaky_spec(passes_on_attempt=99),
      output_dir=tmp_path / "out",
      retries=2,
  )
  assert sandbox.scripts == [ENTRYSCRIPT_NAME] * 3
  assert verdict is not None
  assert (verdict.attempts, verdict.resolved, verdict.flaky) == (
      3,
      False,
      False,
  )


def test_a_failed_attempt_keeps_its_own_output(tmp_path: Path):
  # A naive retry overwrites output.json — i.e. exactly the evidence of the
  # flake we are trying to collect.
  out = tmp_path / "out"
  spec = UnitTestSpec(
      eval_script="echo eval\n",
      mounts={
          REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(["a"]).encode())),
          "output.json": Mount(Inline(json.dumps({"tests": []}).encode())),
      },
      grader=_RewritingGrader(2),
      native_outputs={"output.json": "output.json"},
  )
  _, verdict = run_unit_test(_fake(tmp_path), spec, output_dir=out, retries=1)
  assert verdict is not None and verdict.flaky is True
  assert (out / "eval.attempt1.output.json").is_file()


def test_negative_retries_is_refused(tmp_path: Path):
  with pytest.raises(ValueError, match="retries"):
    _ = run_unit_test(
        _fake(tmp_path),
        _unit_test_spec(["a"], ["a"]),
        output_dir=tmp_path / "out",
        retries=-1,
    )


def test_a_spec_can_override_the_callers_retry_budget(tmp_path: Path):
  # The consumer may know an instance needs more attempts than the caller's
  # default; the spec is where that knowledge lands (ADR-0005).
  sandbox = _fake(tmp_path)
  spec = replace(_flaky_spec(passes_on_attempt=3), retries=2)
  _, verdict = run_unit_test(
      sandbox, spec, output_dir=tmp_path / "out", retries=0
  )
  assert sandbox.scripts == [ENTRYSCRIPT_NAME] * 3  # spec won, not the caller
  assert verdict is not None
  assert (verdict.attempts, verdict.resolved, verdict.flaky) == (3, True, True)


def test_a_spec_without_an_override_defers_to_the_caller(tmp_path: Path):
  sandbox = _fake(tmp_path)
  _, verdict = run_unit_test(
      sandbox,
      _flaky_spec(passes_on_attempt=2),
      output_dir=tmp_path / "out",
      retries=1,
  )
  assert sandbox.scripts == [ENTRYSCRIPT_NAME] * 2
  assert verdict is not None and verdict.attempts == 2


def test_a_spec_can_disable_retrying_the_caller_asked_for(tmp_path: Path):
  # The override works downwards too — 0 is a value, not "unset".
  sandbox = _fake(tmp_path)
  spec = replace(_flaky_spec(passes_on_attempt=99), retries=0)
  _, _ = run_unit_test(sandbox, spec, output_dir=tmp_path / "out", retries=5)
  assert sandbox.scripts == [ENTRYSCRIPT_NAME]


def test_a_negative_spec_override_is_refused(tmp_path: Path):
  spec = replace(_flaky_spec(passes_on_attempt=1), retries=-1)
  with pytest.raises(ValueError, match="spec retries"):
    _ = run_unit_test(_fake(tmp_path), spec, output_dir=tmp_path / "out")


def test_backend_observers_feed_the_eval_result(tmp_path: Path):
  # ADR-0007 §3: the backend's own observers are composed first, so a
  # backend's runtime metrics land in the same RunResult (and, mechanically,
  # in a persisted record's metrics) with no composition change.
  class _MeteredFake(FakeSandbox):

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      class _Meter(SandboxObserver):

        @override
        def before_destroy(self, sb: SandboxFs) -> Contribution | None:
          del sb
          return Contribution(metrics={"sandbox.fake_metric": 42.0})

      return (_Meter(),)

  sandbox = _MeteredFake(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  result, verdict = run_unit_test(
      sandbox,
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "out",
  )
  assert verdict is not None and verdict.resolved is True
  assert result.metrics["sandbox.fake_metric"] == 42.0
