"""Tests for the Workflow: static edge resolution, execution, the record.

The chain is exercised with small generic tasks over ``FakeSandbox``
factories and a real ``FilesystemStore``: a producer whose observer declares
and emits ``thing.txt``, and a consumer whose ``input_schema`` requires it —
the same shape as rollout → eval, without Docker or datasets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import json
import pathlib
from pathlib import Path
from typing import Any, final, override

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
    Inline,
    Mount,
    SandboxConfig,
    SandboxError,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
    Store,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.testing import FakeSandboxConfig
from swe_lab.workflow import (
    EntryStatus,
    read_marker,
    Task,
    TaskAddress,
    Workflow,
    WorkflowEntry,
    WorkflowError,
)


def _on(wf: Workflow, sandbox: SandboxConfig) -> Workflow:
  """Run every entry of ``wf`` on ``sandbox`` — entries declare where they run.

  Args:
    wf: The workflow to place.
    sandbox: The config (and therefore the backend) every entry gets.

  Returns:
    A copy whose entries all declare ``sandbox``.
  """
  return replace(wf, entries=[replace(e, sandbox=sandbox) for e in wf.entries])


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
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
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

  produces: str = "thing.txt"
  content: bytes = b"THING"

  @override
  def observers(
      self, instance: TaskInstance[Any]
  ) -> tuple[SandboxObserver, ...]:
    del instance
    return (_Emit(name=self.produces, content=self.content),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    return sb.run_script("main.sh", timeout=timeout)


@final
@dataclass
class _Consumer(Task):
  """Declares ``consumes`` as an input; records what got staged."""

  consumes: str = "thing.txt"
  optional: bool = False
  seen: list[bytes] = field(default_factory=list)

  @override
  def input_schema(self) -> tuple[ArtifactSchema, ...]:
    return (
        ArtifactSchema(
            self.consumes,
            required=not self.optional,
            description="the upstream thing",
        ),
    )

  @override
  def observers(
      self, instance: TaskInstance[Any]
  ) -> tuple[SandboxObserver, ...]:
    del instance
    return (_Emit(name="consumed.txt", content=b"OK"),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    self.seen.append(
        sb.read(self.consumes) if sb.exists(self.consumes) else b""
    )
    return sb.run_script("main.sh", timeout=timeout)


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
              _Producer(),
              timeout=10.0,
          ),
          WorkflowEntry(
              "consumer",
              _Consumer(),
              timeout=10.0,
              **consumer_kwargs,  # pyright: ignore[reportArgumentType]
          ),
      ],
  )


# ─── declaration-time validation ─────────────────────────────────────────────


def test_binding_syntax_is_refused_at_declaration(tmp_path: Path):
  # What a declaration alone can decide: the binding's shape, and whether the
  # consuming task declares the input at all. No instance needed, so a
  # registry full of workflows catches these at import.
  cases = [
      ("malformed", ("thing.txt",), "malformed binding"),
      ("undeclared", ("producer/other.txt",), "does not declare"),
      ("duplicate", ("producer/thing.txt", "producer/thing.txt"), "twice"),
  ]
  for _, inputs, match in cases:
    with pytest.raises(WorkflowError, match=match):
      _ = _chain(tmp_path, inputs=inputs)


def test_duplicate_keys_are_refused(tmp_path: Path):
  with pytest.raises(WorkflowError, match="duplicate entry keys"):
    _ = Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "same",
                _Producer(),
                timeout=10.0,
            ),
            WorkflowEntry(
                "same",
                _Consumer(),
                timeout=10.0,
            ),
        ],
    )


# ─── bind-time validation (phase A) ──────────────────────────────────────────


def test_an_unproduced_input_is_refused_at_bind_time(tmp_path: Path):
  # Output schemas can be instance-derived, so this is decided when the
  # instance binds — still before any sandbox is built.
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "consumer",
              _Consumer(),
              timeout=10.0,
          )
      ],
  )
  with pytest.raises(WorkflowError, match="nothing produces"):
    _ = _on(wf, FakeSandboxConfig()).execute(
        _Instance(),
        output_dir=tmp_path / "out",
        run_ts="ts-0",
    )


def test_two_producers_of_one_name_demand_an_explicit_binding(tmp_path: Path):
  def chain(consumer_entry: WorkflowEntry) -> Workflow:
    return Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "one",
                _Producer(),
                timeout=10.0,
            ),
            WorkflowEntry(
                "two",
                _Producer(content=b"FROM TWO"),
                timeout=10.0,
            ),
            consumer_entry,
        ],
    )

  ambiguous = chain(
      WorkflowEntry(
          "consumer",
          _Consumer(),
          timeout=10.0,
      )
  )
  with pytest.raises(WorkflowError, match="bind it explicitly"):
    _ = _on(ambiguous, FakeSandboxConfig()).execute(
        _Instance(),
        output_dir=tmp_path / "out",
        run_ts="ts-0",
    )
  # the one-line fix the error asks for — and it picks the bound producer:
  consumer = _Consumer()
  outcome = _on(
      chain(
          WorkflowEntry(
              "consumer",
              consumer,
              timeout=10.0,
              inputs=("two/thing.txt",),
          )
      ),
      FakeSandboxConfig(),
  ).execute(
      _Instance(),
      output_dir=tmp_path / "out2",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"FROM TWO"]


def test_a_task_that_builds_its_own_input_needs_no_producer(tmp_path: Path):
  # The third supplier: a task carrying an inputs builder fills its declared
  # input in-session, so an unproduced name is the standalone shape rather
  # than a dangling edge. Requiredness is still verified — inside the session,
  # before the action.
  def build(sb: SandboxFs, instance: TaskInstance[Any]) -> Mapping[str, bytes]:
    del sb, instance
    return {"thing.txt": b"SELF-MADE"}

  consumer = _Consumer(inputs_builder=build)
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[WorkflowEntry("consumer", consumer, timeout=10.0)],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"SELF-MADE"]


def test_an_optional_input_nothing_produces_leaves_the_workflow_valid(
    tmp_path: Path,
):
  # Optional here means what it means at execution: a workflow that simply
  # does not supply one is valid, and the entry runs without it. Refusing to
  # bind would make an optional input harder to satisfy than a required one.
  consumer = _Consumer(consumes="extra.txt", optional=True)
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[WorkflowEntry("consumer", consumer, timeout=10.0)],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b""]  # it ran, and read nothing


def test_an_optional_input_still_binds_where_something_produces_it(
    tmp_path: Path,
):
  consumer = _Consumer(optional=True)
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry("producer", _Producer(), timeout=10.0),
          WorkflowEntry("consumer", consumer, timeout=10.0),
      ],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"THING"]
  record = json.loads(wf.store.get_bytes(outcome.record_key or ""))
  assert record["edges"]["consumer"] == {"thing.txt": "producer"}


def test_an_optional_input_the_caller_supplies_binds_too(tmp_path: Path):
  consumer = _Consumer(optional=True)
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[WorkflowEntry("consumer", consumer, timeout=10.0)],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      inputs={"thing.txt": Mount(Inline(b"FROM CALLER"))},
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"FROM CALLER"]


def test_a_binding_to_a_non_producer_is_refused_at_bind_time(tmp_path: Path):
  wf = _chain(tmp_path, inputs=("ghost/thing.txt",))
  with pytest.raises(WorkflowError, match="not an earlier producer"):
    _ = _on(wf, FakeSandboxConfig()).execute(
        _Instance(),
        output_dir=tmp_path / "out",
        run_ts="ts-0",
    )


# ─── the sandbox: declared semantics, synthesized per attempt ────────────────


def test_an_entrys_config_is_used_exactly_as_declared(tmp_path: Path):
  # Nothing merges into it and nothing overrides it: what the entry declares
  # is what the sandbox is built from. The runner adds only the one thing the
  # entry may not set — the attempt's own workspace.
  config = FakeSandboxConfig(network=False, env={"EVAL": "1"})
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry("producer", _Producer(), timeout=10.0, sandbox=config)
      ],
  )
  outcome = wf.execute(_Instance(), output_dir=tmp_path / "out", run_ts="ts-0")
  assert outcome.succeeded is True
  built = config.built[0].config
  assert isinstance(built, FakeSandboxConfig)
  assert (built.network, dict(built.env)) == (False, {"EVAL": "1"})
  assert built.workspace == epath.Path(tmp_path / "out/producer/ws/a0")


def test_each_entry_runs_on_the_backend_its_own_config_names(tmp_path: Path):
  # The point of the config carrying the backend: two entries of ONE workflow
  # can run on two different backends, because nothing above them imposes one.
  first = FakeSandboxConfig()
  second = FakeSandboxConfig()
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry("producer", _Producer(), timeout=10.0, sandbox=first),
          WorkflowEntry("consumer", _Consumer(), timeout=10.0, sandbox=second),
      ],
  )
  outcome = wf.execute(_Instance(), output_dir=tmp_path / "out", run_ts="ts-0")
  assert outcome.succeeded is True
  # Each entry built from its OWN config — `built` is per-config, so one
  # sandbox each is the proof that no shared prototype was in play.
  assert (len(first.built), len(second.built)) == (1, 1)
  assert first.built[0].workspace == epath.Path(tmp_path / "out/producer/ws/a0")
  assert second.built[0].workspace == epath.Path(
      tmp_path / "out/consumer/ws/a0"
  )


def test_an_entry_refuses_budgets_it_could_not_run(tmp_path: Path):
  # Checked on the entry, not at one caller, so a definition written by hand,
  # one built by a CLI override, and one a downstream user registers are all
  # refused the same way.
  del tmp_path
  for timeout, retries, match in [
      (0.0, 0, "positive, finite"),
      (-1.0, 0, "positive, finite"),
      (float("nan"), 0, "positive, finite"),
      (float("inf"), 0, "positive, finite"),
      (10.0, -1, "retries must be"),
  ]:
    with pytest.raises(WorkflowError, match=match):
      _ = WorkflowEntry(
          "producer", _Producer(), timeout=timeout, retries=retries
      )


def test_an_entry_may_not_declare_a_workspace(tmp_path: Path):
  with pytest.raises(WorkflowError, match="workspace"):
    _ = WorkflowEntry(
        "producer",
        _Producer(),
        timeout=10.0,
        sandbox=FakeSandboxConfig(workspace=epath.Path(tmp_path)),
    )


# ─── execution (phase B + the gate + the record) ─────────────────────────────


def test_the_chain_feeds_the_consumer_from_the_store(tmp_path: Path):
  wf = _chain(tmp_path)
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
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
  assert record["succeeded"] is True


def test_the_workflow_record_is_written_whatever_the_outcome(tmp_path: Path):
  # The invariant ADR-0009 turns on: once binding is past, a run leaves a
  # roll-up — success or failure — so a consumer never has to reassemble one
  # by globbing task prefixes. Absence now means only "never got past
  # binding".

  @final
  class _NeverEmits(SandboxObserver):

    @override
    def output_schema(self) -> tuple[ArtifactSchema, ...]:
      return (ArtifactSchema("thing.txt", description="never emitted"),)

  @final
  @dataclass
  class _Failing(Task):

    @override
    def observers(
        self, instance: TaskInstance[Any]
    ) -> tuple[SandboxObserver, ...]:
      del instance
      return (_NeverEmits(),)  # declared, never emitted -> FAILED

    @override
    def action(
        self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
    ) -> ExecResult:
      del sb, instance, timeout
      return ExecResult(0, "", "")

  for name, task in (("ok", _Producer()), ("bad", _Failing())):
    store = _store(tmp_path / name)
    wf = Workflow(
        store=store,
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "producer", task, timeout=10.0, sandbox=FakeSandboxConfig()
            )
        ],
    )
    outcome = wf.execute(
        _Instance(), output_dir=tmp_path / f"out-{name}", run_ts="ts-0"
    )
    assert outcome.succeeded is (name == "ok")
    assert outcome.record_key is not None
    record = json.loads(store.get_bytes(outcome.record_key))
    assert record["succeeded"] is (name == "ok")


def test_the_record_carries_each_entrys_metrics(tmp_path: Path):
  # The roll-up copies the metrics the attempt shard already holds, so a
  # consumer reads one object per run instead of one per task per run.
  store = _store(tmp_path)
  wf = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              _Producer(),
              timeout=10.0,
              sandbox=FakeSandboxConfig(),
          )
      ],
  )
  outcome = wf.execute(_Instance(), output_dir=tmp_path / "out", run_ts="ts-0")
  assert outcome.record_key is not None
  entry = json.loads(store.get_bytes(outcome.record_key))["entries"][0]
  run = outcome.entries[0].run
  assert run is not None
  # exactly what the shard says — a roll-up, nothing newly measured
  assert entry["metrics"] == dict(run.record.metrics)


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
              _Producer(content=b""),  # an empty patch
              timeout=10.0,
          ),
          WorkflowEntry(
              "consumer",
              _Consumer(),
              timeout=10.0,
          ),
      ],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is False
  producer, consumer = outcome.entries
  assert producer.status is EntryStatus.SUCCEEDED
  assert consumer.status is EntryStatus.EDGE_FAILED
  assert consumer.missing_inputs == ("thing.txt",)
  assert consumer.run is None  # no sandbox was built, no budget spent
  # the consumer never became terminal: a repaired store can proceed
  address = TaskAddress(sweep_id="sw", rollout_id=0, task="consumer")
  assert read_marker(store, address, "acme__widget-1") is None
  # …and the roll-up is still written, saying so: the failed run is the one
  # most worth reading (ADR-0009).
  assert outcome.record_key is not None
  record = json.loads(store.get_bytes(outcome.record_key))
  assert record["succeeded"] is False
  consumer = next(e for e in record["entries"] if e["key"] == "consumer")
  assert consumer["status"] == "edge_failed"
  assert consumer["missing_inputs"] == ["thing.txt"]
  # it never ran, and the record says that rather than omitting it
  assert (consumer["attempts"], consumer["artifact_keys"]) == (0, {})


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
              _Producer(content=b"x"),
              timeout=10.0,
          ),
      ],
  )
  del wf  # covered below with a real failing task

  @final
  @dataclass
  class _Failing(Task):

    @override
    def observers(
        self, instance: TaskInstance[Any]
    ) -> tuple[SandboxObserver, ...]:
      del instance
      # declares but never emits → outputs_valid False → FAILED
      return (_DeclareOnly(),)

    @override
    def action(
        self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
    ) -> ExecResult:
      del instance
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
              _Failing(),
              timeout=10.0,
          ),
          WorkflowEntry(
              "consumer",
              _Consumer(),
              timeout=10.0,
          ),
      ],
  )
  outcome = _on(chain, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is False
  assert [e.status for e in outcome.entries] == [
      EntryStatus.FAILED,
      EntryStatus.BLOCKED,
  ]
  assert outcome.entries[1].run is None  # never attempted
  # The roll-up is written anyway, and carries every entry's status — a
  # blocked entry is a fact, not an omission (ADR-0009).
  assert outcome.record_key is not None
  record = json.loads(store.get_bytes(outcome.record_key))
  assert record["succeeded"] is False
  assert [(e["key"], e["status"]) for e in record["entries"]] == [
      ("producer", "failed"),
      ("consumer", "blocked"),
  ]


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
                _Producer(),
                timeout=10.0,
            ),
            WorkflowEntry(
                "consumer",
                consumer,
                timeout=10.0,
            ),
        ],
    )
    return _on(wf, FakeSandboxConfig()).execute(
        _Instance(),
        output_dir=tmp_path / "out",
        run_ts="ts-1",
    )

  first_consumer = _Consumer()
  first = run(first_consumer)
  assert first.succeeded is True
  # re-entry: both entries hit their markers; nothing executes again
  second_consumer = _Consumer()
  second = run(second_consumer)
  assert second.succeeded is True
  assert all(e.run is not None and e.run.resumed for e in second.entries)
  assert second_consumer.seen == []  # its action never ran
  # the resumed producer's record still fed the edge map of the new record
  assert second.record_key is not None
  record = json.loads(store.get_bytes(second.record_key))
  assert record["entries"][0]["resumed"] is True


def test_a_workflow_refuses_to_resume_past_a_broken_marker(tmp_path: Path):
  # A single entry, so nothing downstream would have noticed the missing
  # record by failing an edge: without the check the workflow would report
  # success and write a workflow record over evidence that is not there.
  store = _store(tmp_path)
  wf = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      entries=[WorkflowEntry("producer", _Producer(), timeout=10.0)],
  )
  first = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert first.succeeded is True
  shard = (
      pathlib.Path(str(tmp_path / "store"))
      / "sw/acme__widget-1/r0/producer/a0/run.json"
  )
  shard.unlink()

  with pytest.raises(SandboxError, match="no shard matches"):
    _ = _on(wf, FakeSandboxConfig()).execute(
        _Instance(),
        output_dir=tmp_path / "out2",
        run_ts="ts-1",
    )


def test_resume_false_runs_everything_fresh(tmp_path: Path):
  store = _store(tmp_path)
  consumer = _Consumer()

  def entries(consumer: _Consumer):
    return [
        WorkflowEntry(
            "producer",
            _Producer(),
            timeout=10.0,
        ),
        WorkflowEntry(
            "consumer",
            consumer,
            timeout=10.0,
        ),
    ]

  first = _on(
      Workflow(
          store=store, sweep_id="sw", rollout_id=0, entries=entries(consumer)
      ),
      FakeSandboxConfig(),
  ).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-1",
  )
  assert first.succeeded is True
  rerun_consumer = _Consumer()
  rerun = _on(
      Workflow(
          store=store,
          sweep_id="sw",
          rollout_id=0,
          # fresh factory bases: the factory contract is a fresh, empty
          # workspace per call, and a rerun is a new set of calls
          entries=[
              WorkflowEntry(
                  "producer",
                  _Producer(),
                  timeout=10.0,
              ),
              WorkflowEntry(
                  "consumer",
                  rerun_consumer,
                  timeout=10.0,
              ),
          ],
      ),
      FakeSandboxConfig(),
  ).execute(
      _Instance(),
      output_dir=tmp_path / "out2",
      run_ts="ts-2",
      resume=False,
  )
  assert rerun.succeeded is True
  assert all(e.run is not None and not e.run.resumed for e in rerun.entries)
  assert rerun_consumer.seen == [b"THING"]  # it really ran again


# ─── workflow-level inputs: the caller as a producer ─────────────────────────


def test_a_single_entry_workflow_takes_its_input_from_the_caller(
    tmp_path: Path,
):
  # The eval-CLI shape: one entry, its declared input provided at the
  # workflow boundary — same channel, no special-casing.
  consumer = _Consumer()
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "consumer",
              consumer,
              timeout=10.0,
          )
      ],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      inputs={"thing.txt": Mount(Inline(b"FROM CALLER"))},
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"FROM CALLER"]
  assert outcome.record_key is not None
  record = json.loads(wf.store.get_bytes(outcome.record_key))
  assert record["edges"] == {"consumer": {"thing.txt": "inputs"}}


def test_an_empty_caller_input_is_the_same_edge_failure(tmp_path: Path):
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "consumer",
              _Consumer(),
              timeout=10.0,
          )
      ],
  )
  outcome = _on(wf, FakeSandboxConfig()).execute(
      _Instance(),
      inputs={"thing.txt": Mount(Inline(b""))},
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is False
  assert outcome.entries[0].status is EntryStatus.EDGE_FAILED
  assert outcome.entries[0].missing_inputs == ("thing.txt",)


def test_an_unconsumed_caller_input_is_refused(tmp_path: Path):
  wf = Workflow(
      store=_store(tmp_path),
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              "producer",
              _Producer(),
              timeout=10.0,
          )
      ],
  )
  with pytest.raises(WorkflowError, match="consumed by no entry"):
    _ = _on(wf, FakeSandboxConfig()).execute(
        _Instance(),
        inputs={"thing.txt": Mount(Inline(b"NOBODY WANTS ME"))},
        output_dir=tmp_path / "out",
        run_ts="ts-0",
    )


def test_caller_input_vs_entry_output_is_ambiguity_like_any_other(
    tmp_path: Path,
):
  provided = {"thing.txt": Mount(Inline(b"CALLER"))}

  def chain(consumer_entry: WorkflowEntry) -> Workflow:
    return Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "producer",
                _Producer(),
                timeout=10.0,
            ),
            consumer_entry,
        ],
    )

  ambiguous = chain(
      WorkflowEntry(
          "consumer",
          _Consumer(),
          timeout=10.0,
      )
  )
  with pytest.raises(WorkflowError, match="bind it explicitly"):
    _ = _on(ambiguous, FakeSandboxConfig()).execute(
        _Instance(),
        inputs=provided,
        output_dir=tmp_path / "out",
        run_ts="ts-0",
    )
  # binding to the reserved source resolves it, like any producer key
  consumer = _Consumer()
  outcome = _on(
      chain(
          WorkflowEntry(
              "consumer",
              consumer,
              timeout=10.0,
              inputs=("inputs/thing.txt",),
          )
      ),
      FakeSandboxConfig(),
  ).execute(
      _Instance(),
      inputs=provided,
      output_dir=tmp_path / "out2",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"CALLER"]


def test_the_inputs_entry_key_is_reserved(tmp_path: Path):
  with pytest.raises(WorkflowError, match="reserved"):
    _ = Workflow(
        store=_store(tmp_path),
        sweep_id="sw",
        rollout_id=0,
        entries=[
            WorkflowEntry(
                "inputs",
                _Producer(),
                timeout=10.0,
            )
        ],
    )
