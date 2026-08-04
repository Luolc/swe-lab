"""Tests for UnitTestTask: the composition on an injected FakeSandbox.

``Task.execute`` takes the sandbox by **injection**, so a test just constructs
a :class:`FakeSandbox` (real local-dir file ops, scripted exec, no Docker) and
passes it — no backend registry, no patching a construction function. The
instance is injected the same way: a fake one serves a compiled spec back, the
way a dataset record would.

The last section pins where flake absorption went (ADR-0008): the in-run retry
loop is gone, and the same question is answered one level up, by ``run_task``
spending its budget on a fresh sandbox and persisting every attempt.
"""

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import final, override

from etils import epath
import pytest

from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.unit_test import (
    ENTRYSCRIPT_NAME,
    gold_patch,
    UnitTestTask,
    verdict_of,
)
from swe_lab.evaluation.verdict import Grader, UnitTestSpec
from swe_lab.sandbox import (
    Contribution,
    ExecResult,
    FilesystemStore,
    Inline,
    Mount,
    RunStatus,
    SandboxError,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.testing import FakeSandbox, FakeSandboxConfig
from swe_lab.workflow import (
    AttemptResult,
    run_task,
    TaskAddress,
    TaskOutcome,
    TaskRunOutcome,
)

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


@final
@dataclass(frozen=True)
class _Instance(TaskInstance[SweBenchProVerdict]):
  """Serves a precompiled spec back, the way a dataset record would."""

  spec: UnitTestSpec[SweBenchProVerdict]
  instance_id: str = "acme__widget-1"
  gold: str | None = None

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return self.gold

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    del apply_patch, checkout_golden_tests
    return replace(self.spec, patch_name=patch_name)


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


def _grade(
    sandbox: FakeSandbox,
    spec: UnitTestSpec[SweBenchProVerdict],
    *,
    output_dir: Path,
    gold: str | None = None,
    **kwargs: object,
) -> AttemptResult:
  """Run the eval task over a caller-supplied patch (the standalone shape)."""
  task: UnitTestTask[SweBenchProVerdict] = UnitTestTask()
  return task.execute(
      sandbox,
      _Instance(spec=spec, gold=gold),
      output_dir=output_dir,
      timeout=60.0,
      extra_mounts={PATCH_NAME: Mount(Inline(b"CANDIDATE"))},
      **kwargs,  # pyright: ignore[reportArgumentType]
  )


def test_run_stages_entryscript_and_grades(tmp_path: Path):
  sandbox = _fake(tmp_path)
  result = _grade(
      sandbox,
      _unit_test_spec(["a", "b"], ["a", "b"]),
      output_dir=tmp_path / "o",
  )
  # the eval script is run as entryscript.sh (a workspace file, by name)
  assert sandbox.scripts == [ENTRYSCRIPT_NAME]
  assert result.run.status is RunStatus.SUCCESS
  verdict = verdict_of(result)
  assert isinstance(verdict, SweBenchProVerdict)
  assert verdict.resolved is True
  assert verdict.score == 1.0


def test_run_partial_pass_not_resolved(tmp_path: Path):
  result = _grade(
      _fake(tmp_path), _unit_test_spec(["a", "b"], ["a"]), output_dir=tmp_path
  )
  verdict = verdict_of(result)
  assert verdict is not None and verdict.resolved is False


def test_grader_runs_even_when_body_exec_fails(tmp_path: Path):
  # a nonzero entryscript still lets before_destroy grade (task-02 semantics)
  result = _grade(
      _fake(tmp_path, run_results=[ExecResult(1, "", "boom")]),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "o",
  )
  # the body did not raise; it returned 1
  assert result.run.status is RunStatus.SUCCESS
  verdict = verdict_of(result)
  assert verdict is not None and verdict.resolved is True


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
  sandbox.workspace.mkdir(parents=True, exist_ok=True)
  _ = (sandbox.workspace / "stdout.log").write_text("test output")
  result = _grade(
      sandbox, _spec_with_outputs(["a"], ["a"]), output_dir=tmp_path / "o"
  )
  assert "unit_test.entryscript.sh" in result.run.artifacts
  # staged by the spec's mounts
  assert "unit_test.output.json" in result.run.artifacts
  assert "unit_test.logs" in result.run.artifacts


def test_absent_outputs_are_skipped_best_effort(tmp_path: Path):
  # A run that died mid-script registers fewer files, never a broken reference.
  result = _grade(
      _fake(tmp_path),
      _spec_with_outputs(["a"], ["a"]),
      output_dir=tmp_path / "o",
  )
  assert (
      "unit_test.logs" not in result.run.artifacts
  )  # stdout.log never written
  assert "unit_test.entryscript.sh" in result.run.artifacts  # this one did land


def test_metrics_carry_the_verdict_and_the_execution(tmp_path: Path):
  result = _grade(
      _fake(tmp_path, run_results=[ExecResult(3, "", "boom")]),
      _unit_test_spec(["a", "b"], ["a"]),
      output_dir=tmp_path / "o",
  )
  m = result.run.metrics
  assert m["unit_test.score"] == 0.0 and m["unit_test.resolved"] == 0.0
  assert m["unit_test.passed"] == 1.0  # one test reported PASSED
  assert m["unit_test.missing"] == 1.0  # of two required
  assert m["unit_test.required"] == 2.0
  assert m["unit_test.exit_code"] == 3.0  # the entryscript's own outcome, kept
  assert m["unit_test.timed_out"] == 0.0
  assert m["unit_test.wall_seconds"] >= 0.0


def test_a_timed_out_run_is_reported_as_timeout(tmp_path: Path):
  # Nothing raises on a timeout, so the engine assembles SUCCESS; the task
  # knows better. Without this a killed eval looked like one that produced
  # nothing.
  result = _grade(
      _fake(tmp_path, run_results=[ExecResult(124, "", "", timed_out=True)]),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "o",
  )
  assert result.run.status is RunStatus.TIMEOUT
  assert result.run.metrics["unit_test.timed_out"] == 1.0


def test_script_env_reaches_the_entryscript(tmp_path: Path):
  # Mirrors the coding task's agent_env: extra env for the thing being run.
  sandbox = _fake(tmp_path)
  task: UnitTestTask[SweBenchProVerdict] = UnitTestTask(
      script_env={"MY_FLAG": "1"}
  )
  _ = task.execute(
      sandbox,
      _Instance(spec=_unit_test_spec(["a"], ["a"])),
      output_dir=tmp_path / "o",
      timeout=60.0,
      extra_mounts={PATCH_NAME: Mount(Inline(b"CANDIDATE"))},
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

  result = _grade(
      _fake(tmp_path),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "o",
      extra_observers=[_Probe()],
  )
  assert seen == ["probe"]  # it ran
  verdict = verdict_of(result)
  assert verdict is not None and verdict.resolved is True  # grading unaffected


def test_setup_failure_is_captured_not_raised(tmp_path: Path):
  result = _grade(
      _fake(tmp_path, up_error=SandboxError("no docker")),
      _unit_test_spec(["a"], ["a"]),
      output_dir=tmp_path / "o",
  )
  assert result.run.status is RunStatus.SETUP_ERROR
  assert isinstance(result.run.error, SandboxError)
  assert verdict_of(result) is None  # grading never ran


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
  result = _grade(
      sandbox, _unit_test_spec(["a"], ["a"]), output_dir=tmp_path / "o"
  )
  verdict = verdict_of(result)
  assert verdict is not None and verdict.resolved is True
  assert result.run.metrics["sandbox.fake_metric"] == 42.0


# ─── the patch input: three suppliers, one channel ───────────────────────────


def test_the_gold_builder_fills_the_patch_input_itself(tmp_path: Path):
  # The self-check shape: no edge, no caller bytes — the task builds its own
  # input from the instance's reference solution.
  sandbox = _fake(tmp_path)
  task: UnitTestTask[SweBenchProVerdict] = UnitTestTask(
      inputs_builder=gold_patch
  )
  result = task.execute(
      sandbox,
      _Instance(spec=_unit_test_spec(["a"], ["a"]), gold="GOLD DIFF"),
      output_dir=tmp_path / "o",
      timeout=60.0,
  )
  assert result.run.status is RunStatus.SUCCESS
  assert (sandbox.workspace / PATCH_NAME).read_text() == "GOLD DIFF"


def test_the_gold_builder_refuses_an_instance_without_one(tmp_path: Path):
  # Asking to grade a reference solution that does not exist is a caller
  # error, recorded as the attempt's failure rather than graded as unresolved.
  task: UnitTestTask[SweBenchProVerdict] = UnitTestTask(
      inputs_builder=gold_patch
  )
  result = task.execute(
      _fake(tmp_path),
      _Instance(spec=_unit_test_spec(["a"], ["a"]), gold=None),
      output_dir=tmp_path / "o",
      timeout=60.0,
  )
  assert result.run.status is RunStatus.RUN_ERROR
  assert "gold patch" in str(result.run.error)
  assert task.outputs_valid(result) is False


def test_a_custom_patch_name_reaches_the_schema_and_the_spec():
  # The declared input and the compiled script read the same name by
  # construction — the task threads it into both.
  task: UnitTestTask[SweBenchProVerdict] = UnitTestTask(
      patch_name="candidate.diff"
  )
  instance = _Instance(spec=_unit_test_spec(["a"], ["a"]))
  assert [s.name for s in task.input_schema()] == ["candidate.diff"]
  assert (
      instance.unit_test_spec(
          apply_patch=True, patch_name=task.patch_name
      ).patch_name
      == "candidate.diff"
  )


# ─── flake absorption, one level up (ADR-0008) ───────────────────────────────


@dataclass
class _RewritingGrader(Grader[SweBenchProVerdict]):
  """Grades a fail until ``passes_on_attempt``, then a pass — like a flake.

  Counts its own calls, which is exactly how many times the eval has graded:
  once per attempt, since the in-run loop is gone.
  """

  passes_on_attempt: int
  calls: int = field(default=0)

  @override
  def grade(self, sb: SandboxFs) -> SweBenchProVerdict:
    del sb
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


def _flaky_spec(*, passes_on_attempt: int) -> UnitTestSpec[SweBenchProVerdict]:
  """Build a spec that only grades as passing from the Nth grading on."""
  return UnitTestSpec(
      eval_script="echo eval\n",
      mounts={
          REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(["a"]).encode())),
          "output.json": Mount(Inline(json.dumps({"tests": []}).encode())),
      },
      grader=_RewritingGrader(passes_on_attempt),
  )


