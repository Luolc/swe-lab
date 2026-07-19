"""The engine-level outcome of one sandbox run.

The manager assembles a single ``RunResult`` from observer *return values*
(``Contribution``) plus what it catches — exactly once, at teardown. Typed
per-axis results (e.g. an eval verdict) do not travel through here: they live
on the stateful observer that produced them — the caller constructed that
observer, holds the reference, and reads the typed result straight back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .errors import SandboxError


class RunStatus(StrEnum):
  """How the run ended, at the engine's level of abstraction."""

  SUCCESS = "success"
  SETUP_ERROR = "setup_error"  # before_create / mounts / up / after_create
  RUN_ERROR = "run_error"  # the body or a post-processing hook failed


@dataclass(frozen=True, slots=True)
class Contribution:
  """What one observer hook hands back for the manager to aggregate.

  Only engine-generic shapes live here: artifact *references* into the
  workspace (the persist index — never file content) and scalar metrics.

  Attributes:
    artifacts: Canonical artifact name → its path inside the workspace.
    metrics: Metric name → value.
  """

  artifacts: dict[str, Path] = field(default_factory=dict)
  metrics: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunResult:
  """The engine-level outcome, assembled once at teardown.

  Attributes:
    label: The run's label (defaults to the instance id).
    status: How the run ended.
    artifacts: Union of all observers' artifact references.
    metrics: Union of all observers' metrics.
    error: The run's primary error, when there was one.
  """

  label: str
  status: RunStatus
  artifacts: dict[str, Path]
  metrics: dict[str, float]
  error: BaseException | None = None


def merge_contributions(
    contributions: list[Contribution],
) -> tuple[dict[str, Path], dict[str, float]]:
  """Union observers' contributions, refusing key collisions.

  Args:
    contributions: Every non-``None`` contribution, in hook order.

  Returns:
    The merged ``(artifacts, metrics)`` pair.

  Raises:
    SandboxError: If two contributions claim the same artifact or metric
      name (no silent last-writer-wins).
  """
  artifacts: dict[str, Path] = {}
  metrics: dict[str, float] = {}
  for contribution in contributions:
    for name, path in contribution.artifacts.items():
      if name in artifacts:
        raise SandboxError(f"two observers contributed artifact {name!r}")
      artifacts[name] = path
    for name, value in contribution.metrics.items():
      if name in metrics:
        raise SandboxError(f"two observers contributed metric {name!r}")
      metrics[name] = value
  return artifacts, metrics
