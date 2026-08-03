"""The workflow: a declared list of tasks, edges resolved from the store.

A ``Workflow`` is `(key, task)` entries over one ``(sweep, instance,
rollout)`` (ADR-0007 §§5, 9–10). Edges are matched **by store name** between
one entry's declared outputs and a later entry's declared inputs — statically
at construction, where any ambiguity is an error — and materialized at run
time by fetching the producer's recorded artifact out of the store and
mounting it read-only. Execution is resume-aware (Task 20's ``run_task`` per
entry), all-or-nothing, and leaves a derived workflow record, written last.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import json

from etils import epath

from swe_lab.sandbox import (
    Inline,
    LocalFile,
    merge_output_schemas,
    Mount,
    Mounts,
    Sandbox,
    SandboxError,
    Store,
)

from .run_task import run_task, TaskAddress, TaskOutcome, TaskRunOutcome
from .task import Task

# The derived workflow record's object name under the rollout prefix
# (ADR-0007 §10): written last, and its absence means "did not complete".
WORKFLOW_RECORD_NAME = "workflow.json"

# The reserved producer key naming the workflow's own ``inputs`` in edge
# resolution and bindings ("inputs/patch.diff"): caller-provided artifacts are
# a producer like any entry — same matching, same ambiguity rules — just one
# that exists before anything runs. No entry may claim this key.
INPUTS_KEY = "inputs"


class WorkflowError(Exception):
  """A workflow declaration that can never run — raised at construction."""


class EntryStatus(StrEnum):
  """How one entry ended within a workflow execution."""

  SUCCEEDED = "succeeded"
  FAILED = "failed"
  EDGE_FAILED = "edge_failed"  # a required input missing/empty (ADR-0007 §5)
  BLOCKED = "blocked"  # an earlier entry failed; never attempted


@dataclass(frozen=True)
class WorkflowEntry:
  """One step: a key naming it in the store, the task, and how it runs.

  Attributes:
    key: The task segment of the ADR-0004 key, unique in the workflow. Also
      the identity resume trusts: change the task, change the key.
    task: The constructed task (declaration data).
    sandbox_factory: Builds this entry's sandbox — called once per attempt;
      every call must return a sandbox over a fresh, empty workspace (the
      factory owns that allocation). Per entry, so backend / network / pull
      knobs stay the caller's and two entries can differ.
    timeout: Seconds before each of this entry's attempts is killed —
      the budget is the entry's own (an agent run and an eval have no reason
      to share one), so there is no workflow-wide value to fall back to.
    inputs: Explicit edge bindings, each ``"<producer key>/<input name>"``
      (``"rollout/patch.diff"``). Only needed where name matching alone is
      ambiguous (two earlier producers of one name); a binding that matching
      would have resolved is also accepted, as documentation.
    retries: Task-level retry budget for this entry (``run_task``).
  """

  key: str
  task: Task
  sandbox_factory: Callable[[], Sandbox]
  timeout: float
  inputs: Sequence[str] = ()
  retries: int = 0


@dataclass(frozen=True)
class EntryOutcome:
  """How one entry fared in one workflow execution.

  Attributes:
    key: The entry's key.
    status: How it ended.
    run: The task-level run report; ``None`` when the entry never ran
      (``BLOCKED``, or ``EDGE_FAILED`` before any sandbox existed).
    missing_inputs: For ``EDGE_FAILED``: the required input names that could
      not be materialized (absent from the producer's record, or empty).
  """

  key: str
  status: EntryStatus
  run: TaskRunOutcome | None = None
  missing_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowOutcome:
  """What one workflow execution reports back.

  Attributes:
    succeeded: Whether every entry succeeded (ADR-0007 §10 — all or nothing).
    entries: Per-entry outcomes, in declared order.
    record_key: The written workflow record's store key; ``None`` unless the
      workflow succeeded (its absence *means* "did not complete").
  """

  succeeded: bool
  entries: tuple[EntryOutcome, ...]
  record_key: str | None


@dataclass(frozen=True)
class Workflow:
  """A declared, ordered list of tasks over one ``(sweep, instance, rollout)``.

  Construction **is** the static check: entry keys, instance consistency, and
  every edge (explicit bindings verified, name matches unambiguous) are
  validated before anything can run — a workflow that would ever dangle is
  refused at declaration.

  Attributes:
    store: Where every entry persists and where edges are resolved from.
    sweep_id: The sweep the run belongs to.
    rollout_id: Which sample of the instance.
    entries: The steps, in order — the caller owns the topological sort
      (ADR-0007 §9: a list before it is a DAG).
    inputs: Caller-provided artifacts, input name → mount — how a value the
      caller already holds (a gold patch, a candidate file) enters the same
      channel a workflow edge uses. In edge resolution they are a producer
      named ``"inputs"`` that exists before anything runs, so a single-entry
      workflow with a declared input is as runnable as a chain; an input no
      entry consumes is refused (the author believes something false).
  """

  store: Store
  sweep_id: str
  rollout_id: int
  entries: Sequence[WorkflowEntry]
  inputs: Mapping[str, Mount] = field(default_factory=dict)
  _edges: dict[str, dict[str, str]] = field(
      init=False, repr=False, compare=False
  )

  def __post_init__(self) -> None:
    """Validate the declaration and resolve every edge, statically.

    Raises:
      WorkflowError: If the declaration can never run — duplicate or
        malformed keys, mixed instances, a dead or unverifiable binding, an
        input no earlier entry produces, or one that two produce unbound.
    """
    if not self.entries:
      raise WorkflowError("a workflow needs at least one entry")
    keys = [entry.key for entry in self.entries]
    if len(set(keys)) != len(keys):
      raise WorkflowError(f"duplicate entry keys: {sorted(keys)}")
    if INPUTS_KEY in keys:
      raise WorkflowError(
          f"entry key {INPUTS_KEY!r} is reserved for the workflow's own"
          " inputs"
      )
    instance_ids = {e.task.instance.instance_id for e in self.entries}
    if len(instance_ids) != 1:
      raise WorkflowError(
          "one workflow runs one instance; entries bind"
          f" {sorted(instance_ids)}"
      )
    # TaskAddress re-validates each key's shape; building them here surfaces
    # a malformed key at declaration, with the entry named.
    for entry in self.entries:
      try:
        _ = self._address(entry)
      except ValueError as error:
        raise WorkflowError(f"entry {entry.key!r}: {error}") from error
    edges = _resolve_edges(self.entries, provided=set(self.inputs))
    consumed = {
        name
        for bound in edges.values()
        for name, producer in bound.items()
        if producer == INPUTS_KEY
    }
    dead = sorted(set(self.inputs) - consumed)
    if dead:
      raise WorkflowError(f"workflow input(s) {dead} are consumed by no entry")
    object.__setattr__(self, "_edges", edges)

  @property
  def instance_id(self) -> str:
    """The one instance every entry is bound to."""
    return self.entries[0].task.instance.instance_id

  def _address(self, entry: WorkflowEntry) -> TaskAddress:
    return TaskAddress(
        sweep_id=self.sweep_id, rollout_id=self.rollout_id, task=entry.key
    )

  def execute(
      self,
      *,
      output_dir: epath.PathLike,
      run_ts: str,
      resume: bool = True,
      backend: str = "",
      model: str = "",
      extra_record: Mapping[str, object] | None = None,
  ) -> WorkflowOutcome:
    """Run the entries in order; stop at the first failure; record last.

    Per entry: materialize its resolved inputs out of the store (phase B —
    a required input that is missing or empty is the *distinct* edge
    failure, and costs no container), then ``run_task`` (resume check,
    attempts, marker). A failed or edge-failed entry blocks everything
    after it and fails the workflow (ADR-0007 §10). After every entry
    succeeds, the derived workflow record is written — last, atomically —
    and its absence means the workflow did not complete.

    Args:
      output_dir: Host directory for the run (per-entry subdirectories; edge
        staging under ``edges/``).
      run_ts: Launch timestamp, injected — recorded, never read.
      resume: Honor terminal markers (skip finished entries). ``False`` runs
        everything fresh, overwriting — the one-off CLI shape, where
        re-running a command means re-running it.
      backend: The sandbox backend name, recorded on the shards.
      model: The agent model alias, recorded on the shards.
      extra_record: Extra facts merged into every entry's records (e.g. the
        instance's run provenance).

    Returns:
      The workflow outcome, entry by entry.
    """
    output_dir = epath.Path(output_dir)
    outcomes: list[EntryOutcome] = []
    runs: dict[str, TaskRunOutcome] = {}
    failed = False
    for entry in self.entries:
      if failed:
        outcomes.append(EntryOutcome(key=entry.key, status=EntryStatus.BLOCKED))
        continue
      staged, missing = self._materialize_inputs(
          entry, runs, staging_dir=output_dir / "edges" / entry.key
      )
      if missing:
        outcomes.append(
            EntryOutcome(
                key=entry.key,
                status=EntryStatus.EDGE_FAILED,
                missing_inputs=tuple(missing),
            )
        )
        failed = True
        continue
      run = run_task(
          entry.task,
          store=self.store,
          address=self._address(entry),
          sandbox_factory=entry.sandbox_factory,
          output_dir=output_dir / entry.key,
          timeout=entry.timeout,
          retries=entry.retries,
          resume=resume,
          run_ts=run_ts,
          backend=backend,
          model=model,
          extra_mounts=staged,
          extra_record=extra_record,
      )
      runs[entry.key] = run
      if run.outcome is TaskOutcome.SUCCEEDED:
        outcomes.append(
            EntryOutcome(key=entry.key, status=EntryStatus.SUCCEEDED, run=run)
        )
      else:
        outcomes.append(
            EntryOutcome(key=entry.key, status=EntryStatus.FAILED, run=run)
        )
        failed = True

    record_key = None if failed else self._write_record(outcomes, run_ts)
    return WorkflowOutcome(
        succeeded=not failed, entries=tuple(outcomes), record_key=record_key
    )

  def _materialize_inputs(
      self,
      entry: WorkflowEntry,
      runs: dict[str, TaskRunOutcome],
      *,
      staging_dir: epath.Path,
  ) -> tuple[Mounts, list[str]]:
    """Fetch the entry's resolved inputs out of the store (phase B).

    The producer's **record** is the authority — its ``artifact_keys`` map
    names to the full store keys of the final attempt; keys are never
    assembled by hand. The record comes from this execution's own run when
    the producer ran here, or is read back by ``run_task`` when it resumed.

    Args:
      entry: The consuming entry.
      runs: The producers' run reports so far, by entry key.
      staging_dir: Host directory the fetched bytes land in.

    Returns:
      The mounts to feed ``execute`` (one read-only ``LocalFile`` per input),
      and the required input names that could not be materialized — non-empty
      means the distinct edge failure.
    """
    staged: Mounts = {}
    missing: list[str] = []
    for schema in entry.task.input_schema():
      producer_key = self._edges[entry.key][schema.name]
      if producer_key == INPUTS_KEY:
        mount = self.inputs[schema.name]
        if _known_empty(mount):
          # The same rule an edge applies to a fetched artifact: empty bytes
          # never reach a container.
          if schema.required:
            missing.append(schema.name)
          continue
        staged[schema.name] = mount
        continue
      record = runs[producer_key].record
      full_key = (
          record.artifact_keys.get(schema.name) if record is not None else None
      )
      if full_key is None:
        if schema.required:
          missing.append(schema.name)
        continue
      dest = staging_dir / schema.name
      try:
        self.store.get(full_key, dest)
      except SandboxError:
        full_key = None
      if full_key is None or not dest.is_file() or not dest.read_bytes():
        # Missing or empty bytes: an empty patch is caught here, before a
        # container is paid for (ADR-0007 §5).
        if schema.required:
          missing.append(schema.name)
        continue
      staged[schema.name] = Mount(LocalFile(dest), read_only=True)
    return staged, missing

  def _write_record(self, outcomes: Sequence[EntryOutcome], run_ts: str) -> str:
    """Derive and write the workflow record — last, atomically.

    A roll-up of the entries' final attempt records (nothing new is
    measured): per entry its key, attempts, resumed flag, and artifact keys,
    plus the resolved edge map. v1 records success only (ADR-0007 §10).

    Args:
      outcomes: Every entry's outcome, all ``SUCCEEDED``.
      run_ts: The launch timestamp, recorded.

    Returns:
      The record's store key.
    """
    entries_json = []
    for outcome in outcomes:
      run = outcome.run
      assert run is not None  # succeeded entries always ran or resumed
      entries_json.append(
          {
              "key": outcome.key,
              "attempts": run.attempts,
              "resumed": run.resumed,
              "artifact_keys": (
                  dict(run.record.artifact_keys)
                  if run.record is not None
                  else {}
              ),
          }
      )
    key = (
        f"{self.sweep_id}/{self.instance_id}/r{self.rollout_id}"
        f"/{WORKFLOW_RECORD_NAME}"
    )
    self.store.put_bytes(
        key,
        json.dumps(
            {
                "run_ts": run_ts,
                "entries": entries_json,
                "edges": self._edges,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
    )
    return key


def _resolve_edges(
    entries: Sequence[WorkflowEntry],
    *,
    provided: set[str],
) -> dict[str, dict[str, str]]:
  """Resolve every input to its producing source, statically (phase A).

  Everything here is declaration data — ``input_schema()`` is fixed by task
  configuration and output schemas derive from the observers at construction
  time — so the whole edge map is computed, and checked, before any sandbox
  exists. The workflow's own ``inputs`` participate as the producer
  ``"inputs"``, existing before every entry.

  Args:
    entries: The workflow's entries, in declared (topological) order.
    provided: The caller-provided input names (the ``"inputs"`` producer).

  Returns:
    ``entry key → {input name → producer key}``.

  Raises:
    WorkflowError: A malformed, dead, duplicate, or unverifiable binding; an
      input no source produces; or one that several produce, unbound —
      never nearest-wins: ambiguity is an error, like every duplicate in this
      codebase.
  """
  edges: dict[str, dict[str, str]] = {}
  # name → earlier producers, in order; caller inputs exist before anything.
  produced: dict[str, list[str]] = {name: [INPUTS_KEY] for name in provided}
  for entry in entries:
    declared_inputs = {s.name for s in entry.task.input_schema()}
    explicit = _parse_bindings(entry, declared_inputs)
    bound: dict[str, str] = {}
    for name in sorted(declared_inputs):
      if name in explicit:
        producer = explicit[name]
        if producer not in produced.get(name, []):
          raise WorkflowError(
              f"{entry.key} binds {name!r} to {producer!r}, which is not an"
              " earlier producer of it"
          )
        bound[name] = producer
      else:
        candidates = produced.get(name, [])
        if len(candidates) == 1:
          bound[name] = candidates[0]
        elif not candidates:
          raise WorkflowError(
              f"nothing produces {name!r}, required by {entry.key}: no"
              " earlier entry declares it and the workflow's inputs do"
              " not provide it"
          )
        else:
          raise WorkflowError(
              f"{name!r} is produced by {candidates}; bind it explicitly on"
              f' {entry.key} (inputs=("<producer>/{name}",))'
          )
    edges[entry.key] = bound
    for schema in merge_output_schemas(
        *(o.output_schema() for o in entry.task.observers())
    ):
      produced.setdefault(schema.name, []).append(entry.key)
  return edges


def _known_empty(mount: Mount) -> bool:
  """Whether a caller-provided mount is verifiably empty (edge-invalid).

  Best-effort over the built-in resource kinds; a consumer-added kind is
  accepted as-is — only its sandbox can reach the bytes.

  Args:
    mount: The caller-provided input mount.

  Returns:
    ``True`` when the content is known to be empty or unreadable.
  """
  resource = mount.resource
  if isinstance(resource, Inline):
    return not resource.content
  if isinstance(resource, LocalFile):
    return not resource.path.is_file() or not resource.path.read_bytes()
  return False


def _parse_bindings(
    entry: WorkflowEntry, declared_inputs: set[str]
) -> dict[str, str]:
  """Parse an entry's ``"<producer>/<name>"`` bindings into name → producer.

  Splits on the **first** ``/`` — entry keys never contain one, artifact
  names may contain dots.

  Args:
    entry: The entry whose bindings are parsed.
    declared_inputs: The input names the entry's task declares.

  Returns:
    Input name → the producer key the author bound it to.

  Raises:
    WorkflowError: On a malformed binding, one for an undeclared input (the
      author believes something false — an error, not ignored), or a
      duplicate.
  """
  explicit: dict[str, str] = {}
  for binding in entry.inputs:
    producer, sep, name = binding.partition("/")
    if not sep or not producer or not name:
      raise WorkflowError(
          f"{entry.key}: malformed binding {binding!r}; expected"
          ' "<producer key>/<input name>"'
      )
    if name not in declared_inputs:
      raise WorkflowError(
          f"{entry.key} binds {name!r}, which its task does not declare"
          " as an input"
      )
    if name in explicit:
      raise WorkflowError(f"{entry.key} binds {name!r} twice")
    explicit[name] = producer
  return explicit
