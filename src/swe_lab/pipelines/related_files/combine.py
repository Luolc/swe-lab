"""CLI: combine per-instance aggregates into one deliverable parquet.

    python -m swe_lab.pipelines.related_files.combine [--dataset ...]

Reads every
``outputs/related_files/<dataset>/intermediate/<instance_id>/aggregate.json``
and writes a one-row-per-instance table to
``outputs/related_files/<dataset>/annotations.parquet`` (see ``storage`` for the
layout). Each row carries the instance's snippets in a ``relevant_snippets``
column holding a JSON string of the ordered snippet dicts (``SCHEMA`` below).
The candidates and ``.last_exchange.json`` files are intermediate audit data and
are not included — only the aggregate, the deliverable, is combined.

Alongside the parquet a ``metadata.json`` sidecar records the row (instance)
count, the total snippet count, the generation timestamp, and a SHA-256 checksum
of the parquet, so consumers can tell which build they have and verify it is
intact.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path

import polars as pl

from .schema import Annotation
from .storage import (
    combined_parquet_path,
    DEFAULT_DATASET,
    iter_aggregate_paths,
    load_aggregate,
)

# Combined-table schema — one row per instance. ``relevant_snippets`` is a JSON
# string encoding the ordered list of snippet dicts (``file_path``,
# ``start_line``, ``end_line``, ``category``, ``description``).
SCHEMA: dict[str, pl.DataType] = {
    "instance_id": pl.String(),
    "relevant_snippets": pl.String(),
}
COLUMNS = tuple(SCHEMA)

# Sidecar written next to the parquet, describing that build.
METADATA_NAME = "metadata.json"


def _snippets_json(annotation: Annotation) -> str:
  """Encode an instance's ordered snippets as a JSON string of dicts."""
  snippets = [
      {
          "file_path": snippet.file_path,
          "start_line": snippet.start_line,
          "end_line": snippet.end_line,
          "category": snippet.category.value,
          "description": snippet.description,
      }
      for snippet in annotation.snippets
  ]
  return json.dumps(snippets, ensure_ascii=False)


def _rows(annotations: Iterable[Annotation]) -> list[dict[str, object]]:
  """Turn annotations into one row per instance, ready for a DataFrame."""
  return [
      {
          "instance_id": annotation.instance_id,
          "relevant_snippets": _snippets_json(annotation),
      }
      for annotation in annotations
  ]


def build_dataframe(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_root: Path | None = None,
) -> pl.DataFrame:
  """Build the combined per-instance table for a dataset from its aggregates."""
  annotations = [
      load_aggregate(path)
      for path in iter_aggregate_paths(dataset, repo_root=repo_root)
  ]
  return pl.DataFrame(_rows(annotations), schema=SCHEMA)


def _total_snippets(frame: pl.DataFrame) -> int:
  """Return the total snippet count across all rows (parses the JSON)."""
  return sum(len(json.loads(value)) for value in frame["relevant_snippets"])


def _sha256(path: Path) -> str:
  """Return the SHA-256 hex digest of a file's bytes."""
  return hashlib.sha256(path.read_bytes()).hexdigest()


def build_metadata(
    parquet_path: Path, frame: pl.DataFrame
) -> dict[str, object]:
  """Describe one parquet build: counts, timestamp, and checksum."""
  return {
      "parquet": parquet_path.name,
      "num_rows": frame.height,
      "num_snippets": _total_snippets(frame),
      "generated_at": datetime.now(UTC).isoformat(),
      "sha256": _sha256(parquet_path),
  }


def combine(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_root: Path | None = None,
    output: Path | None = None,
) -> tuple[Path, Path, pl.DataFrame]:
  """Write the combined parquet and its ``metadata.json`` sidecar.

  Returns the parquet path, the metadata path, and the frame.
  """
  frame = build_dataframe(dataset, repo_root=repo_root)
  out = output or combined_parquet_path(dataset, repo_root=repo_root)
  out.parent.mkdir(parents=True, exist_ok=True)
  frame.write_parquet(out)

  metadata_path = out.with_name(METADATA_NAME)
  metadata = build_metadata(out, frame)
  _ = metadata_path.write_text(
      json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"
  )
  return out, metadata_path, frame


def main() -> int:
  """Run the combine CLI and print a summary of the build."""
  parser = argparse.ArgumentParser(
      prog="python -m swe_lab.pipelines.related_files.combine",
      description=(
          "Combine per-instance aggregate annotations into one parquet."
      ),
  )
  _ = parser.add_argument(
      "--dataset", default=DEFAULT_DATASET, help="Dataset name."
  )
  _ = parser.add_argument(
      "--output",
      type=Path,
      default=None,
      help="Parquet output path (default: outputs/related_files/<dataset>/"
      "annotations.parquet).",
  )
  args = parser.parse_args()

  out, metadata_path, frame = combine(args.dataset, output=args.output)
  print(f"[OK] {args.dataset}")
  print(f"  instances: {frame.height}")
  print(f"  snippets:  {_total_snippets(frame)}")
  print(f"  written:   {out}")
  print(f"  metadata:  {metadata_path}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
