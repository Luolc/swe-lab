"""The per-task orchestrator: resume check → attempt loop → terminal marker.

``run_task`` makes one task's execution durable and repeatable (ADR-0007
§§6–7): every attempt runs in a fresh sandbox and is persisted under its
task-keyed store prefix — success or not, failures are evidence — the task's
own hooks decide validity and retry, and a terminal marker (written last,
atomically) is what a later process resumes against. A workflow calls this
once per entry; a caller with a single task calls it directly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import re

from etils import epath

from swe_lab.sandbox import (
    AttemptRecord,
    Mounts,
    persist,
    Sandbox,
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
    task: The task segment — the workflow-entry key (``rollout``, ``eval``).
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
      it, read back from the store when resumed. A workflow edge resolves
      upstream artifacts through it (``record.artifact_keys``), never by
      assembling keys by hand.
    result: The final attempt's in-memory result; ``None`` when resumed (a
      resumed task ran in another process — only the store survives it).
  """

  outcome: TaskOutcome
  resumed: bool
  attempts: int
  record: AttemptRecord | None
  result: AttemptResult | None


def run_task(
    task: Task,
    *,
    store: Store,
    address: TaskAddress,
    sandbox_factory: Callable[[], Sandbox],
    output_dir: epath.PathLike,
    timeout: float,
    retries: int = 0,
    resume: bool = True,
    run_ts: str,
    backend: str = "",
    model: str = "",
    extra_mounts: Mounts | None = None,
    extra_observers: Sequence[SandboxObserver] = (),
    extra_record: Mapping[str, object] | None = None,
) -> TaskRunOutcome:
  """Run one task durably: resume check, attempt loop, terminal marker.

  The write path, in order (the persistence walk-through of the task-20
  design): read the marker — a terminal task is never re-entered; then for
  each attempt, build a **fresh sandbox** from the factory (every call must
  yield one over a fresh, empty workspace — the factory owns that
  allocation), execute, judge validity with the task's own
  ``outputs_valid``, persist the attempt's artifacts and record shard
  whether or not it was valid, and ask the task's ``should_retry`` about
  another attempt; finally write the marker — last, atomically — keyed off
  the final attempt's validity.

  Preemption costs nothing by construction: a killed process writes neither
  shard nor marker for its in-flight attempt, so a resume simply runs the
  task again from ``a0``, deterministically overwriting the dead attempts.

  Args:
    task: The task to run (a declaration — re-executed as-is per attempt).
    store: Where attempts, records, and the marker are persisted.
    address: The task's store address (sweep / rollout / task key).
    sandbox_factory: Builds each attempt's sandbox, fresh workspace included.
    output_dir: Host directory for the attempts' collected artifacts
      (per-attempt subdirectories ``a0``, ``a1``, …).
    timeout: Seconds before each attempt's main action is killed.
    retries: Extra attempts after the first (``0`` = single attempt). The
      budget absorbs validation failures and infrastructure failures alike.
    resume: Honor an existing terminal marker (the workflow default).
      ``False`` skips the check and runs fresh, overwriting attempts and
      marker — the one-off CLI shape, where re-running a command means
      re-running it.
    run_ts: Launch timestamp, injected by the caller — recorded, never read.
    backend: The sandbox backend name, recorded on the shards.
    model: The agent model alias, recorded on the shards.
    extra_mounts: Resolved inputs for the task (a workflow edge, or a
      standalone caller's own bytes), passed through to ``execute``.
    extra_observers: Extra observers, passed through to ``execute``.
    extra_record: Additional facts merged into each attempt's record
      ``extra`` (e.g. the instance's run provenance).

  Returns:
    The terminal outcome — resumed or freshly earned. A task-assembly error
    (``execute``'s ``SandboxError``: duplicate mounts/outputs, a required
    input nobody staged) propagates from the first attempt.

  Raises:
    ValueError: If ``retries`` is negative.
  """
  if retries < 0:
    raise ValueError(f"retries must be >= 0, got {retries}")
  instance_id = task.instance.instance_id

  marker = read_marker(store, address, instance_id) if resume else None
  if marker is not None:
    shards = store.read_manifest(
        address.sweep_id, instance_id, address.rollout_id, task=address.task
    )
    return TaskRunOutcome(
        outcome=marker.outcome,
        resumed=True,
        attempts=marker.attempts,
        record=shards[-1] if shards else None,
        result=None,
    )

  result: AttemptResult | None = None
  valid = False
  record: AttemptRecord | None = None
  attempt = 0
  for attempt in range(retries + 1):
    sandbox = sandbox_factory()
    result = task.execute(
        sandbox,
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
