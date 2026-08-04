"""The per-task orchestrator: resume check → attempt loop → terminal marker.

``run_task`` makes one task's execution durable and repeatable (ADR-0007
§§6–7): every attempt runs in a fresh sandbox and is persisted under its
task-keyed store prefix — success or not, failures are evidence — the task's
own hooks decide validity and retry, and a terminal marker (written last,
atomically) is what a later process resumes against. A workflow calls this
once per entry; a caller with a single task calls it directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from enum import StrEnum
import json
import re
from typing import Any

from etils import epath

from swe_lab.datasets.instance import TaskInstance
from swe_lab.sandbox import (
    AttemptRecord,
    Mounts,
    persist,
    sandbox_factory,
    SandboxConfig,
    SandboxError,
    SandboxObserver,
    Store,
)

from .task import AttemptResult, Task

# The terminal marker's object name under the task prefix (ADR-0007 §7).
MARKER_NAME = "complete.json"

# The task segment of the store key: kept to characters that read unambiguously
# in a path and can never collide with the `r<N>` / `a<N>` segments.
_TASK_KEY_RE = re.compile(r"[a-z][a-z0-9_-]*\Z")


class TaskOutcome(StrEnum):
  """How a task-level run ended — both values are terminal (ADR-0007 §7)."""

  SUCCEEDED = "succeeded"
  FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TaskAddress:
  """Where one task's runs live in the store: the ADR-0004 key, sans attempt.

  Attributes:
    sweep_id: The sweep the run belongs to (``adhoc`` for a one-off).
    rollout_id: Which sample of the instance.
    task: The task segment — the workflow-entry key (``rollout``,
      ``unit_test``).
  """

  sweep_id: str
  rollout_id: int
  task: str

  def __post_init__(self) -> None:
    """Refuse a task key that would corrupt the store layout.

    Raises:
      ValueError: If ``task`` is not a well-formed key segment.
    """
    if not _TASK_KEY_RE.match(self.task):
      raise ValueError(
          f"task key {self.task!r} must match {_TASK_KEY_RE.pattern}"
      )

  def prefix(self, instance_id: str) -> str:
    """Return the task prefix ``<sweep>/<instance>/r<rollout>/<task>``."""
    return f"{self.sweep_id}/{instance_id}/r{self.rollout_id}/{self.task}"


@dataclass(frozen=True, slots=True)
class TerminalMarker:
  """The task's terminal state, as stored in ``complete.json``.

  Attributes:
    outcome: How the task ended; both values are terminal.
    attempts: How many attempts were spent (last index + 1).
    run_ts: The launch timestamp of the run that wrote the marker.
  """

  outcome: TaskOutcome
  attempts: int
  run_ts: str

  def to_json(self) -> str:
    """Serialize to stable JSON."""
    return json.dumps(
        {
            "outcome": self.outcome.value,
            "attempts": self.attempts,
            "run_ts": self.run_ts,
        },
        indent=2,
        sort_keys=True,
    )

  @classmethod
  def from_json(cls, text: str) -> TerminalMarker:
    """Read a marker back from its JSON."""
    data = json.loads(text)
    return cls(
        outcome=TaskOutcome(data["outcome"]),
        attempts=int(data["attempts"]),
        run_ts=str(data["run_ts"]),
    )


def read_marker(
    store: Store, address: TaskAddress, instance_id: str
) -> TerminalMarker | None:
  """Read a task's terminal marker; ``None`` means "not terminal, run it".

  Args:
    store: The store to read from.
    address: The task's address in it.
    instance_id: The instance the address's rollout belongs to.

  Returns:
    The marker, or ``None`` when the task never reached a terminal state.
  """
  key = f"{address.prefix(instance_id)}/{MARKER_NAME}"
  try:
    return TerminalMarker.from_json(store.get_bytes(key).decode("utf-8"))
  except SandboxError:
    return None


@dataclass(frozen=True)
class TaskRunOutcome:
  """What ``run_task`` reports back for one task-level run.

  Attributes:
    outcome: The terminal outcome (fresh or resumed — both are authoritative).
    resumed: Whether a terminal marker made this run a no-op.
    attempts: Attempts spent by whichever process finished the task.
    record: The final attempt's shard — held directly when this process ran
      it, read back from the store when resumed. Always present: an attempt is
      persisted before the marker that ends the task, so a run that reports an
      outcome has a record behind it either way. A workflow edge resolves
      upstream artifacts through it (``record.artifact_keys``), never by
      assembling keys by hand.
    result: The final attempt's in-memory result; ``None`` when resumed (a
      resumed task ran in another process — only the store survives it).
  """

  outcome: TaskOutcome
  resumed: bool
  attempts: int
  record: AttemptRecord
  result: AttemptResult | None


def _final_shard(
    shards: Sequence[AttemptRecord],
    marker: TerminalMarker,
    address: TaskAddress,
    instance_id: str,
) -> AttemptRecord:
  """Return the shard the marker was written for — never an outlived one.

  A forced re-run (``resume=False``) overwrites attempts from ``a0`` and may
  spend **fewer** of them, so a later attempt's shard can outlive the run that
  wrote it: after a two-attempt run followed by a one-attempt re-run, ``a1``
  is still in the store while the marker says one attempt. Taking the last
  shard would hand a downstream edge the *older* run's artifacts, silently
  contradicting the marker that resume just trusted.

  The marker names both the run (``run_ts``) and how many attempts it spent,
  so the final shard is the one matching both — and it **must** be there. The
  marker is written last, after that shard is durable (ADR-0007 §7), so its
  absence is not a state this system can reach: the store lost data, or
  something wrote a marker that never ran. Resume trusts the marker to skip
  work entirely, so it verifies the one thing that trust rests on.

  Args:
    shards: The task's persisted attempt records.
    marker: The terminal marker resume read.
    address: The task's address, for the message.
    instance_id: The instance, for the message.

  Returns:
    The marker's own final attempt record.

  Raises:
    SandboxError: If no shard matches the marker — the task's evidence is
      gone, and a resume that carried on would report a success nothing backs.
  """
  attempt = marker.attempts - 1
  for shard in shards:
    if shard.attempt == attempt and shard.run_ts == marker.run_ts:
      return shard
  # Deliberately not "treat it as un-run and do it again": that would burn
  # budget on an impossible state, and would re-enter a task the marker says
  # is terminally failed. The remedy is explicit — re-run with resume off,
  # which overwrites from a0 — and the operator should know they needed it.
  raise SandboxError(
      f"{address.prefix(instance_id)}: the terminal marker claims"
      f" {marker.outcome.value} after {marker.attempts} attempt(s) at"
      f" {marker.run_ts!r}, but no shard matches (a{attempt} of that run is"
      f" not in the store; found"
      f" {[(s.attempt, s.run_ts) for s in shards]}). The marker is written"
      " last, so this cannot happen to a store that kept what it was given."
      " Re-run this task with resume disabled to rebuild it."
  )


def _over_a_fresh_workspace(
    config: SandboxConfig, workspace: epath.Path
) -> SandboxConfig:
  """Return the attempt's config: the declared one, over its own workspace.

  Allocation is the runner's, not the caller's — that is what makes "a fresh
  sandbox per attempt" a property of this loop rather than a contract a
  factory has to be trusted to honor. A backend whose config has no workspace
  places its own files and is handed the config unchanged.

  Args:
    config: The task's declared sandbox config.
    workspace: The directory this attempt runs in.

  Returns:
    The config to build this attempt's sandbox from.
  """
  if not any(f.name == "workspace" for f in fields(type(config))):
    return config
  overrides: dict[str, Any] = {"workspace": workspace}
  return replace(config, **overrides)


def run_task(
    task: Task,
    instance: TaskInstance[Any],
    *,
    store: Store,
    address: TaskAddress,
    backend: str,
    sandbox: SandboxConfig,
    output_dir: epath.PathLike,
    timeout: float,
    retries: int = 0,
    resume: bool = True,
    run_ts: str,
    model: str = "",
    extra_mounts: Mounts | None = None,
    extra_observers: Sequence[SandboxObserver] = (),
    extra_record: Mapping[str, object] | None = None,
) -> TaskRunOutcome:
  """Run one task durably: resume check, attempt loop, terminal marker.

  The write path, in order (the persistence walk-through of the task-20
  design): read the marker — a terminal task is never re-entered; then for
  each attempt, build a **fresh sandbox** — the declared config over a fresh,
  empty workspace this function allocates (``<output_dir>/ws/a<N>``) —
  execute, judge validity with the task's own ``outputs_valid``, persist the
  attempt's artifacts and record shard whether or not it was valid, and ask
  the task's ``should_retry`` about another attempt; finally write the marker
  — last, atomically — keyed off the final attempt's validity.

  Preemption costs nothing by construction: a killed process writes neither
  shard nor marker for its in-flight attempt, so a resume simply runs the
  task again from ``a0``, deterministically overwriting the dead attempts.

  Args:
    task: The task to run (a declaration — re-executed as-is per attempt).
    instance: The instance to run it against; supplies the store key's
      instance segment and reaches every hook through ``execute``.
    store: Where attempts, records, and the marker are persisted.
    address: The task's store address (sweep / rollout / task key).
    backend: The registered sandbox backend to build each attempt on; also
      recorded on the shards.
    sandbox: The backend's config for this task — run semantics plus that
      backend's mechanics. The workspace is **not** the caller's to set: one
      is allocated per attempt, so no two attempts can ever share state.
    output_dir: Host directory for the run — the attempts' collected
      artifacts (``a0``, ``a1``, …) and their sandbox workspaces
      (``ws/a0``, …).
    timeout: Seconds before each attempt's main action is killed.
    retries: Extra attempts after the first (``0`` = single attempt). The
      budget absorbs validation failures and infrastructure failures alike.
    resume: Honor an existing terminal marker (the workflow default).
      ``False`` skips the check and runs fresh, overwriting attempts and
      marker — the one-off CLI shape, where re-running a command means
      re-running it.
    run_ts: Launch timestamp, injected by the caller — recorded, never read.
    model: The agent model alias, recorded on the shards.
    extra_mounts: Resolved inputs for the task (a workflow edge, or a
      standalone caller's own bytes), passed through to ``execute``.
    extra_observers: Extra observers, passed through to ``execute``.
    extra_record: Additional facts merged into each attempt's record
      ``extra`` (e.g. the instance's run provenance).

  Returns:
    The terminal outcome — resumed or freshly earned. A task-assembly error
    (``execute``'s ``SandboxError``: duplicate mounts/outputs, a required
    input nobody staged) propagates from the first attempt, as does a store
    that contradicts its own marker on resume (see :func:`_final_shard`).

  Raises:
    ValueError: If ``retries`` is negative.
  """
  if retries < 0:
    raise ValueError(f"retries must be >= 0, got {retries}")
  instance_id = instance.instance_id

  marker = read_marker(store, address, instance_id) if resume else None
  if marker is not None:
    shards = store.read_manifest(
        address.sweep_id, instance_id, address.rollout_id, task=address.task
    )
    return TaskRunOutcome(
        outcome=marker.outcome,
        resumed=True,
        attempts=marker.attempts,
        record=_final_shard(shards, marker, address, instance_id),
        result=None,
    )

  result: AttemptResult | None = None
  valid = False
  record: AttemptRecord | None = None
  attempt = 0
  for attempt in range(retries + 1):
    built = sandbox_factory(backend)(
        instance.sandbox_spec(),
        _over_a_fresh_workspace(
            sandbox, epath.Path(output_dir) / "ws" / f"a{attempt}"
        ),
    )
    result = task.execute(
        built,
        instance,
        output_dir=epath.Path(output_dir) / f"a{attempt}",
        timeout=timeout,
        extra_mounts=extra_mounts,
        extra_observers=extra_observers,
    )
    valid = task.outputs_valid(result)
    # Persist the attempt, valid or not: the failing attempt is evidence,
    # and a record exists for every container that was paid for. The engine
    # error travels too — a shard whose status says SETUP_ERROR with nothing
    # to read is exactly the debugging dead end downstream has hit.
    error = result.run.error
    extra: dict[str, object] = {"outputs_valid": valid}
    if error is not None:
      extra["error"] = repr(error)
    record = persist(
        store,
        AttemptRecord(
            sweep_id=address.sweep_id,
            instance_id=instance_id,
            task=address.task,
            rollout_id=address.rollout_id,
            attempt=attempt,
            run_ts=run_ts,
            status=result.run.status.value,
            tier="formal",
            backend=backend,
            model=model,
            metrics=dict(result.run.metrics),
            extra=extra | dict(extra_record or {}),
        ),
        result.run.artifacts,
    )
    if not task.should_retry(result):
      break

  # The loop runs at least once (a non-negative budget), and every attempt
  # persists before anything else can end the task.
  assert record is not None
  outcome = TaskOutcome.SUCCEEDED if valid else TaskOutcome.FAILED
  # The marker goes last, after the attempts' artifacts and shards are
  # durable, and atomically (ADR-0007 §7): a crash before it re-runs the
  # task; a torn write must never read as complete.
  store.put_bytes(
      f"{address.prefix(instance_id)}/{MARKER_NAME}",
      TerminalMarker(outcome=outcome, attempts=attempt + 1, run_ts=run_ts)
      .to_json()
      .encode("utf-8"),
  )
  return TaskRunOutcome(
      outcome=outcome,
      resumed=False,
      attempts=attempt + 1,
      record=record,
      result=result,
  )
