"""The post-run persist step: a finished run → T1 store + a manifest shard.

Persistence is a **post-run consumer**, not an observer (task 12 §4): the
manifest needs the final ``status`` / ``metrics`` the manager assembles *after*
teardown, and the ``fetch``/collect seam (ADR-0003) has already landed the
registered artifacts on the host. So the composition, after the run, hands the
finished outcome here — no engine hook, no ``PersistObserver``.

``persist`` uploads a run's artifacts under its key and appends one per-run
:class:`RunRecord` shard; ``promote`` does the same for a whole debug workspace
(the misclassification safety valve); ``index`` aggregates a sweep's shards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
import json
import pathlib
from typing import TYPE_CHECKING

from etils import epath

if TYPE_CHECKING:
  from .store import Store

# Every run's artifacts + shard live under this key prefix.
RUNS_PREFIX = "runs"
# The manifest shard each run writes (one per run → race-free under a sweep).
MANIFEST_NAME = "run.json"


@dataclass(frozen=True, slots=True)
class RunRecord:
  """One T1 manifest shard — the ledger entry for a single persisted run.

  Failures are recorded too (persistence gates on tier, not success). The
  timestamp is *injected* at launch (``run_ts``), never read inside the engine,
  so a run is reproducible and the record is testable.

  Attributes:
    sweep_id: The sweep this run belongs to (``adhoc`` for a one-off).
    instance_id: The dataset instance.
    run_ts: Launch timestamp, injected by the caller (keys the run).
    status: The engine ``RunStatus`` value the run ended with.
    tier: The persistence tier — always ``formal`` here (debug never persists).
    backend: The sandbox backend name the run used.
    model: The agent model alias (empty for a grading-only run).
    artifacts: Object name → its full store key (filled by :func:`persist`).
    metrics: Scalar metrics from the run.
    extra: Any other run facts (e.g. ``is_empty_patch``, an error repr).
  """

  sweep_id: str
  instance_id: str
  run_ts: str
  status: str
  tier: str
  backend: str
  model: str = ""
  artifacts: dict[str, str] = field(default_factory=dict)
  metrics: dict[str, float] = field(default_factory=dict)
  extra: dict[str, object] = field(default_factory=dict)

  def to_json(self) -> str:
    """Serialize the shard to pretty, stable JSON."""
    return json.dumps(asdict(self), indent=2, sort_keys=True)

  @classmethod
  def from_json(cls, text: str) -> RunRecord:
    """Read a shard back from its JSON."""
    return cls(**json.loads(text))


def run_prefix(record: RunRecord) -> str:
  """Return the run key prefix ``runs/<sweep>/<instance>/<run-ts>``."""
  return f"{RUNS_PREFIX}/{record.sweep_id}/{record.instance_id}/{record.run_ts}"


def persist(
    store: Store, record: RunRecord, files: Mapping[str, epath.PathLike]
) -> RunRecord:
  """Upload a run's files under its key and append its manifest shard.

  Args:
    store: The T1 store to write to.
    record: The run's metadata (its ``artifacts`` field is filled in here).
    files: Object name (the key suffix under the run prefix) → host path.

  Returns:
    The completed record (``artifacts`` = object name → full store key), as
    written to the manifest.
  """
  prefix = run_prefix(record)
  artifacts: dict[str, str] = {}
  for name, path in files.items():
    key = f"{prefix}/{name}"
    store.put(key, path)
    artifacts[name] = key
  completed = replace(record, artifacts=artifacts)
  store.append_manifest(completed)
  return completed


def promote(
    store: Store, record: RunRecord, workspace: epath.PathLike
) -> RunRecord:
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


def index(store: Store, sweep_id: str) -> list[RunRecord]:
  """Aggregate a sweep's per-run shards into one list (ordered by key)."""
  return store.read_manifest(sweep_id)
