"""Shared CLI persistence wiring: a local `Store` + a post-run `persist`.

The CLI is the entry point that owns the tier decision (`--persist` opts a run
into T1) and injects the launch timestamp — the engine never reads the clock.
Task 13 swaps ``build_store("filesystem", …)`` for ``"s3"`` (R2) with no change
here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, UTC

from etils import epath

from swe_lab.paths import cache_root
from swe_lab.sandbox import (
    AttemptRecord,
    build_store,
    RUNS_NAMESPACE,
    Store,
)

_STORE_SUBDIR = "store"


def run_ts() -> str:
  """Return a compact, sortable UTC launch timestamp for the record."""
  return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def run_store(
    root: epath.PathLike, *, persist_to_t1: bool, scratch: epath.Path
) -> Store:
  """Return the store a command's run persists through.

  Task-level running always persists — every attempt is evidence, and the
  edges of a chain resolve through the store — so ``--persist`` no longer
  decides *whether* but *where*: the shared T1 store, or a throwaway one under
  the run's own output directory, which the next run of the command wipes with
  it.

  Args:
    root: The repo root (locates the cache-backed T1 store).
    persist_to_t1: Whether this run was opted into T1 (``--persist``).
    scratch: The run's own directory, which holds the throwaway store.

  Returns:
    The store to run against.
  """
  if persist_to_t1:
    return local_store(root)
  return build_store("filesystem", root=scratch / _STORE_SUBDIR)


def local_store(root: epath.PathLike) -> Store:
  """Return the default T1 store: a ``FilesystemStore`` under the cache.

  The ``runs`` namespace is part of the store's *root*, not of any key
  (ADR-0004), so keys stay ``<sweep>/<instance>/<rollout>/<attempt>`` and a
  shared cloud bucket (task 13) still keeps runs apart from future siblings.
  """
  return build_store(
      "filesystem", root=cache_root(root) / _STORE_SUBDIR / RUNS_NAMESPACE
  )


def new_record(
    *,
    sweep: str,
    instance_id: str,
    task: str,
    status: str,
    backend: str,
    rollout_id: int = 0,
    attempt: int = 0,
    model: str = "",
    metrics: Mapping[str, float] | None = None,
    extra: Mapping[str, object] | None = None,
) -> AttemptRecord:
  """Build a ``formal``-tier record with a freshly injected launch timestamp.

  ``rollout_id`` / ``attempt`` default to the single-rollout, first-try case;
  a pass@K sweep passes the sample index, and a retry bumps the attempt.
  ``task`` is required — every record names its task (ADR-0007 §6).
  """
  return AttemptRecord(
      sweep_id=sweep,
      instance_id=instance_id,
      task=task,
      rollout_id=rollout_id,
      attempt=attempt,
      run_ts=run_ts(),
      status=status,
      tier="formal",
      backend=backend,
      model=model,
      metrics=dict(metrics or {}),
      extra=dict(extra or {}),
  )
