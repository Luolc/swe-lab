"""On-disk layout of the annotation deliverable.

Per-instance runs are *intermediate* data: the whole 3-sample-then-aggregate
run is preserved under ``intermediate/`` — every candidate's annotation and
final proxy exchange, plus the aggregate's — so the result is auditable. The
combined deliverable is a single ``annotations.parquet`` beside it, built from
every instance's aggregate by the ``combine`` binary:

    outputs/related_files/<dataset>/
        intermediate/<instance_id>/
            candidate_1.json / candidate_1.last_exchange.json
            candidate_2.json / candidate_2.last_exchange.json
            candidate_3.json / candidate_3.last_exchange.json
            aggregate.json   / aggregate.last_exchange.json
        annotations.parquet   <- the combined deliverable

``<label>.json`` is the annotation record; ``<label>.last_exchange.json`` is the
extracted final ``cc-reverse-proxy`` record for that run.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

from swe_lab.core.paths import find_repo_root, outputs_root

from .agent_run import RunResult
from .schema import Annotation

DEFAULT_DATASET = "swebench_pro"
TASK_DIRNAME = "related_files"
AGGREGATE_LABEL = "aggregate"
INTERMEDIATE_DIRNAME = "intermediate"
COMBINED_PARQUET_NAME = "annotations.parquet"


def dataset_dir(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_root: Path | None = None,
) -> Path:
  """Return the per-dataset folder of ``intermediate/`` and the parquet."""
  root = outputs_root(repo_root or find_repo_root())
  return root / TASK_DIRNAME / dataset


def intermediate_dir(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_root: Path | None = None,
) -> Path:
  """Return the folder of every instance's per-run intermediates."""
  return dataset_dir(dataset, repo_root=repo_root) / INTERMEDIATE_DIRNAME


def combined_parquet_path(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_root: Path | None = None,
) -> Path:
  """Return the path of the combined deliverable parquet for a dataset."""
  return dataset_dir(dataset, repo_root=repo_root) / COMBINED_PARQUET_NAME


def instance_dir(
    instance_id: str,
    *,
    dataset: str = DEFAULT_DATASET,
    repo_root: Path | None = None,
) -> Path:
  """Return the directory of one instance's intermediate artifacts."""
  return intermediate_dir(dataset, repo_root=repo_root) / instance_id


def iter_aggregate_paths(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_root: Path | None = None,
) -> Iterator[Path]:
  """Yield every instance's ``aggregate.json`` under ``intermediate/``.

  Sorted by instance id (the parent directory name) for deterministic output.
  """
  base = intermediate_dir(dataset, repo_root=repo_root)
  if not base.is_dir():
    return
  yield from sorted(base.glob(f"*/{AGGREGATE_LABEL}.json"))


def load_aggregate(path: Path) -> Annotation:
  """Load one ``aggregate.json`` into an :class:`Annotation`."""
  return Annotation.from_dict(json.loads(path.read_text()))


def store_run(
    instance_id: str,
    label: str,
    result: RunResult,
    *,
    dataset: str = DEFAULT_DATASET,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
  """Write one run's annotation + last exchange under the instance dir.

  ``label`` is e.g. ``candidate_1`` or ``aggregate``. Returns the two paths.
  """
  return _write(
      instance_dir(instance_id, dataset=dataset, repo_root=repo_root),
      label,
      result.annotation,
      result.last_record,
  )


def candidate_label(index: int) -> str:
  """Return the storage label of one candidate (e.g. ``candidate_1``)."""
  return f"candidate_{index}"


def _write(
    directory: Path,
    label: str,
    annotation: Annotation,
    last_record: dict[str, object],
) -> tuple[Path, Path]:
  directory.mkdir(parents=True, exist_ok=True)
  annotation_path = directory / f"{label}.json"
  _ = annotation_path.write_text(annotation.to_json())

  last_exchange_path = directory / f"{label}.last_exchange.json"
  _ = last_exchange_path.write_text(
      json.dumps(last_record, indent=2, ensure_ascii=False) + "\n"
  )
  return annotation_path, last_exchange_path
