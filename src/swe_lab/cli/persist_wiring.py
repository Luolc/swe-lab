"""Shared CLI persistence wiring: a local `Store` + a post-run `persist`.

The CLI is the entry point that owns the tier decision (`--persist` opts a run
into T1) and injects the launch timestamp — the engine never reads the clock.
Task 13 swaps ``build_store("filesystem", …)`` for ``"s3"`` (R2) with no change
here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, UTC
from pathlib import Path

from swe_lab.paths import cache_root
from swe_lab.sandbox import build_store, persist, RunRecord, Store

_STORE_SUBDIR = "store"


def _run_ts() -> str:
  """Return a compact, sortable UTC launch timestamp for the record."""
  return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def local_store(root: Path) -> Store:
  """Return the default T1 store: a ``FilesystemStore`` under the cache."""
  return build_store("filesystem", root=cache_root(root) / _STORE_SUBDIR)


def new_record(
    *,
    sweep: str,
    instance_id: str,
    status: str,
    backend: str,
    model: str = "",
    metrics: Mapping[str, float] | None = None,
    extra: Mapping[str, object] | None = None,
) -> RunRecord:
  """Build a ``formal``-tier record with a freshly injected launch timestamp."""
  return RunRecord(
      sweep_id=sweep,
      instance_id=instance_id,
      run_ts=_run_ts(),
      status=status,
      tier="formal",
      backend=backend,
      model=model,
      metrics=dict(metrics or {}),
      extra=dict(extra or {}),
  )


def persist_run(
    root: Path,
    *,
    sweep: str,
    instance_id: str,
    status: str,
    backend: str,
    artifacts: Mapping[str, Path],
    model: str = "",
    metrics: Mapping[str, float] | None = None,
    extra: Mapping[str, object] | None = None,
) -> RunRecord:
  """Persist a finished run's artifacts + a manifest shard to the local store.

  Args:
    root: The repo root (locates the cache-backed store).
    sweep: The sweep id this run belongs to (``adhoc`` for a one-off).
    instance_id: The dataset instance.
    status: The engine ``RunStatus`` value the run ended with.
    backend: The sandbox backend name used.
    artifacts: Collected artifacts (name → host path); uploaded by filename.
    model: The agent model alias (empty for a grading-only run).
    metrics: Scalar run metrics.
    extra: Any other run facts to record.

  Returns:
    The written record (its ``artifacts`` are the store keys).
  """
  record = new_record(
      sweep=sweep,
      instance_id=instance_id,
      status=status,
      backend=backend,
      model=model,
      metrics=metrics,
      extra=extra,
  )
  files = {path.name: path for path in artifacts.values()}
  return persist(local_store(root), record, files)
