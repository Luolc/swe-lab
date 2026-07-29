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

from etils import epath

from .errors import SandboxError


class RunStatus(StrEnum):
  """How the run ended, at the engine's level of abstraction."""

  SUCCESS = "success"
  SETUP_ERROR = "setup_error"  # before_create / mounts / up / after_create
  RUN_ERROR = "run_error"  # the body or a post-processing hook failed


@dataclass(frozen=True, slots=True)
class InlineArtifact:
  """An artifact the observer already holds, handed over instead of fetched.

  For output an observer *derived* host-side (a parsed conversation, a cleaned
  patch): it already has the bytes, so writing them back into the sandbox only
  to fetch them out again is two pointless transfers — and on a remote sandbox,
  two network round trips. The manager writes these straight to its output dir.

  Attributes:
    filename: The name to write it under in the output dir (the same name it
      would have had in the workspace).
    content: The bytes to write.
  """

  filename: str
  content: bytes


@dataclass(frozen=True, slots=True)
class Contribution:
  """What one observer hook hands back for the manager to aggregate.

  Only engine-generic shapes live here: artifacts and scalar metrics. There are
  two artifact channels, by **where the content already is**:

  - ``artifacts`` — still *in the sandbox*, referenced by its in-sandbox
    filename (a host path is not meaningful for a remote sandbox); the
    manager's collect step fetches each out while the sandbox is live.
  - ``inline_artifacts`` — already *in hand*, because this observer produced it;
    the manager writes it out directly, with no sandbox round trip.

  Either way ``RunResult.artifacts`` ends up holding host paths, so a consumer
  never has to care which channel an artifact came through.

  Attributes:
    artifacts: Canonical artifact name → its in-sandbox filename.
    inline_artifacts: Canonical artifact name → content the observer holds.
    metrics: Metric name → value.
  """

  artifacts: dict[str, str] = field(default_factory=dict)
  inline_artifacts: dict[str, InlineArtifact] = field(default_factory=dict)
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
  artifacts: dict[str, epath.Path]
  metrics: dict[str, float]
  error: BaseException | None = None


def merge_contributions(contributions: list[Contribution]) -> Contribution:
  """Union observers' contributions, refusing key collisions.

  An artifact name must be unique across *both* channels — which one an observer
  used is an implementation detail, so two observers claiming the same name is a
  collision either way.

  Args:
    contributions: Every non-``None`` contribution, in hook order.

  Returns:
    One merged contribution.

  Raises:
    SandboxError: If two contributions claim the same artifact or metric
      name (no silent last-writer-wins).
  """
  artifacts: dict[str, str] = {}
  inline: dict[str, InlineArtifact] = {}
  metrics: dict[str, float] = {}
  for contribution in contributions:
    for name, filename in contribution.artifacts.items():
      if name in artifacts or name in inline:
        raise SandboxError(f"two observers contributed artifact {name!r}")
      artifacts[name] = filename
    for name, produced in contribution.inline_artifacts.items():
      if name in artifacts or name in inline:
        raise SandboxError(f"two observers contributed artifact {name!r}")
      inline[name] = produced
    for name, value in contribution.metrics.items():
      if name in metrics:
        raise SandboxError(f"two observers contributed metric {name!r}")
      metrics[name] = value
  return Contribution(
      artifacts=artifacts, inline_artifacts=inline, metrics=metrics
  )
