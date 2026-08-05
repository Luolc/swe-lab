"""The post-run persist step: a finished run → T1 store + a manifest shard.

Persistence is a **post-run consumer**, not an observer (task 12 §4): the
manifest needs the final ``status`` / ``metrics`` the manager assembles *after*
teardown, and the ``fetch``/collect seam (ADR-0003) has already landed the
registered artifacts on the host. So the composition, after the run, hands the
finished outcome here — no engine hook, no ``PersistObserver``.

``persist`` uploads a run's artifacts under its key and appends one per-run
:class:`AttemptRecord` shard; ``promote`` does the same for a whole debug
workspace (the misclassification safety valve); ``index`` aggregates a sweep's
shards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
import json
import logging
import pathlib
from typing import TYPE_CHECKING

from etils import epath

if TYPE_CHECKING:
  from .store import Store

_logger = logging.getLogger(__name__)

# The namespace a run store's *root* is configured with (ADR-0004): it is not
# part of any key, so a shared bucket keeps runs separated from future siblings
# (traces, datasets, indexes) without repeating the segment in every key.
RUNS_NAMESPACE = "runs"
# The manifest shard each run writes (one per run → race-free under a sweep).
MANIFEST_NAME = "run.json"


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptRecord:
  """One T1 manifest shard — the ledger entry for a single persisted run.

  Failures are recorded too (persistence gates on tier, not success). A run is
  identified by ``(sweep_id, instance_id, rollout_id, task, attempt)`` — which
  is exactly its store key (ADR-0004, key amended by ADR-0007 §6) — so K
  rollouts of one instance are addressable, two tasks of one rollout cannot
  collide even when they produce same-named artifacts, and a retry of one
  task is distinguishable. ``run_ts`` is *injected* at launch, never read
  inside the engine (so a run is reproducible and the record is testable),
  and is recorded rather than keying anything.

  Attributes:
    sweep_id: The sweep this run belongs to (``adhoc`` for a one-off).
    instance_id: The dataset instance.
    task: Which task of this rollout the run belongs to — the workflow-entry
      key (``rollout``, ``unit_test``). Required: every record names its task,
      and the task owns its attempts.
    rollout_id: Which sample of this instance, ``0..K-1`` for pass@K. ``0`` for
      a single-rollout job.
    attempt: Retry index of *this task* (validation or infrastructure
      failure, ADR-0007 §6). ``0`` unless something re-ran it.
    run_ts: Launch timestamp, injected by the caller (recorded, not a key).
    status: The engine ``RunStatus`` value the run ended with.
    tier: The persistence tier — always ``formal`` here (debug never persists).
    backend: The sandbox backend name the run used.
    model: The agent model alias, when a caller records one. **Nothing
      in-tree sets it**: a model is a per-task fact (one entry runs an agent,
      the next grades and has no model at all), so no workflow-level value
      could be right, and the workflow layer no longer carries one. Left
      settable for a caller building records itself (``new_record``).
    artifact_keys: Object name → its full store key (filled by :func:`persist`).
    metrics: Scalar metrics from the run.
    extra: Any other run facts (e.g. ``is_empty_patch``, an error repr).
  """

  sweep_id: str
  instance_id: str
  task: str
  rollout_id: int = 0
  attempt: int = 0
  run_ts: str
  status: str
  tier: str
  backend: str
  model: str = ""
  artifact_keys: dict[str, str] = field(default_factory=dict)
  metrics: dict[str, float] = field(default_factory=dict)
  extra: dict[str, object] = field(default_factory=dict)

  def to_json(self) -> str:
    """Serialize the shard to pretty, stable JSON."""
    return json.dumps(asdict(self), indent=2, sort_keys=True)

  @classmethod
  def from_json(cls, text: str) -> AttemptRecord:
    """Read a shard back from its JSON."""
    return cls(**json.loads(text))

  @property
  def sort_key(self) -> tuple[str, int, str, int]:
    """Identity within a sweep, ordered numerically (not by key string)."""
    return (self.instance_id, self.rollout_id, self.task, self.attempt)


def run_prefix(record: AttemptRecord) -> str:
  """Return the run key ``<sweep>/<instance>/r<rollout>/<task>/a<attempt>``.

  ADR-0004's key, with the task segment ADR-0007 §6 added: the task sits
  between rollout and attempt because the task owns its attempts —
  ``unit_test/a1`` is the grading task's second try, unrelated to
  ``rollout/a0``. The ``r`` / ``a``
  prefixes keep the layout self-describing when browsing the store, the
  ``runs/`` namespace lives in the store's configured root, and ``run_ts`` is
  recorded on the shard rather than keying it, so re-running a given attempt
  deterministically overwrites it.
  """
  return (
      f"{record.sweep_id}/{record.instance_id}"
      f"/r{record.rollout_id}/{record.task}/a{record.attempt}"
  )


def persist(
    store: Store, record: AttemptRecord, files: Mapping[str, epath.PathLike]
) -> AttemptRecord:
  """Upload a run's files under its key and append its manifest shard.

  A path that does not exist is skipped, not fatal. The collect step already
  omits an artifact its ``fetch`` failed to land, so this is defence in depth at
  the layer that would actually raise: the record's value is that it *says* how
  the run went, and a run whose sandbox died mid-collect is precisely when that
  matters most. Dropping the shard over one missing best-effort artifact would
  make the attempt look never-started to a resume or a summary.

  Args:
    store: The T1 store to write to.
    record: The run's metadata (its ``artifact_keys`` field is filled in here).
    files: Object name (the key suffix under the run prefix) → host path.

  Returns:
    The completed record (``artifact_keys`` = object name → full store key, for
    the files that were there), as written to the manifest.
  """
  prefix = run_prefix(record)
  artifact_keys: dict[str, str] = {}
  for name, path in files.items():
    if not epath.Path(path).exists():
      _logger.warning(
          "artifact %r is missing at %s; not persisting it", name, path
      )
      continue
    key = f"{prefix}/{name}"
    store.put(key, path)
    artifact_keys[name] = key
  completed = replace(record, artifact_keys=artifact_keys)
  store.append_manifest(completed)
  return completed


def promote(
    store: Store, record: AttemptRecord, workspace: epath.PathLike
) -> AttemptRecord:
  """Push a whole debug workspace into T1 (the misclassification safety valve).

  Uploads every file under ``workspace`` (keyed by its workspace-relative path,
  so nesting like ``diagnostics/…`` is preserved) and appends a shard.

  Args:
    store: The T1 store to write to.
    record: The run's metadata (``tier`` should be ``formal`` post-promotion).
    workspace: The debug run's workspace directory.

  Returns:
    The completed record.
  """
  # epath.Path has no recursive glob (`rglob`/`**` raise NotImplementedError to
  # avoid unbounded cloud listing), so walk the local workspace via pathlib and
  # wrap the results back into epath.Path.
  base = pathlib.Path(workspace)
  files = {
      path.relative_to(base).as_posix(): epath.Path(path)
      for path in sorted(base.rglob("*"))
      if path.is_file()
  }
  return persist(store, record, files)


def index(store: Store, sweep_id: str) -> list[AttemptRecord]:
  """Aggregate a sweep's per-run shards into one list (ordered by identity)."""
  return store.read_manifests(sweep_id)
