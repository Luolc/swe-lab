"""The workflow: a declared list of tasks, edges resolved from the store.

A ``Workflow`` is `(key, task)` entries over one ``(sweep, rollout)`` and one
instance, bound at ``execute`` (ADR-0007 §§5, 9–10). Edges are matched **by
store name** between one entry's declared outputs and a later entry's declared
inputs — resolved at bind time, before any container, where any ambiguity is
an error — and materialized by fetching the producer's recorded artifact out
of the store and mounting it read-only. Execution is resume-aware (Task 20's
``run_task`` per entry), all-or-nothing, and leaves a derived workflow record,
written last.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from enum import StrEnum
import json
from typing import Any

from etils import epath

from swe_lab.datasets.instance import TaskInstance
from swe_lab.sandbox import (
    Inline,
    LocalFile,
    merge_output_schemas,
    Mount,
    Mounts,
    SandboxConfig,
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
    timeout: Seconds before each of this entry's attempts is killed —
      the budget is the entry's own (an agent run and an eval have no reason
      to share one), so there is no workflow-wide value to fall back to.
    sandbox: This entry's **run semantics**: the base ``SandboxConfig`` every
      backend must honor or refuse (an eval declares ``network=False``, an
      agent declares the secret it inherits). Statically declarable, so a
      shipped workflow definition can carry it. Which backend realizes those
      semantics — and that backend's own mechanics — comes from the
      invocation, and the runner merges the two. An entry MAY declare a
      backend's own config subclass; that binds the workflow to the backend,
      and an invocation on another one is then refused.
    inputs: Explicit edge bindings, each ``"<producer key>/<input name>"``
      (``"rollout/patch.diff"``). Only needed where name matching alone is
      ambiguous (two earlier producers of one name); a binding that matching
      would have resolved is also accepted, as documentation.
    retries: Task-level retry budget for this entry (``run_task``).
  """

  key: str
  task: Task
  timeout: float
  sandbox: SandboxConfig = SandboxConfig()
  inputs: Sequence[str] = ()
  retries: int = 0

  def __post_init__(self) -> None:
    """Refuse a declared workspace — allocation is the runner's, per attempt.

    Raises:
      WorkflowError: If the declared config carries a workspace.
    """
    if getattr(self.sandbox, "workspace", None) is not None:
      raise WorkflowError(
          f"entry {self.key!r} declares a sandbox workspace; the runner"
          " allocates one per attempt, so a declaration could only make two"
          " attempts share state"
      )


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
  """A declared, ordered list of tasks over one ``(sweep, rollout)``.

  Validation splits by what each phase can know. **Construction** checks
  everything that is pure declaration: entry keys and their shape, binding
  syntax, and each binding against the consuming task's static
  ``input_schema()``. **Binding** (``execute``, before any container) resolves
  the edges themselves, because output schemas may be instance-derived — an
  eval's declared byproducts come from the compiled spec. Either way a
  workflow that would dangle is refused before anything runs.

  Attributes:
    store: Where every entry persists and where edges are resolved from.
    sweep_id: The sweep the run belongs to.
    rollout_id: Which sample of the instance.
    entries: The steps, in order — the caller owns the topological sort
      (ADR-0007 §9: a list before it is a DAG).
  """

  store: Store
  sweep_id: str
  rollout_id: int
  entries: Sequence[WorkflowEntry]

  def __post_init__(self) -> None:
    """Validate everything the declaration alone can decide.

    A malformed declaration raises ``WorkflowError`` from
    :func:`validate_declaration`.
    """
    validate_declaration(self.entries)

  def _address(self, entry: WorkflowEntry) -> TaskAddress:
    return TaskAddress(
        sweep_id=self.sweep_id, rollout_id=self.rollout_id, task=entry.key
    )

  def execute(
      self,
      instance: TaskInstance[Any],
      *,
      backend: str,
      sandbox: SandboxConfig,
      inputs: Mapping[str, Mount] | None = None,
      output_dir: epath.PathLike,
      run_ts: str,
      resume: bool = True,
      model: str = "",
      extra_record: Mapping[str, object] | None = None,
  ) -> WorkflowOutcome:
    """Bind the instance, resolve the edges, run the entries in order.

    Binding comes first (phase A): with the instance in hand every entry's
    output schema is known, so the whole edge map is resolved and checked
    before any container exists. Then, per entry: materialize its resolved
    inputs out of the store (phase B — a required input that is missing or
    empty is the *distinct* edge failure, and costs no container), then
    ``run_task`` (resume check, attempts, marker). A failed or edge-failed
    entry blocks everything after it and fails the workflow (ADR-0007 §10).
    After every entry succeeds, the derived workflow record is written —
    last, atomically — and its absence means the workflow did not complete.

    Args:
      instance: The instance every entry runs against.
      backend: The registered sandbox backend this run builds on.
      sandbox: That backend's config for this run — the **prototype**: the
        invocation's own mechanics (an image pull, a remote pool), onto which
        each entry's declared semantics are merged.
      inputs: Caller-provided artifacts, input name → mount — how a value the
        caller already holds (a gold patch, a candidate file) enters the same
        channel a workflow edge uses. In edge resolution they are a producer
        named ``"inputs"`` that exists before anything runs, so a single-entry
        workflow with a declared input is as runnable as a chain; an input no
        entry consumes is refused (the author believes something false).
      output_dir: Host directory for the run (per-entry subdirectories; edge
        staging under ``edges/``).
      run_ts: Launch timestamp, injected — recorded, never read.
      resume: Honor terminal markers (skip finished entries). ``False`` runs
        everything fresh, overwriting — the one-off CLI shape, where
        re-running a command means re-running it.
      model: The agent model alias, recorded on the shards.
      extra_record: Extra facts merged into every entry's records (e.g. the
        instance's run provenance).

    Returns:
      The workflow outcome, entry by entry.

    Raises:
      WorkflowError: If the bound workflow can never run — an input nothing
        produces, one that several produce unbound, a binding to a
        non-producer, a caller input no entry consumes, or an entry whose
        declared sandbox config the invocation's backend cannot realize.
    """
    provided = dict(inputs or {})
    edges = _resolve_edges(self.entries, instance, provided=set(provided))
    consumed = {
        name
        for bound in edges.values()
        for name, producer in bound.items()
        if producer == INPUTS_KEY
    }
    dead = sorted(provided.keys() - consumed)
    if dead:
      raise WorkflowError(f"workflow input(s) {dead} are consumed by no entry")
    # Every entry's config is synthesized up front: a mismatch on the last
    # entry must not surface after the first one has already burned a
    # container.
    configs = {e.key: _synthesize_config(e, sandbox) for e in self.entries}

    output_dir = epath.Path(output_dir)
    outcomes: list[EntryOutcome] = []
    runs: dict[str, TaskRunOutcome] = {}
    failed = False
    for entry in self.entries:
      if failed:
        outcomes.append(EntryOutcome(key=entry.key, status=EntryStatus.BLOCKED))
        continue
      staged, missing = self._materialize_inputs(
          entry,
          runs,
          bound=edges[entry.key],
          provided=provided,
          staging_dir=output_dir / "edges" / entry.key,
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
          instance,
          store=self.store,
          address=self._address(entry),
          backend=backend,
          sandbox=configs[entry.key],
          output_dir=output_dir / entry.key,
          timeout=entry.timeout,
          retries=entry.retries,
          resume=resume,
          run_ts=run_ts,
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

    record_key = (
        None
        if failed
        else self._write_record(
            outcomes, run_ts, instance_id=instance.instance_id, edges=edges
        )
    )
    return WorkflowOutcome(
        succeeded=not failed, entries=tuple(outcomes), record_key=record_key
    )

  def _materialize_inputs(
      self,
      entry: WorkflowEntry,
      runs: dict[str, TaskRunOutcome],
      *,
      bound: Mapping[str, str],
      provided: Mapping[str, Mount],
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
      bound: This entry's resolved edges (input name → producer key).
      provided: The caller's own inputs (the ``"inputs"`` producer).
      staging_dir: Host directory the fetched bytes land in.

    Returns:
      The mounts to feed ``execute`` (one read-only ``LocalFile`` per input),
      and the required input names that could not be materialized — non-empty
      means the distinct edge failure.
    """
    staged: Mounts = {}
    missing: list[str] = []
    for schema in entry.task.input_schema():
      producer_key = bound.get(schema.name)
      if producer_key is None:
        continue  # the task's own builder fills it, inside the session
      if producer_key == INPUTS_KEY:
        caller = provided[schema.name]
        # The same rule an edge applies to a fetched artifact: empty bytes
        # never reach a container.
        mount = None if _known_empty(caller) else caller
      else:
        mount = self._fetch_input(
            runs[producer_key], schema.name, staging_dir / schema.name
        )
      if mount is None:
        if schema.required:
          missing.append(schema.name)
        continue
      staged[schema.name] = mount
    return staged, missing

  def _fetch_input(
      self, producer: TaskRunOutcome, name: str, dest: epath.Path
  ) -> Mount | None:
    """Fetch one input out of the producer's recorded artifact.

    Args:
      producer: The producing entry's run report (its record is the authority
        on where the artifact landed).
      name: The input's store name.
      dest: Where the bytes are staged on the host.

    Returns:
      The read-only mount, or ``None`` when there is nothing usable to mount —
      the artifact was never recorded, cannot be fetched, or is empty. An
      empty patch is caught here, before a container is paid for (§5).
    """
    record = producer.record
    full_key = record.artifact_keys.get(name) if record is not None else None
    if full_key is None:
      return None
    try:
      self.store.get(full_key, dest)
    except SandboxError:
      return None
    if not dest.is_file() or not dest.read_bytes():
      return None
    return Mount(LocalFile(dest), read_only=True)

  def _write_record(
      self,
      outcomes: Sequence[EntryOutcome],
      run_ts: str,
      *,
      instance_id: str,
      edges: Mapping[str, Mapping[str, str]],
  ) -> str:
    """Derive and write the workflow record — last, atomically.

    A roll-up of the entries' final attempt records (nothing new is
    measured): per entry its key, attempts, resumed flag, and artifact keys,
    plus the resolved edge map. v1 records success only (ADR-0007 §10).

    Args:
      outcomes: Every entry's outcome, all ``SUCCEEDED``.
      run_ts: The launch timestamp, recorded.
      instance_id: The bound instance, naming the record's prefix.
      edges: The bound edge map, recorded as resolved.

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
        f"{self.sweep_id}/{instance_id}/r{self.rollout_id}"
        f"/{WORKFLOW_RECORD_NAME}"
    )
    self.store.put_bytes(
        key,
        json.dumps(
            {
                "run_ts": run_ts,
                "entries": entries_json,
                "edges": {k: dict(v) for k, v in edges.items()},
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
    )
    return key


def validate_declaration(entries: Sequence[WorkflowEntry]) -> None:
  """Check everything a list of entries can be judged on by itself.

  What a definition can be wrong about with no instance and no store in hand:
  its keys, and its bindings' syntax and targets. A registry runs this at
  import, and every ``Workflow`` runs it at construction, so both fail in the
  same place for the same reason. The *edges* are not resolved here — output
  schemas can be instance-derived, so that waits for the bind (§5).

  Args:
    entries: The steps, in declared order.

  Raises:
    WorkflowError: If the declaration is malformed — no entries, duplicate or
      malformed keys, the reserved key, or a binding that is malformed,
      duplicated, or names an input its task does not declare.
  """
  if not entries:
    raise WorkflowError("a workflow needs at least one entry")
  keys = [entry.key for entry in entries]
  if len(set(keys)) != len(keys):
    raise WorkflowError(f"duplicate entry keys: {sorted(keys)}")
  if INPUTS_KEY in keys:
    raise WorkflowError(
        f"entry key {INPUTS_KEY!r} is reserved for the workflow's own inputs"
    )
  for entry in entries:
    # TaskAddress re-validates each key's shape; building one here surfaces a
    # malformed key at declaration, with the entry named.
    try:
      _ = TaskAddress(sweep_id="", rollout_id=0, task=entry.key)
    except ValueError as error:
      raise WorkflowError(f"entry {entry.key!r}: {error}") from error
    _ = _parse_bindings(entry, {s.name for s in entry.task.input_schema()})


def _resolve_edges(
    entries: Sequence[WorkflowEntry],
    instance: TaskInstance[Any],
    *,
    provided: set[str],
) -> dict[str, dict[str, str]]:
  """Resolve every input to its producing source at bind time (phase A).

  Inputs are pure declaration (``input_schema()`` is fixed by task
  configuration), but outputs are not: an entry's observers — and therefore
  the names it produces — may be derived from the instance, so the map is
  computed once the instance is bound. Still before any sandbox exists. The
  caller's own ``inputs`` participate as the producer ``"inputs"``, existing
  before every entry.

  Args:
    entries: The workflow's entries, in declared (topological) order.
    instance: The bound instance, from which each entry's observers derive.
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
    schemas = {s.name: s for s in entry.task.input_schema()}
    explicit = _parse_bindings(entry, set(schemas))
    bound: dict[str, str] = {}
    for name in sorted(schemas):
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
          # Two ways an unproduced name is not a dangling edge. A task with a
          # builder fills its own inputs in-session — the standalone shape,
          # where requiredness is verified there, before the action. And an
          # *optional* input is optional here too, exactly as ``execute``
          # treats it: a workflow that simply does not supply one is valid.
          # (A name that *is* produced still binds by edge either way, and
          # then the builder's own collision check has the last word.)
          if (
              entry.task.inputs_builder is not None
              or not schemas[name].required
          ):
            continue
          raise WorkflowError(
              f"nothing produces {name!r}, required by {entry.key}: no"
              " earlier entry declares it, the workflow's inputs do not"
              " provide it, and the task builds no inputs of its own"
          )
        else:
          raise WorkflowError(
              f"{name!r} is produced by {candidates}; bind it explicitly on"
              f' {entry.key} (inputs=("<producer>/{name}",))'
          )
    edges[entry.key] = bound
    for schema in merge_output_schemas(
        *(o.output_schema() for o in entry.task.observers(instance))
    ):
      produced.setdefault(schema.name, []).append(entry.key)
  return edges


def _synthesize_config(
    entry: WorkflowEntry, prototype: SandboxConfig
) -> SandboxConfig:
  """Merge an entry's declared sandbox onto the invocation's prototype.

  Two layers meet here (the workspace is a third, added per attempt by
  ``run_task``): the invocation brings the backend and its mechanics, the
  entry brings the run semantics it declared, and the entry's win — they are
  what the workflow *means* (an eval that declared ``network=False`` does not
  get the network back because someone ran it differently). An entry that
  declared a backend's own config subclass wins on that subclass's fields
  too, and only that backend can run it.

  Args:
    entry: The entry whose declaration is merged in.
    prototype: The invocation's config for this run.

  Returns:
    The config this entry's attempts are built from.

  Raises:
    WorkflowError: If the entry declared a backend config the invocation's
      prototype is not an instance of.
  """
  declared = type(entry.sandbox)
  if not isinstance(prototype, declared):
    raise WorkflowError(
        f"entry {entry.key!r} declares {declared.__name__}, which this run's"
        f" {type(prototype).__name__} cannot realize"
    )
  overrides: dict[str, Any] = {
      f.name: getattr(entry.sandbox, f.name)
      for f in fields(declared)
      if f.name != "workspace"
  }
  return replace(prototype, **overrides)


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
