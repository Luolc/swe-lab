"""Tests for the Workflow: static edge resolution, execution, the record.

The chain is exercised with small generic tasks over ``FakeSandbox``
factories and a real ``FilesystemStore``: a producer whose observer declares
and emits ``thing.txt``, and a consumer whose ``input_schema`` requires it —
the same shape as rollout → eval, without Docker or datasets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
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
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
    Store,
)
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.workflow import (
    EntryStatus,
    read_marker,
    Task,
    TaskAddress,
    Workflow,
    WorkflowEntry,
    WorkflowError,
)

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


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
class _Emit(SandboxObserver):
  """Declares ``name`` and emits the given bytes (nothing when empty)."""

  name: str
  content: bytes

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    return (ArtifactSchema(self.name, description="a produced thing"),)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    del sb
    return Contribution(inline_artifacts={self.name: self.content})


@final
@dataclass
class _Producer(Task):
  """Emits ``produces`` with ``content`` via its observer."""

  instance: TaskInstance[SweBenchProVerdict]
  produces: str = "thing.txt"
  content: bytes = b"THING"

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    return (_Emit(name=self.produces, content=self.content),)

  @override
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    return sb.run_script("main.sh", timeout=timeout)


@final
@dataclass
class _Consumer(Task):
  """Requires ``consumes`` as an input; records what got staged."""

  instance: TaskInstance[SweBenchProVerdict]
  consumes: str = "thing.txt"
  seen: list[bytes] = field(default_factory=list)

  @override
  def input_schema(self) -> tuple[ArtifactSchema, ...]:
    return (ArtifactSchema(self.consumes, description="the upstream thing"),)

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    return (_Emit(name="consumed.txt", content=b"OK"),)

  @override
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    self.seen.append(sb.read(self.consumes))
    return sb.run_script("main.sh", timeout=timeout)


def _factory(base: Path):
  count = 0

  def build() -> Sandbox:
    nonlocal count
    count += 1
    return FakeSandbox(spec=SPEC, workspace=epath.Path(base / f"ws{count}"))

  return build


def _store(tmp_path: Path) -> Store:
  return FilesystemStore(epath.Path(tmp_path / "store"))


def _chain(tmp_path: Path, **consumer_kwargs: object) -> Workflow:
  return Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              _Producer(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "p"),
          ),
          WorkflowEntry(
              "consumer",
              _Consumer(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "c"),
              **consumer_kwargs,  # pyright: ignore[reportArgumentType]
          ),
      ],
  )


# ─── static validation (phase A) ─────────────────────────────────────────────


def test_construction_resolves_the_edge_by_name(tmp_path: Path):
  wf = _chain(tmp_path)
  assert wf._edges == {
      "producer": {},
      "consumer": {"thing.txt": "producer"},
  }


def test_an_unproduced_input_is_refused_at_construction(tmp_path: Path):
  with pytest.raises(WorkflowError, match="no earlier entry produces"):
    _ = Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "consumer",
                _Consumer(instance=_Instance()),
                sandbox_factory=_factory(tmp_path),
            )
        ],
    )


def test_two_producers_of_one_name_demand_an_explicit_binding(tmp_path: Path):
  entries = [
      WorkflowEntry(
          "one",
          _Producer(instance=_Instance()),
          sandbox_factory=_factory(tmp_path / "1"),
      ),
      WorkflowEntry(
          "two",
          _Producer(instance=_Instance()),
          sandbox_factory=_factory(tmp_path / "2"),
      ),
      WorkflowEntry(
          "consumer",
          _Consumer(instance=_Instance()),
          sandbox_factory=_factory(tmp_path / "c"),
      ),
  ]
  with pytest.raises(WorkflowError, match="bind it explicitly"):
    _ = Workflow(
        store=_store(tmp_path), sweep_id="sw", rollout_id=0, entries=entries
    )
  # the one-line fix the error asks for:
  bound = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          entries[0],
          entries[1],
          WorkflowEntry(
              "consumer",
              _Consumer(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "c"),
              inputs=("two/thing.txt",),
          ),
      ],
  )
  assert bound._edges["consumer"] == {"thing.txt": "two"}


def test_bad_bindings_are_refused_at_construction(tmp_path: Path):
  cases = [
      ("malformed", ("thing.txt",), "malformed binding"),
      ("dead", ("producer/other.txt",), "does not declare"),
      ("later-or-unknown", ("ghost/thing.txt",), "not an earlier producer"),
      (
          "duplicate",
          ("producer/thing.txt", "producer/thing.txt"),
          "twice",
      ),
  ]
  for _, inputs, match in cases:
    with pytest.raises(WorkflowError, match=match):
      _ = _chain(tmp_path, inputs=inputs)


def test_mixed_instances_are_refused(tmp_path: Path):
  with pytest.raises(WorkflowError, match="one instance"):
    _ = Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "a",
                _Producer(instance=_Instance()),
                sandbox_factory=_factory(tmp_path),
            ),
            WorkflowEntry(
                "b",
                _Producer(instance=_Instance(instance_id="other")),
                sandbox_factory=_factory(tmp_path),
            ),
        ],
    )


def test_duplicate_keys_are_refused(tmp_path: Path):
  with pytest.raises(WorkflowError, match="duplicate entry keys"):
    _ = Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "same",
                _Producer(instance=_Instance()),
                sandbox_factory=_factory(tmp_path),
            ),
            WorkflowEntry(
                "same",
                _Consumer(instance=_Instance()),
                sandbox_factory=_factory(tmp_path),
            ),
        ],
    )


# ─── execution (phase B + the gate + the record) ─────────────────────────────


def test_the_chain_feeds_the_consumer_from_the_store(tmp_path: Path):
  wf = _chain(tmp_path)
  outcome = wf.execute(output_dir=tmp_path / "out", timeout=10.0, run_ts="ts-0")
  assert outcome.succeeded is True
  assert [e.status for e in outcome.entries] == [
      EntryStatus.SUCCEEDED,
      EntryStatus.SUCCEEDED,
  ]
  # the consumer read exactly the bytes the producer's attempt persisted
  consumer = wf.entries[1].task
  assert isinstance(consumer, _Consumer)
  assert consumer.seen == [b"THING"]
  # and the workflow record was written, naming both entries and the edge
  assert outcome.record_key == "sw/acme__widget-1/r0/workflow.json"
  record = json.loads(wf.store.get_bytes(outcome.record_key))
  assert record["edges"] == {
      "producer": {},
      "consumer": {"thing.txt": "producer"},
  }
  assert [e["key"] for e in record["entries"]] == ["producer", "consumer"]


def test_an_empty_upstream_artifact_is_the_distinct_edge_failure(
    tmp_path: Path,
):
  store = _store(tmp_path)
  wf = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              _Producer(instance=_Instance(), content=b""),  # an empty patch
              sandbox_factory=_factory(tmp_path / "p"),
          ),
          WorkflowEntry(
              "consumer",
              _Consumer(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "c"),
          ),
      ],
  )
  outcome = wf.execute(output_dir=tmp_path / "out", timeout=10.0, run_ts="ts-0")
  assert outcome.succeeded is False
  producer, consumer = outcome.entries
  assert producer.status is EntryStatus.SUCCEEDED
  assert consumer.status is EntryStatus.EDGE_FAILED
  assert consumer.missing_inputs == ("thing.txt",)
  assert consumer.run is None  # no sandbox was built, no budget spent
  # the consumer never became terminal: a repaired store can proceed
  address = TaskAddress(sweep_id="sw", rollout_id=0, task="consumer")
  assert read_marker(store, address, "acme__widget-1") is None
  # and no workflow record exists — absence means "did not complete"
  assert outcome.record_key is None


def test_a_failed_entry_blocks_the_rest(tmp_path: Path):
  store = _store(tmp_path)
  wf = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              # declares thing.txt but the schema requires consumed.txt too?
              # simplest failure: a producer whose observer declares a name
              # it never emits — use a consumer with no input to reuse types
              _Producer(instance=_Instance(), content=b"x"),
              sandbox_factory=_factory(tmp_path / "p"),
          ),
      ],
  )
  del wf  # covered below with a real failing task

  @final
  @dataclass
  class _Failing(Task):
    instance: TaskInstance[SweBenchProVerdict]

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      # declares but never emits → outputs_valid False → FAILED
      return (_DeclareOnly(),)

    @override
    def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
      return sb.run_script("main.sh", timeout=timeout)

  @final
  class _DeclareOnly(SandboxObserver):

    @override
    def output_schema(self) -> tuple[ArtifactSchema, ...]:
      return (ArtifactSchema("thing.txt", description="never emitted"),)

  chain = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              _Failing(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "f"),
          ),
          WorkflowEntry(
              "consumer",
              _Consumer(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "c"),
          ),
      ],
  )
  outcome = chain.execute(
      output_dir=tmp_path / "out", timeout=10.0, run_ts="ts-0"
  )
  assert outcome.succeeded is False
  assert [e.status for e in outcome.entries] == [
      EntryStatus.FAILED,
      EntryStatus.BLOCKED,
  ]
  assert outcome.entries[1].run is None  # never attempted
  assert outcome.record_key is None


def test_reentry_resumes_the_finished_producer_and_does_no_work(
    tmp_path: Path,
):
  store = _store(tmp_path)

  def run(consumer: _Consumer):
    wf = Workflow(
        store=store,
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "producer",
                _Producer(instance=_Instance()),
                sandbox_factory=_factory(tmp_path / "p"),
            ),
            WorkflowEntry(
                "consumer",
                consumer,
                sandbox_factory=_factory(tmp_path / "c"),
            ),
        ],
    )
    return wf.execute(output_dir=tmp_path / "out", timeout=10.0, run_ts="ts-1")

  first_consumer = _Consumer(instance=_Instance())
  first = run(first_consumer)
  assert first.succeeded is True
  # re-entry: both entries hit their markers; nothing executes again
  second_consumer = _Consumer(instance=_Instance())
  second = run(second_consumer)
  assert second.succeeded is True
  assert all(e.run is not None and e.run.resumed for e in second.entries)
  assert second_consumer.seen == []  # its action never ran
  # the resumed producer's record still fed the edge map of the new record
  assert second.record_key is not None
  record = json.loads(store.get_bytes(second.record_key))
  assert record["entries"][0]["resumed"] is True


def test_resume_false_runs_everything_fresh(tmp_path: Path):
  store = _store(tmp_path)
  consumer = _Consumer(instance=_Instance())

  def entries(consumer: _Consumer):
    return [
        WorkflowEntry(
            "producer",
            _Producer(instance=_Instance()),
            sandbox_factory=_factory(tmp_path / "p"),
        ),
        WorkflowEntry(
            "consumer",
            consumer,
            sandbox_factory=_factory(tmp_path / "c"),
        ),
    ]

  first = Workflow(
      store=store, sweep_id="sw", rollout_id=0, entries=entries(consumer)
  ).execute(output_dir=tmp_path / "out", timeout=10.0, run_ts="ts-1")
  assert first.succeeded is True
  rerun_consumer = _Consumer(instance=_Instance())
  rerun = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      # fresh factory bases: the factory contract is a fresh, empty
      # workspace per call, and a rerun is a new set of calls
      entries=[
          WorkflowEntry(
              "producer",
              _Producer(instance=_Instance()),
              sandbox_factory=_factory(tmp_path / "p2"),
          ),
          WorkflowEntry(
              "consumer",
              rerun_consumer,
              sandbox_factory=_factory(tmp_path / "c2"),
          ),
      ],
  ).execute(
      output_dir=tmp_path / "out2",
      timeout=10.0,
      run_ts="ts-2",
      resume=False,
  )
  assert rerun.succeeded is True
  assert all(e.run is not None and not e.run.resumed for e in rerun.entries)
  assert rerun_consumer.seen == [b"THING"]  # it really ran again
