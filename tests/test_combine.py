"""Tests for combining per-instance aggregates into one parquet."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

import polars as pl

from swe_lab.pipelines.related_files.combine import (
    build_dataframe,
    COLUMNS,
    combine,
    METADATA_NAME,
)
from swe_lab.pipelines.related_files.schema import (
    Annotation,
    Snippet,
    SnippetCategory,
)
from swe_lab.pipelines.related_files.storage import (
    instance_dir,
)


def _write_aggregate(
    instance_id: str,
    snippets: tuple[Snippet, ...],
    *,
    repo_root: Path,
) -> None:
  directory = instance_dir(instance_id, repo_root=repo_root)
  directory.mkdir(parents=True, exist_ok=True)
  annotation = Annotation(instance_id, snippets, {"kind": "aggregate"})
  _ = (directory / "aggregate.json").write_text(annotation.to_json())


def _snippet(path: str, start: int, end: int) -> Snippet:
  return Snippet(
      file_path=path,
      start_line=start,
      end_line=end,
      category=SnippetCategory.CONTEXT_FILE,
      description=f"{path}:{start}-{end}",
  )


def test_build_dataframe_one_row_per_instance(tmp_path: Path) -> None:
  _write_aggregate(
      "inst-b",
      (_snippet("b.py", 1, 5), _snippet("b.py", 20, 30)),
      repo_root=tmp_path,
  )
  _write_aggregate("inst-a", (_snippet("a.py", 1, 2),), repo_root=tmp_path)

  frame = build_dataframe(repo_root=tmp_path)

  assert frame.columns == list(COLUMNS)
  assert frame.schema["relevant_snippets"] == pl.String
  # One row per instance, sorted by instance id.
  assert frame.height == 2
  assert frame["instance_id"].to_list() == ["inst-a", "inst-b"]
  # relevant_snippets is a JSON string of the ordered snippet dicts.
  snippets_a = json.loads(frame["relevant_snippets"][0])
  snippets_b = json.loads(frame["relevant_snippets"][1])
  assert [s["file_path"] for s in snippets_a] == ["a.py"]
  assert [
      (s["file_path"], s["start_line"], s["end_line"]) for s in snippets_b
  ] == [("b.py", 1, 5), ("b.py", 20, 30)]
  assert snippets_a[0]["category"] == "context-file"


def test_build_dataframe_empty(tmp_path: Path) -> None:
  frame = build_dataframe(repo_root=tmp_path)
  assert frame.columns == list(COLUMNS)
  assert frame.height == 0


def test_combine_writes_parquet(tmp_path: Path) -> None:
  _write_aggregate("inst-a", (_snippet("a.py", 1, 2),), repo_root=tmp_path)

  out, _, frame = combine(repo_root=tmp_path)

  assert out == (
      tmp_path
      / "outputs"
      / "related_files"
      / "swebench_pro"
      / "annotations.parquet"
  )
  assert out.is_file()
  reloaded = pl.read_parquet(out)
  assert reloaded.equals(frame)
  snippets = json.loads(reloaded["relevant_snippets"][0])
  assert snippets[0]["category"] == "context-file"


def test_combine_writes_metadata_sidecar(tmp_path: Path) -> None:
  _write_aggregate(
      "inst-a",
      (_snippet("a.py", 1, 2), _snippet("a.py", 5, 9)),
      repo_root=tmp_path,
  )

  out, metadata_path, _ = combine(repo_root=tmp_path)

  assert metadata_path == out.with_name(METADATA_NAME)
  metadata = json.loads(metadata_path.read_text())
  assert metadata["parquet"] == "annotations.parquet"
  assert metadata["num_rows"] == 1  # one instance
  assert metadata["num_snippets"] == 2
  # Timestamp is present and ISO-8601 parseable.
  assert datetime.fromisoformat(metadata["generated_at"])
  # Checksum matches the bytes actually written.
  assert metadata["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
