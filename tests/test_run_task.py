"""Tests for the per-task orchestrator: resume, retry, persistence, marker.

``run_task`` is exercised over ``FakeSandbox`` factories and a real
``FilesystemStore`` (overwrite semantics matter here: a dead process's
attempts are overwritten from ``a0``). The hooks' contracts — validity
decides the marker, retry-desire does not — get their named tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import pathlib
from pathlib import Path
from typing import final, override

from etils import epath
import pytest

from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import SweBenchProVerdict
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    ExecResult,
    FilesystemStore,
    SandboxError,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
    Store,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.testing import FakeSandboxConfig
from swe_lab.workflow import (
    AttemptResult,
    read_marker,
    run_task,
    Task,
    TaskAddress,
    TaskOutcome,
    TerminalMarker,
)

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")
ADDRESS = TaskAddress(sweep_id="sw", rollout_id=0, task="probe")


@final
@dataclass(frozen=True)
class _Instance(TaskInstance[SweBenchProVerdict]):
  instance_id: str = "acme__widget-1"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return None

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    raise NotImplementedError


@final
@dataclass
class _MaybeProduce(SandboxObserver):
  """Declares ``out.txt`` (required) and produces it only when told to."""

  produce: bool

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    return (ArtifactSchema("out.txt", description="the deliverable"),)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    del sb
    if self.produce:
      return Contribution(inline_artifacts={"out.txt": b"OUT"})
    return None


@final
@dataclass
class _FlakyProducer(Task):
  """Produces its required output only from the Nth execution on.

  ``executions`` counts sequential re-executions (the task-level attempts) —
  the task is the only thing that survives across attempts, so the flake
  state lives on it.
  """

  produce_from: int = 0
  retry_even_when_valid_until: int = 0
  executions: int = field(default=0, init=False)

  @override
  def observers(
      self, instance: TaskInstance[SweBenchProVerdict]
  ) -> tuple[SandboxObserver, ...]:
    del instance
    return (_MaybeProduce(produce=self.executions >= self.produce_from),)

  @override
  def action(
      self,
      sb: SandboxFs,
      instance: TaskInstance[SweBenchProVerdict],
      *,
      timeout: float,
  ) -> ExecResult:
    del instance
    self.executions += 1
    return sb.run_script("main.sh", timeout=timeout)

  @override
  def should_retry(self, result: AttemptResult) -> bool:
    return (
        super().should_retry(result)
        or self.executions < self.retry_even_when_valid_until
    )


def _store(tmp_path: Path) -> Store:
  return FilesystemStore(epath.Path(tmp_path / "store"))


def _run(
    tmp_path: Path,
    task: Task,
    *,
    store: Store | None = None,
    retries: int = 0,
    up_errors: int = 0,
    run_results: tuple[ExecResult, ...] = (),
):
  store = store if store is not None else _store(tmp_path)
  config = FakeSandboxConfig(up_errors=up_errors, run_results=run_results)
  outcome = run_task(
      task,
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=config,
      output_dir=tmp_path / "out",
      timeout=10.0,
      retries=retries,
      run_ts="ts-0",
  )
  return outcome, store, config


def test_a_clean_run_persists_the_attempt_and_marks_succeeded(tmp_path: Path):
  task = _FlakyProducer()
  outcome, store, config = _run(tmp_path, task)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert (outcome.resumed, outcome.attempts) == (False, 1)
  # the attempt's artifact landed under the task-keyed prefix
  assert outcome.record is not None
  assert outcome.record.artifact_keys["out.txt"] == (
      "sw/acme__widget-1/r0/probe/a0/out.txt"
  )
  assert store.get_bytes("sw/acme__widget-1/r0/probe/a0/out.txt") == b"OUT"
  assert len(config.built) == 1
  # This layer stamps no model on the record. A model is a per-*task* fact —
  # one entry runs an agent, the next grades and has none — so a value carried
  # at this level could only be right for some of the tasks it labels. Guards
  # against a workflow-wide `model` coming back.
  assert outcome.record.model == ""
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.SUCCEEDED
  assert marker.attempts == 1


def test_a_missing_required_output_fails_the_attempt(tmp_path: Path):
  # The named invariant: ArtifactSchema.required is the gate, not advisory.
  task = _FlakyProducer(produce_from=99)
  outcome, store, _ = _run(tmp_path, task)
  assert outcome.outcome is TaskOutcome.FAILED
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.FAILED


def test_an_invalid_attempt_retries_in_a_fresh_sandbox(tmp_path: Path):
  task = _FlakyProducer(produce_from=1)
  outcome, store, config = _run(tmp_path, task, retries=1)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert outcome.attempts == 2
  assert len(config.built) == 2  # a fresh sandbox per attempt
  assert config.built[0].workspace != config.built[1].workspace
  # every attempt persisted, the failing one included — it is the evidence
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert [(s.attempt, s.extra["outputs_valid"]) for s in shards] == [
      (0, False),
      (1, True),
  ]


def test_infra_failure_spends_the_same_budget(tmp_path: Path):
  # A sandbox that cannot come up is just an invalid attempt (ADR-0007 §6).
  task = _FlakyProducer()
  outcome, store, _ = _run(tmp_path, task, retries=1, up_errors=1)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert outcome.attempts == 2
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert shards[0].status == "setup_error"
  # the engine error is IN the shard — a failed attempt must be debuggable
  # from the store alone, not just labeled
  assert "infra down" in str(shards[0].extra["error"])
  assert shards[1].status == "success"
  assert "error" not in shards[1].extra


def test_budget_exhaustion_is_terminal_failure_not_absence(tmp_path: Path):
  task = _FlakyProducer(produce_from=99)
  outcome, store, config = _run(tmp_path, task, retries=2)
  assert outcome.outcome is TaskOutcome.FAILED
  assert outcome.attempts == 3
  assert len(config.built) == 3
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.FAILED


def test_retry_desire_is_not_failure(tmp_path: Path):
  # The eval shape: every attempt is VALID, but the task keeps asking for
  # another (flake absorption). The budget bounds it and the marker still
  # says succeeded — retry-desire never turns into failure.
  task = _FlakyProducer(retry_even_when_valid_until=99)
  outcome, store, _ = _run(tmp_path, task, retries=1)
  assert outcome.attempts == 2  # budget spent on the extra desire
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.SUCCEEDED


@dataclass(frozen=True)  # no slots: a slots dataclass breaks zero-arg super()
class _SpyStore(FilesystemStore):
  """Records every ``put_bytes`` key, in order."""

  log: list[str] = field(default_factory=list)

  @override
  def put_bytes(self, key: str, data: bytes) -> None:
    self.log.append(key)
    super().put_bytes(key, data)


def test_the_marker_is_written_last(tmp_path: Path):
  store = _SpyStore(epath.Path(tmp_path / "store"))
  task = _FlakyProducer()
  outcome, _, _ = _run(tmp_path, task, store=store)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  # exactly one put_bytes — the marker — and by then the shard exists, so a
  # crash at any earlier point leaves the task unmarked and re-runnable
  assert store.log == ["sw/acme__widget-1/r0/probe/complete.json"]
  assert store.read_manifest("sw", "acme__widget-1", 0, task="probe")


def test_resume_skips_a_succeeded_task_entirely(tmp_path: Path):
  task = _FlakyProducer()
  first, store, _ = _run(tmp_path, task)
  assert first.outcome is TaskOutcome.SUCCEEDED

  # a later process re-enters: no sandbox is built, the record is read back
  config = FakeSandboxConfig()
  resumed = run_task(
      _FlakyProducer(),
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=config,
      output_dir=tmp_path / "out2",
      timeout=10.0,
      run_ts="ts-1",
  )
  assert config.built == []  # resume never pays for a container
  assert resumed.resumed is True
  assert resumed.outcome is TaskOutcome.SUCCEEDED
  assert resumed.record is not None
  assert resumed.record.artifact_keys["out.txt"] == (
      "sw/acme__widget-1/r0/probe/a0/out.txt"
  )
  assert resumed.result is None  # only the store survives a process


def test_resume_never_reruns_a_terminally_failed_task(tmp_path: Path):
  task = _FlakyProducer(produce_from=99)
  first, store, _ = _run(tmp_path, task)
  assert first.outcome is TaskOutcome.FAILED

  config = FakeSandboxConfig()
  resumed = run_task(
      _FlakyProducer(produce_from=99),
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=config,
      output_dir=tmp_path / "out2",
      timeout=10.0,
      retries=5,
      run_ts="ts-1",
  )
  assert config.built == []  # a terminal failure never burns budget again
  assert resumed.resumed is True
  assert resumed.outcome is TaskOutcome.FAILED


def test_dead_attempts_without_a_marker_are_overwritten(tmp_path: Path):
  # A preempted process left a shard but no marker: the task is not terminal,
  # and the re-run starts from a0 — deterministic overwrite, not resumption.
  store = _store(tmp_path)
  ghost = _FlakyProducer()
  outcome, _, _ = _run(tmp_path, ghost, store=store)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  # simulate the preemption: strip the marker, keep the attempt
  import pathlib

  marker_path = pathlib.Path(
      str(tmp_path / "store" / "sw/acme__widget-1/r0/probe/complete.json")
  )
  marker_path.unlink()
  fresh = run_task(
      _FlakyProducer(),
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=FakeSandboxConfig(),
      output_dir=tmp_path / "out2",
      timeout=10.0,
      run_ts="ts-1",
  )
  assert fresh.resumed is False  # ran again from scratch
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert [s.run_ts for s in shards] == ["ts-1"]  # a0 overwritten, not a1


def test_a_shorter_rerun_does_not_leave_a_stale_record_behind(tmp_path: Path):
  # A forced re-run overwrites attempts from a0 and may spend FEWER of them,
  # so the previous run's last attempt outlives it in the store. Resume must
  # hand back the marker's own final attempt — taking the last shard would
  # feed a downstream edge the older run's artifacts while the marker it just
  # trusted says something else.
  store = _store(tmp_path)
  first = run_task(
      _FlakyProducer(produce_from=1),  # fails a0, succeeds a1
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=FakeSandboxConfig(),
      output_dir=tmp_path / "out",
      timeout=10.0,
      retries=1,
      run_ts="ts-0",
  )
  assert first.attempts == 2

  rerun = run_task(
      _FlakyProducer(),  # succeeds at a0 this time
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=FakeSandboxConfig(),
      output_dir=tmp_path / "out2",
      timeout=10.0,
      resume=False,
      run_ts="ts-1",
  )
  assert rerun.attempts == 1
  # the older a1 is still in the store — nothing deletes it
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert [(s.attempt, s.run_ts) for s in shards] == [(0, "ts-1"), (1, "ts-0")]

  resumed = run_task(
      _FlakyProducer(),
      _Instance(),
      store=store,
      address=ADDRESS,
      sandbox=FakeSandboxConfig(),
      output_dir=tmp_path / "out3",
      timeout=10.0,
      run_ts="ts-2",
  )
  assert resumed.resumed is True
  assert resumed.attempts == 1
  assert resumed.record is not None
  assert (resumed.record.attempt, resumed.record.run_ts) == (0, "ts-1")


def _marker_key(instance_id: str = "acme__widget-1") -> str:
  return f"{ADDRESS.prefix(instance_id)}/complete.json"


def test_a_marker_whose_shard_is_gone_is_refused_not_believed(tmp_path: Path):
  # The marker is written last, after the shard is durable, so a marker with
  # no shard behind it is a state this system cannot reach. Resume skips the
  # work entirely on the marker's word, so it verifies the one thing that word
  # rests on — otherwise a task reports success with no evidence at all.
  store = _store(tmp_path)
  first, _, _ = _run(tmp_path, _FlakyProducer(), store=store)
  assert first.outcome is TaskOutcome.SUCCEEDED
  shard = (
      pathlib.Path(str(tmp_path / "store"))
      / "sw/acme__widget-1/r0/probe/a0/run.json"
  )
  shard.unlink()

  with pytest.raises(SandboxError, match="no shard matches"):
    _ = run_task(
        _FlakyProducer(),
        _Instance(),
        store=store,
        address=ADDRESS,
        sandbox=FakeSandboxConfig(),
        output_dir=tmp_path / "out2",
        timeout=10.0,
        run_ts="ts-1",
    )


def test_a_shard_from_another_run_does_not_satisfy_the_marker(tmp_path: Path):
  # Right attempt index, wrong run: the shard is evidence of a *different*
  # execution, and a resume that accepted it would report this run's outcome
  # over that run's artifacts.
  store = _store(tmp_path)
  first, _, _ = _run(tmp_path, _FlakyProducer(), store=store)
  assert first.outcome is TaskOutcome.SUCCEEDED
  store.put_bytes(
      _marker_key(),
      TerminalMarker(
          outcome=TaskOutcome.SUCCEEDED, attempts=1, run_ts="ts-elsewhere"
      )
      .to_json()
      .encode("utf-8"),
  )

  with pytest.raises(SandboxError, match="no shard matches"):
    _ = run_task(
        _FlakyProducer(),
        _Instance(),
        store=store,
        address=ADDRESS,
        sandbox=FakeSandboxConfig(),
        output_dir=tmp_path / "out2",
        timeout=10.0,
        run_ts="ts-1",
    )


def test_a_negative_budget_is_refused(tmp_path: Path):
  with pytest.raises(ValueError, match="retries"):
    _ = run_task(
        _FlakyProducer(),
        _Instance(),
        store=_store(tmp_path),
        address=ADDRESS,
        sandbox=FakeSandboxConfig(),
        output_dir=tmp_path / "out",
        timeout=10.0,
        retries=-1,
        run_ts="ts-0",
    )


def test_a_malformed_task_key_is_refused():
  with pytest.raises(ValueError, match="task key"):
    _ = TaskAddress(sweep_id="sw", rollout_id=0, task="Not/AKey")


def test_a_timed_out_attempt_never_spends_the_retry_budget(tmp_path: Path):
  # The runner's gate (ADR-0011): wall-clock is a budget, so a killed attempt
  # is a result and not an infrastructure fault. The task here asks for a
  # retry on every attempt — it is refused anyway, and the run costs ONE
  # container instead of two.
  task = _FlakyProducer(retry_even_when_valid_until=99)
  killed = (ExecResult(124, "", "", timed_out=True),)
  outcome, store, config = _run(tmp_path, task, retries=1, run_results=killed)
  assert outcome.attempts == 1
  assert len(config.built) == 1
  assert outcome.outcome is TaskOutcome.FAILED  # still an invalid attempt
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert [(s.attempt, s.status) for s in shards] == [(0, "timeout")]


def test_retry_on_timeout_lets_the_runner_spend_it(tmp_path: Path):
  # Opting in is a per-run decision, and it is the whole difference.
  task = _FlakyProducer(retry_on_timeout=True)
  killed = (ExecResult(124, "", "", timed_out=True),)
  outcome, _, config = _run(tmp_path, task, retries=1, run_results=killed)
  assert outcome.attempts == 2
  assert len(config.built) == 2
