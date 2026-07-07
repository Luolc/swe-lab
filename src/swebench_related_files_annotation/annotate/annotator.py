"""Annotate one instance: produce its list of relevant code snippets.

A thin wrapper over :func:`agent_run.run_agent` with the annotation prompt (the
counterpart of ``aggregator``, which reconciles several annotations into one).
The shared workspace / proxy / validate / store machinery lives in
``agent_run``.
"""

from __future__ import annotations

from pathlib import Path

from ..datasets.loader import Dataset, load_dataset
from ..datasets.swebench_pro import SweBenchProInstance
from ..repo.provider import GitCheckoutProvider
from .agent_run import (
    DEFAULT_CLAUDE_TIMEOUT_S,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MODEL,
    run_agent,
    RunResult,
)
from .annotation_prompt import build_annotation_prompt
from .proxy import DEFAULT_BASE_PORT

__all__ = [
    "DEFAULT_MODEL",
    "RunResult",
    "annotate_by_id",
    "annotate_instance",
]


def annotate_instance(
    instance: SweBenchProInstance,
    index: int,
    *,
    repo_root: Path | None = None,
    provider: GitCheckoutProvider | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_BASE_PORT,
    port: int | None = None,
    variant: str = "",
    store: bool = True,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claude_timeout: float = DEFAULT_CLAUDE_TIMEOUT_S,
) -> RunResult:
  """Annotate one instance and (by default) persist its artifacts."""
  return run_agent(
      instance,
      index,
      prompt=build_annotation_prompt(instance),
      kind="annotation",
      repo_root=repo_root,
      provider=provider,
      model=model,
      base_port=base_port,
      port=port,
      variant=variant,
      store=store,
      max_attempts=max_attempts,
      claude_timeout=claude_timeout,
  )


def annotate_by_id(
    instance_id: str,
    *,
    dataset: Dataset | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_BASE_PORT,
) -> RunResult:
  """Look an instance up by id and annotate it (using its dataset index)."""
  dataset = dataset or load_dataset()
  record = dataset.require(instance_id)
  if not isinstance(record, SweBenchProInstance):
    raise TypeError(f"Unexpected record type: {type(record).__name__}")
  index = dataset.index_of(instance_id)
  return annotate_instance(record, index, model=model, base_port=base_port)
