"""The production annotation pipeline: sample N times, then aggregate.

Runs N independent annotation agents on one instance in parallel, then an
aggregator that reconciles them into the final annotation. Every artifact is
persisted under ``annotations/<dataset>/<instance_id>/`` (see ``storage``): each
candidate's annotation + final proxy exchange, and the aggregate's. The
aggregate is the deliverable; the candidates make it auditable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..datasets.loader import Dataset, load_dataset
from ..datasets.swebench_pro import SweBenchProInstance
from ..paths import find_repo_root
from .agent_run import DEFAULT_MODEL, RunResult
from .aggregator import aggregate_instance
from .annotator import annotate_instance
from .errors import AnnotationError, UsageLimitError
from .proxy import DEFAULT_BASE_PORT
from .storage import (
    AGGREGATE_LABEL,
    candidate_label,
    DEFAULT_DATASET,
    instance_dir,
    store_run,
)

DEFAULT_SAMPLES = 3


@dataclass
class PipelineResult:
  """The full sample-and-aggregate outcome for one instance."""

  instance_id: str
  candidates: list[RunResult]
  aggregate: RunResult
  directory: Path

  @property
  def is_valid(self) -> bool:
    return self.aggregate.is_valid


def annotate_with_aggregation(
    instance: SweBenchProInstance,
    index: int,
    *,
    dataset: str = DEFAULT_DATASET,
    samples: int = DEFAULT_SAMPLES,
    repo_root: Path | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_BASE_PORT,
) -> PipelineResult:
  """Sample ``instance`` ``samples`` times (in parallel), then aggregate.

  Stores every candidate and the aggregate under
  ``annotations/<dataset>/<instance_id>/``.

  Proxy ports: each instance gets a contiguous block of ``samples + 1`` ports
  keyed by its dataset index — the samples take slots ``0..samples-1`` and the
  aggregate slot ``samples``. Distinct indices give distinct blocks, so the 3
  parallel samples of one instance, and any number of concurrent *different*
  instances, never collide. Ceiling: ``base_port + N*(samples+1) < 65535`` (for
  731 instances × 4 that is ~22.9k, far below the limit).
  """
  if samples < 1:
    raise ValueError("samples must be >= 1")
  root = repo_root or find_repo_root()
  stride = samples + 1
  block = base_port + index * stride

  def _sample(k: int) -> RunResult:
    return annotate_instance(
        instance,
        index,
        repo_root=root,
        model=model,
        port=block + (k - 1),
        variant=f"sample{k}",
    )

  candidates: list[RunResult] = []
  with ThreadPoolExecutor(max_workers=samples) as pool:
    futures = [pool.submit(_sample, k) for k in range(1, samples + 1)]
    for future in futures:
      try:
        candidates.append(future.result())
      except UsageLimitError:
        raise  # quota exhausted — stop, don't quietly drop
      except AnnotationError:
        pass  # a sample failed after its own retries; aggregate the survivors
  if not candidates:
    raise AnnotationError(
        f"all {samples} samples failed for {instance.instance_id}"
    )

  for k, candidate in enumerate(candidates, start=1):
    _ = store_run(
        instance.instance_id,
        candidate_label(k),
        candidate,
        dataset=dataset,
        repo_root=root,
    )

  payload = [
      {"snippets": [s.to_dict() for s in c.annotation.snippets]}
      for c in candidates
  ]
  aggregate = aggregate_instance(
      instance,
      index,
      payload,
      repo_root=root,
      model=model,
      port=block + samples,
  )
  _ = store_run(
      instance.instance_id,
      AGGREGATE_LABEL,
      aggregate,
      dataset=dataset,
      repo_root=root,
  )
  return PipelineResult(
      instance_id=instance.instance_id,
      candidates=candidates,
      aggregate=aggregate,
      directory=instance_dir(
          instance.instance_id, dataset=dataset, repo_root=root
      ),
  )


def annotate_by_id_with_aggregation(
    instance_id: str,
    *,
    dataset_obj: Dataset | None = None,
    dataset: str = DEFAULT_DATASET,
    samples: int = DEFAULT_SAMPLES,
    model: str = DEFAULT_MODEL,
) -> PipelineResult:
  """Look an instance up by id and run the sample-and-aggregate pipeline."""
  dataset_obj = dataset_obj or load_dataset(dataset)
  record = dataset_obj.require(instance_id)
  if not isinstance(record, SweBenchProInstance):
    raise TypeError(f"Unexpected record type: {type(record).__name__}")
  index = dataset_obj.index_of(instance_id)
  return annotate_with_aggregation(
      record, index, dataset=dataset, samples=samples, model=model
  )
