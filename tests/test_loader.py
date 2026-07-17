"""Tests for the dataset-agnostic parquet loader."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from swe_lab.core.datasets.loader import (
    load_dataset,
    load_parquet,
)
from swe_lab.core.datasets.swebench_pro import (
    COLUMNS,
    SweBenchProInstance,
)


def _row(instance_id: str, language: str = "python") -> dict[str, str]:
  values = dict.fromkeys(COLUMNS, "")
  values.update(
      repo="acme/widget",
      instance_id=instance_id,
      base_commit="0" * 40,
      repo_language=language,
      fail_to_pass="[]",
      pass_to_pass="[]",
      issue_specificity="[]",
      issue_categories="[]",
      selected_test_files_to_run="[]",
  )
  return values


def _write_parquet(path: Path, rows: list[dict[str, str]]) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  schema = dict.fromkeys(COLUMNS, pl.String)
  pl.DataFrame(rows, schema=schema).write_parquet(path)
  return path


def test_load_parquet_preserves_order_and_lookup(tmp_path: Path) -> None:
  rows = [_row("i0"), _row("i1"), _row("i2")]
  path = _write_parquet(tmp_path / "data.parquet", rows)

  ds = load_parquet(path, SweBenchProInstance, name="demo")

  assert ds.name == "demo"
  assert len(ds) == 3
  assert [rec.instance_id for rec in ds] == ["i0", "i1", "i2"]
  assert ds[1].instance_id == "i1"
  assert ds.get("i2") is ds[2]
  assert ds.get("missing") is None
  assert ds.index_of("i2") == 2
  assert ds.require("i0").instance_id == "i0"


def test_filter(tmp_path: Path) -> None:
  rows = [_row("i0"), _row("i1"), _row("i2")]
  ds = load_parquet(
      _write_parquet(tmp_path / "d.parquet", rows), SweBenchProInstance
  )

  selected = ds.filter(lambda r: r.instance_id in {"i0", "i2"})
  assert [r.instance_id for r in selected] == ["i0", "i2"]


def test_require_missing_raises(tmp_path: Path) -> None:
  ds = load_parquet(
      _write_parquet(tmp_path / "d.parquet", [_row("i0")]), SweBenchProInstance
  )
  with pytest.raises(KeyError):
    ds.require("nope")


def test_duplicate_instance_ids_raise(tmp_path: Path) -> None:
  rows = [_row("dup"), _row("dup")]
  path = _write_parquet(tmp_path / "d.parquet", rows)
  with pytest.raises(ValueError, match="duplicate instance_id"):
    load_parquet(path, SweBenchProInstance)


def test_load_dataset_uses_layout(tmp_path: Path) -> None:
  root = tmp_path / "datasets"
  _write_parquet(root / "swebench_pro" / "data" / "test.parquet", [_row("i0")])

  ds = load_dataset("swebench_pro", root=root)
  assert ds.name == "swebench_pro"
  assert ds[0].instance_id == "i0"


def test_load_dataset_unknown_name_raises(tmp_path: Path) -> None:
  with pytest.raises(KeyError, match="Unknown dataset"):
    load_dataset("absent", root=tmp_path / "datasets")


def test_load_dataset_missing_dir_raises(tmp_path: Path) -> None:
  with pytest.raises(FileNotFoundError):
    load_dataset("swebench_pro", root=tmp_path / "datasets")
