"""Dataset-agnostic loader over the downloaded parquet files.

The pieces here know nothing about any specific dataset's columns: a
:class:`Dataset` is just an ordered, id-indexed collection of records, and
:func:`load_parquet` turns a parquet into one given a *record type* that knows
its own columns and how to parse a row (the :class:`DatasetRecord` protocol).
Dataset-specific knowledge lives in per-dataset modules such as
``swebench_pro``; new datasets are added by writing a record type and
registering it in ``_DATASET_RECORDS`` — no change to the code below.

Order matches the parquet row order, and the positional index is stable for a
given file — later milestones derive a per-instance proxy port from it
(``base_port + index``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import ClassVar, Protocol

from etils import epath
import polars as pl

from ..paths import datasets_root
from .deepswe.fetch import ensure_deepswe_parquet
from .deepswe.record import DeepSweInstance
from .swebench_pro import SweBenchProInstance


class DatasetRecord(Protocol):
  """One row of a dataset: knows its columns and how to parse a raw row."""

  COLUMNS: ClassVar[tuple[str, ...]]

  # Read-only so frozen-dataclass records satisfy the protocol.
  @property
  def instance_id(self) -> str:
    """The unique id of this record within its dataset."""
    ...

  @classmethod
  def from_raw(cls, raw: Mapping[str, str]) -> DatasetRecord:
    """Parse one raw parquet row into a record."""
    ...


# Registry of known datasets: name -> record type. Add a dataset by writing its
# record type (see ``swebench_pro``) and registering it here.
_DATASET_RECORDS: dict[str, type[DatasetRecord]] = {
    "deepswe": DeepSweInstance,
    "swebench_pro": SweBenchProInstance,
}

# Datasets that can materialize (and must verify) their own parquet before the
# generic lookup runs. Called on every load, not only on a missing file — for
# a pinned dataset the verification IS the point (the pin is the trust
# anchor; see ``deepswe.fetch``).
_DATASET_PREPARERS: dict[str, Callable[[epath.Path], object]] = {
    "deepswe": ensure_deepswe_parquet,
}


class Dataset:
  """An ordered collection of records loaded from one parquet file."""

  def __init__(
      self, name: str, path: epath.PathLike, records: tuple[DatasetRecord, ...]
  ) -> None:
    """Initialize the dataset and index its records by instance id.

    Args:
      name: The dataset name (registry key, or the parquet file stem).
      path: The parquet file the records were loaded from.
      records: The parsed records, in parquet row order.

    Raises:
      ValueError: If two records share an ``instance_id``.
    """
    self.name: str = name
    self.path: epath.Path = epath.Path(path)
    self._records: tuple[DatasetRecord, ...] = records
    self._by_id: dict[str, int] = {
        rec.instance_id: i for i, rec in enumerate(records)
    }
    if len(self._by_id) != len(records):
      raise ValueError(
          f"Dataset {name!r} contains duplicate instance_id values."
      )

  def __len__(self) -> int:
    return len(self._records)

  def __iter__(self) -> Iterator[DatasetRecord]:
    return iter(self._records)

  def __getitem__(self, index: int) -> DatasetRecord:
    return self._records[index]

  @property
  def records(self) -> tuple[DatasetRecord, ...]:
    """The records in parquet row order."""
    return self._records

  def get(self, instance_id: str) -> DatasetRecord | None:
    """Return the record with this id, or ``None`` if absent."""
    index = self._by_id.get(instance_id)
    return None if index is None else self._records[index]

  def require(self, instance_id: str) -> DatasetRecord:
    """Return the record with this id, raising ``KeyError`` if absent."""
    record = self.get(instance_id)
    if record is None:
      raise KeyError(f"No instance {instance_id!r} in dataset {self.name!r}.")
    return record

  def index_of(self, instance_id: str) -> int:
    """Return the stable positional index of an instance id."""
    index = self._by_id.get(instance_id)
    if index is None:
      raise KeyError(f"No instance {instance_id!r} in dataset {self.name!r}.")
    return index

  def filter(
      self, predicate: Callable[[DatasetRecord], bool]
  ) -> tuple[DatasetRecord, ...]:
    """Return records matching ``predicate``, preserving order."""
    return tuple(rec for rec in self._records if predicate(rec))


def find_parquet(
    name: str, *, root: epath.PathLike | None = None
) -> epath.Path:
  """Locate the single parquet file for a dataset under ``datasets/<name>``."""
  data_dir = epath.Path(root or datasets_root()) / name / "data"
  if not data_dir.is_dir():
    raise FileNotFoundError(
        f"Dataset directory not found: {data_dir}. See datasets/README.md for"
        " download instructions."
    )
  parquets = sorted(data_dir.glob("*.parquet"))
  if not parquets:
    raise FileNotFoundError(
        f"No parquet files in {data_dir}. Download the dataset first (see"
        " datasets/README.md)."
    )
  if len(parquets) > 1:
    raise ValueError(
        f"Expected exactly one parquet in {data_dir}, found {len(parquets)}:"
        f" {[p.name for p in parquets]}."
    )
  return parquets[0]


def load_parquet(
    path: epath.PathLike,
    record_type: type[DatasetRecord],
    *,
    name: str | None = None,
) -> Dataset:
  """Load a :class:`Dataset` from a parquet file using ``record_type``."""
  frame = pl.read_parquet(str(path))
  missing = [c for c in record_type.COLUMNS if c not in frame.columns]
  if missing:
    raise ValueError(f"{path} is missing expected columns: {missing}")

  records = tuple(
      record_type.from_raw(row) for row in frame.iter_rows(named=True)
  )
  return Dataset(name or epath.Path(path).stem, path, records)


def load_dataset(
    name: str = "swebench_pro", *, root: epath.PathLike | None = None
) -> Dataset:
  """Load a registered dataset by name from the ``datasets/<name>`` layout."""
  preparer = _DATASET_PREPARERS.get(name)
  if preparer is not None:
    _ = preparer(epath.Path(root or datasets_root()) / name / "data")
  record_type = _DATASET_RECORDS.get(name)
  if record_type is None:
    known = ", ".join(sorted(_DATASET_RECORDS)) or "(none)"
    raise KeyError(f"Unknown dataset {name!r}. Known datasets: {known}.")
  path = find_parquet(name, root=root)
  return load_parquet(path, record_type, name=name)
