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
from typing import override

from etils import epath

from .errors import SandboxError
from .persist import MANIFEST_NAME, run_prefix, RunRecord


class Store(ABC):
  """Durable home for T1 run artifacts + their manifest shards.

  A behavior interface (ABC, per ADR-0002). Keys are ``/``-separated POSIX-style
  paths; a store maps them onto its own layout (a local dir, an S3 prefix, …).
  """

  @abstractmethod
  def put(self, key: str, src: epath.PathLike) -> None:
    """Upload one file to ``key`` (overwriting)."""
    ...

  @abstractmethod
  def get(self, key: str, dest: epath.PathLike) -> None:
    """Download ``key`` to the host path ``dest`` (parents created)."""
    ...

  @abstractmethod
  def append_manifest(self, record: RunRecord) -> None:
    """Write one run's manifest shard (``<run-key>/run.json``)."""
    ...

  @abstractmethod
  def read_manifests(self, sweep_id: str) -> list[RunRecord]:
    """Read every run shard under a sweep, ordered by identity.

    The bulk read, for aggregation (``index``) and pass@K metrics. On a cloud
    store this is a broad listing — prefer :meth:`read_manifest` when only one
    rollout is in question.

    Args:
      sweep_id: The sweep to aggregate.

    Returns:
      Every shard, sorted by ``(instance_id, rollout_id, attempt)``.
    """
    ...

  @abstractmethod
  def read_manifest(
      self, sweep_id: str, instance_id: str, rollout_id: int
  ) -> list[RunRecord]:
    """Read the attempts of **one** rollout (usually one shard).

    The targeted read a resume/retry check wants: a narrow prefix lookup rather
    than scanning the whole sweep.

    Args:
      sweep_id: The sweep the rollout belongs to.
      instance_id: The dataset instance.
      rollout_id: Which sample of that instance.

    Returns:
      That rollout's shards, ordered by ``attempt``; empty if it never ran.
    """
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

  root: epath.Path

  def _path(self, key: str) -> epath.Path:
    return self.root / key

  @override
  def put(self, key: str, src: epath.PathLike) -> None:
    """Copy ``src`` to ``root/key``."""
    dest = self._path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = epath.Path(src).copy(dest, overwrite=True)

  @override
  def get(self, key: str, dest: epath.PathLike) -> None:
    """Copy ``root/key`` to ``dest``."""
    src = self._path(key)
    if not src.is_file():
      raise SandboxError(f"store key not found: {key}")
    dest = epath.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = epath.Path(src).copy(dest, overwrite=True)

  @override
  def append_manifest(self, record: RunRecord) -> None:
    """Write the run's shard JSON under its key."""
    shard = self._path(f"{run_prefix(record)}/{MANIFEST_NAME}")
    shard.parent.mkdir(parents=True, exist_ok=True)
    _ = shard.write_text(record.to_json())

  @override
  def read_manifests(self, sweep_id: str) -> list[RunRecord]:
    """Read every shard under the sweep (``<instance>/r<n>/a<n>``)."""
    return self._read(f"{sweep_id}/*/r*/a*/{MANIFEST_NAME}")

  @override
  def read_manifest(
      self, sweep_id: str, instance_id: str, rollout_id: int
  ) -> list[RunRecord]:
    """Read just one rollout's attempts, without scanning the sweep."""
    return self._read(
        f"{sweep_id}/{instance_id}/r{rollout_id}/a*/{MANIFEST_NAME}"
    )

  def _read(self, pattern: str) -> list[RunRecord]:
    """Load the shards matching a glob, sorted numerically by identity.

    Sorting the parsed records (not the path strings) is what keeps rollout
    ``10`` after ``2``: a lexical key sort would interleave them, and this needs
    no zero-padding in the key.
    """
    if not self.root.is_dir():
      return []
    records = [
        RunRecord.from_json(shard.read_text())
        for shard in self.root.glob(pattern)
    ]
    return sorted(records, key=lambda record: record.sort_key)


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


def _build_filesystem(root: epath.PathLike) -> Store:
  return FilesystemStore(epath.Path(root))


register_store("filesystem", _build_filesystem)
