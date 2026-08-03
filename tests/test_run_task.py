"""Tests for the per-task orchestrator: resume, retry, persistence, marker.

``run_task`` is exercised over ``FakeSandbox`` factories and a real
``FilesystemStore`` (overwrite semantics matter here: a dead process's
attempts are overwritten from ``a0``). The hooks' contracts — validity
decides the marker, retry-desire does not — get their named tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    Sandbox,
    SandboxError,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
    Store,
)
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.workflow import (
    read_marker,
    run_task,
    Task,
    TaskAddress,
    TaskOutcome,
    TaskResult,
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
      patch: str | None,
      checkout_golden_tests: bool = True,
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

  instance: TaskInstance[SweBenchProVerdict]
  produce_from: int = 0
  retry_even_when_valid_until: int = 0
  executions: int = field(default=0, init=False)

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    return (_MaybeProduce(produce=self.executions >= self.produce_from),)

  @override
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    self.executions += 1
    return sb.run_script("main.sh", timeout=timeout)

  @override
  def should_retry(self, result: TaskResult) -> bool:
    return (
        super().should_retry(result)
        or self.executions < self.retry_even_when_valid_until
    )


def _factory(tmp_path: Path, *, up_errors: int = 0):
  """Return a fresh-workspace FakeSandbox factory; first N calls fail up."""
  built: list[FakeSandbox] = []

  def build() -> Sandbox:
    sandbox = FakeSandbox(
        spec=SPEC,
        workspace=epath.Path(tmp_path / f"ws{len(built)}"),
        up_error=(
            SandboxError("infra down") if len(built) < up_errors else None
        ),
    )
    built.append(sandbox)
    return sandbox

  build.built = built  # pyright: ignore[reportFunctionMemberAccess]
  return build


def _store(tmp_path: Path) -> Store:
  return FilesystemStore(epath.Path(tmp_path / "store"))


def _run(
    tmp_path: Path,
    task: Task,
    *,
    store: Store | None = None,
    retries: int = 0,
    up_errors: int = 0,
):
  store = store if store is not None else _store(tmp_path)
  factory = _factory(tmp_path, up_errors=up_errors)
  outcome = run_task(
      task,
      store=store,
      address=ADDRESS,
      sandbox_factory=factory,
      output_dir=tmp_path / "out",
      timeout=10.0,
      retries=retries,
      run_ts="ts-0",
      backend="fake",
  )
  return outcome, store, factory


def test_a_clean_run_persists_the_attempt_and_marks_succeeded(tmp_path: Path):
  task = _FlakyProducer(instance=_Instance())
  outcome, store, factory = _run(tmp_path, task)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert (outcome.resumed, outcome.attempts) == (False, 1)
  # the attempt's artifact landed under the task-keyed prefix
  assert outcome.record is not None
  assert outcome.record.artifacts["out.txt"] == (
      "sw/acme__widget-1/r0/probe/a0/out.txt"
  )
  assert store.get_bytes("sw/acme__widget-1/r0/probe/a0/out.txt") == b"OUT"
  assert len(factory.built) == 1  # pyright: ignore[reportFunctionMemberAccess]
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.SUCCEEDED
  assert marker.attempts == 1


def test_a_missing_required_output_fails_the_attempt(tmp_path: Path):
  # The named invariant: ArtifactSchema.required is the gate, not advisory.
  task = _FlakyProducer(instance=_Instance(), produce_from=99)
  outcome, store, _ = _run(tmp_path, task)
  assert outcome.outcome is TaskOutcome.FAILED
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.FAILED


def test_an_invalid_attempt_retries_in_a_fresh_sandbox(tmp_path: Path):
  task = _FlakyProducer(instance=_Instance(), produce_from=1)
  outcome, store, factory = _run(tmp_path, task, retries=1)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert outcome.attempts == 2
  built = factory.built  # pyright: ignore[reportFunctionMemberAccess]
  assert len(built) == 2  # a fresh sandbox per attempt
  assert built[0].workspace != built[1].workspace
  # every attempt persisted, the failing one included — it is the evidence
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert [(s.attempt, s.extra["outputs_valid"]) for s in shards] == [
      (0, False),
      (1, True),
  ]


def test_infra_failure_spends_the_same_budget(tmp_path: Path):
  # A sandbox that cannot come up is just an invalid attempt (ADR-0007 §6).
  task = _FlakyProducer(instance=_Instance())
  outcome, store, _ = _run(tmp_path, task, retries=1, up_errors=1)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  assert outcome.attempts == 2
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert shards[0].status == "setup_error"
  assert shards[1].status == "success"


def test_budget_exhaustion_is_terminal_failure_not_absence(tmp_path: Path):
  task = _FlakyProducer(instance=_Instance(), produce_from=99)
  outcome, store, factory = _run(tmp_path, task, retries=2)
  assert outcome.outcome is TaskOutcome.FAILED
  assert outcome.attempts == 3
  assert len(factory.built) == 3  # pyright: ignore[reportFunctionMemberAccess]
  marker = read_marker(store, ADDRESS, "acme__widget-1")
  assert marker is not None and marker.outcome is TaskOutcome.FAILED


def test_retry_desire_is_not_failure(tmp_path: Path):
  # The eval shape: every attempt is VALID, but the task keeps asking for
  # another (flake absorption). The budget bounds it and the marker still
  # says succeeded — retry-desire never turns into failure.
  task = _FlakyProducer(instance=_Instance(), retry_even_when_valid_until=99)
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
  task = _FlakyProducer(instance=_Instance())
  outcome, _, _ = _run(tmp_path, task, store=store)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  # exactly one put_bytes — the marker — and by then the shard exists, so a
  # crash at any earlier point leaves the task unmarked and re-runnable
  assert store.log == ["sw/acme__widget-1/r0/probe/complete.json"]
  assert store.read_manifest("sw", "acme__widget-1", 0, task="probe")


def test_resume_skips_a_succeeded_task_entirely(tmp_path: Path):
  task = _FlakyProducer(instance=_Instance())
  first, store, _ = _run(tmp_path, task)
  assert first.outcome is TaskOutcome.SUCCEEDED

  # a later process re-enters: no sandbox is built, the record is read back
  def exploding_factory() -> Sandbox:
    raise AssertionError("resume must not build a sandbox")

  resumed = run_task(
      _FlakyProducer(instance=_Instance()),
      store=store,
      address=ADDRESS,
      sandbox_factory=exploding_factory,
      output_dir=tmp_path / "out2",
      timeout=10.0,
      run_ts="ts-1",
  )
  assert resumed.resumed is True
  assert resumed.outcome is TaskOutcome.SUCCEEDED
  assert resumed.record is not None
  assert resumed.record.artifacts["out.txt"] == (
      "sw/acme__widget-1/r0/probe/a0/out.txt"
  )
  assert resumed.result is None  # only the store survives a process


def test_resume_never_reruns_a_terminally_failed_task(tmp_path: Path):
  task = _FlakyProducer(instance=_Instance(), produce_from=99)
  first, store, _ = _run(tmp_path, task)
  assert first.outcome is TaskOutcome.FAILED

  def exploding_factory() -> Sandbox:
    raise AssertionError("a terminal failure must not burn budget again")

  resumed = run_task(
      _FlakyProducer(instance=_Instance(), produce_from=99),
      store=store,
      address=ADDRESS,
      sandbox_factory=exploding_factory,
      output_dir=tmp_path / "out2",
      timeout=10.0,
      retries=5,
      run_ts="ts-1",
  )
  assert resumed.resumed is True
  assert resumed.outcome is TaskOutcome.FAILED


def test_dead_attempts_without_a_marker_are_overwritten(tmp_path: Path):
  # A preempted process left a shard but no marker: the task is not terminal,
  # and the re-run starts from a0 — deterministic overwrite, not resumption.
  store = _store(tmp_path)
  ghost = _FlakyProducer(instance=_Instance())
  outcome, _, _ = _run(tmp_path, ghost, store=store)
  assert outcome.outcome is TaskOutcome.SUCCEEDED
  # simulate the preemption: strip the marker, keep the attempt
  import pathlib

  marker_path = pathlib.Path(
      str(tmp_path / "store" / "sw/acme__widget-1/r0/probe/complete.json")
  )
  marker_path.unlink()
  fresh = run_task(
      _FlakyProducer(instance=_Instance()),
      store=store,
      address=ADDRESS,
      sandbox_factory=_factory(tmp_path / "second", up_errors=0),
      output_dir=tmp_path / "out2",
      timeout=10.0,
      run_ts="ts-1",
  )
  assert fresh.resumed is False  # ran again from scratch
  shards = store.read_manifest("sw", "acme__widget-1", 0, task="probe")
  assert [s.run_ts for s in shards] == ["ts-1"]  # a0 overwritten, not a1


def test_a_negative_budget_is_refused(tmp_path: Path):
  with pytest.raises(ValueError, match="retries"):
    _ = run_task(
        _FlakyProducer(instance=_Instance()),
        store=_store(tmp_path),
        address=ADDRESS,
        sandbox_factory=_factory(tmp_path),
        output_dir=tmp_path / "out",
        timeout=10.0,
        retries=-1,
        run_ts="ts-0",
    )


def test_a_malformed_task_key_is_refused():
  with pytest.raises(ValueError, match="task key"):
    _ = TaskAddress(sweep_id="sw", rollout_id=0, task="Not/AKey")