def _run_eval(
    tmp_path: Path, *, passes_on_attempt: int, retries: int
) -> tuple[TaskRunOutcome, FakeSandboxConfig, FilesystemStore]:
  """Run the eval task through run_task on the fake backend."""
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  config = FakeSandboxConfig()
  outcome = run_task(
      UnitTestTask(),
      _Instance(spec=_flaky_spec(passes_on_attempt=passes_on_attempt)),
      store=store,
      address=TaskAddress(sweep_id="sw", rollout_id=0, task="unit_test"),
      backend="fake",
      sandbox=config,
      output_dir=tmp_path / "out",
      timeout=60.0,
      retries=retries,
      run_ts="ts-0",
      extra_mounts={PATCH_NAME: Mount(Inline(b"CANDIDATE"))},
  )
  return outcome, config, store


def test_an_unresolved_verdict_spends_the_task_budget(tmp_path: Path):
  # The headline behaviour ADR-0005 used to get from an in-run loop: same
  # patch, another attempt — now a *fresh sandbox*, and both attempts are
  # persisted separately, which the warm-container loop could never do.
  outcome, config, store = _run_eval(tmp_path, passes_on_attempt=2, retries=1)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert outcome.attempts == 2
  assert len(config.built) == 2
  assert config.built[0].workspace != config.built[1].workspace
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="unit_test")
  assert [s.attempt for s in shards] == [0, 1]
  # the evidence of the flake: the failing attempt's own grade, kept apart
  assert shards[0].metrics["unit_test.resolved"] == 0.0
  assert shards[1].metrics["unit_test.resolved"] == 1.0


def test_a_first_attempt_pass_is_never_retried(tmp_path: Path):
  outcome, config, _ = _run_eval(tmp_path, passes_on_attempt=1, retries=2)
  assert outcome.attempts == 1
  assert len(config.built) == 1  # budget unspent


def test_failing_every_attempt_is_still_a_succeeded_task(tmp_path: Path):
  # "Unresolved" is an answer, not a failure: the budget bounds the retrying,
  # and the terminal marker reads validity, never retry-desire.
  outcome, config, _ = _run_eval(tmp_path, passes_on_attempt=99, retries=2)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert len(config.built) == 3


def test_a_negative_budget_is_refused(tmp_path: Path):
  with pytest.raises(ValueError, match="retries"):
    _ = _run_eval(tmp_path, passes_on_attempt=1, retries=-1)
