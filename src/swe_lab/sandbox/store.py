"""The T1 persistence seam: a tiny `Store` + an open vendor registry.

A ``Store`` is where formal intermediates (T1: trajectories, patches, per-run
results, diagnostics — including failed runs) are kept durably, keyed by run and
indexed by an append-only manifest of **per-run shards** (task 12 design). The
vendor is **configuration, not architecture**: ``build_store(name, **cfg)`` is
an open registry mirroring ``build_sandbox``, so swapping ``filesystem`` for a
cloud store (``s3`` → R2, task 13) is a config change, not a code change.

``FilesystemStore`` is the default and the cloud-free implementation that makes
the whole persist / promote / index flow unit-testable without any network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import override

from .errors import SandboxError
from .persist import MANIFEST_NAME, run_prefix, RunRecord, RUNS_PREFIX


class Store(ABC):
  """Durable home for T1 run artifacts + their manifest shards.

  A behavior interface (ABC, per ADR-0002). Keys are ``/``-separated POSIX-style
  paths; a store maps them onto its own layout (a local dir, an S3 prefix, …).
  """

  @abstractmethod
  def put(self, key: str, src: Path) -> None:
    """Upload one file to ``key`` (overwriting)."""
    ...

  @abstractmethod
  def get(self, key: str, dest: Path) -> None:
    """Download ``key`` to the host path ``dest`` (parents created)."""
    ...

  @abstractmethod
  def append_manifest(self, record: RunRecord) -> None:
    """Write one run's manifest shard (``<run-prefix>/run.json``)."""
    ...

  @abstractmethod
  def read_manifest(self, sweep_id: str) -> list[RunRecord]:
    """Read every run shard under a sweep (the aggregation the index uses)."""
    ...


@dataclass(frozen=True, slots=True)
class FilesystemStore(Store):
  """A ``Store`` backed by a local directory — the default, cloud-free vendor.

  Keys become paths under ``root``; ``put``/``get`` copy, ``append_manifest``
  writes the shard JSON. Fully exercises the persist / promote / index flow in
  tests with no network.

  Attributes:
    root: The directory keys are resolved under (created on write).
  """

  root: Path

  def _path(self, key: str) -> Path:
    return self.root / key

  @override
  def put(self, key: str, src: Path) -> None:
    """Copy ``src`` to ``root/key``."""
    dest = self._path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(src, dest)

  @override
  def get(self, key: str, dest: Path) -> None:
    """Copy ``root/key`` to ``dest``."""
    src = self._path(key)
    if not src.is_file():
      raise SandboxError(f"store key not found: {key}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(src, dest)

  @override
  def append_manifest(self, record: RunRecord) -> None:
    """Write the run's shard JSON under its key."""
    shard = self._path(f"{run_prefix(record)}/{MANIFEST_NAME}")
    shard.parent.mkdir(parents=True, exist_ok=True)
    _ = shard.write_text(record.to_json())

  @override
  def read_manifest(self, sweep_id: str) -> list[RunRecord]:
    """Read every ``run.json`` shard under the sweep, ordered by key."""
    sweep_dir = self._path(f"{RUNS_PREFIX}/{sweep_id}")
    if not sweep_dir.is_dir():
      return []
    shards = sorted(sweep_dir.glob(f"*/*/{MANIFEST_NAME}"))
    return [RunRecord.from_json(shard.read_text()) for shard in shards]


type StoreFactory = Callable[..., Store]
"""Builds a store from vendor-specific keyword config."""

_REGISTRY: dict[str, StoreFactory] = {}


def register_store(name: str, factory: StoreFactory) -> None:
  """Register a store factory under a ``--store`` name."""
  _REGISTRY[name] = factory


def registered_stores() -> list[str]:
  """Return the registered store names, sorted."""
  return sorted(_REGISTRY)


def build_store(name: str, **cfg: object) -> Store:
  """Construct the named store from vendor-specific config.

  Args:
    name: A registered store name (e.g. ``filesystem``; ``s3`` in task 13).
    **cfg: Passed through to the vendor's factory (e.g. ``root=`` for
      ``filesystem``; ``endpoint=`` / ``bucket=`` for ``s3``).

  Returns:
    The constructed store.

  Raises:
    SandboxError: If ``name`` is not registered.
  """
  try:
    factory = _REGISTRY[name]
  except KeyError:
    raise SandboxError(
        f"unknown store {name!r}; registered: {registered_stores()}"
    ) from None
  return factory(**cfg)


def _build_filesystem(root: str | Path) -> Store:
  return FilesystemStore(Path(root))


register_store("filesystem", _build_filesystem)
