"""The engine-level outcome of one sandbox run.

The manager assembles a single ``RunResult`` from observer *return values*
(``Contribution``) plus what it catches — exactly once, at teardown. Typed
per-axis results (e.g. an eval verdict) do not travel through here: they live
on the stateful observer that produced them — the caller constructed that
observer, holds the reference, and reads the typed result straight back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from etils import epath

from .errors import SandboxError


class RunStatus(StrEnum):
  """How the run ended, at the engine's level of abstraction."""

  SUCCESS = "success"
  SETUP_ERROR = "setup_error"  # before_create / mounts / up / after_create
  RUN_ERROR = "run_error"  # the body or a post-processing hook failed
  # The body's own watchdog killed it. Distinct from RUN_ERROR because nothing
  # raised: the composition read a timed-out ExecResult and said so. Without it
  # a killed run is indistinguishable from one that simply produced nothing.
  TIMEOUT = "timeout"


# Separator between a namespace (the harness or method that owns an artifact)
# and the artifact's own name. A dot, not a slash: these are logical names in a
# manifest, not key paths.
NAME_SEPARATOR = "."


def qualified_name(namespace: str, name: str) -> str:
  """Return an artifact name namespaced by whatever produced it.

  Args:
    namespace: The owner — a harness's ``name``, or an eval method's.
    name: The artifact's own name, format suffix included.

  Returns:
    The namespaced name, e.g. ``claude_code.stderr.log``.
  """
  return f"{namespace}{NAME_SEPARATOR}{name}"


@dataclass(frozen=True, slots=True)
class Contribution:
  """What one observer hook hands back for the manager to aggregate.

  Only engine-generic shapes live here: artifacts and scalar metrics. There are
  two artifact channels, by **where the content already is**:

  - ``artifacts`` — still *in the sandbox*, referenced by its in-sandbox
    filename (a host path is not meaningful for a remote sandbox); the
    manager's collect step fetches each out while the sandbox is live.
  - ``inline_artifacts`` — already *in hand*, because this observer produced it
    (a parsed conversation, a cleaned patch); the manager writes the bytes out
    directly, with no sandbox round trip — writing them back into the sandbox
    only to fetch them out again is two pointless transfers, and two network
    round trips against a remote sandbox.

  Either way ``RunResult.artifacts`` ends up holding host paths, so a consumer
  never has to care which channel an artifact came through.

  **Name an artifact for what it is, suffixed with its format** —
  ``conversation.json``, ``patch.diff``, ``event_stream.jsonl``, ``stderr.log``.
  The name travels into a persisted manifest, where a reader has only the name
  to go on: the suffix tells them how to parse the payload without opening it,
  and distinguishes one object (``.json``) from newline-delimited records
  (``.jsonl``). A shared, cross-harness artifact is simply named for its file
  (``patch.raw.diff``); one a harness owns is namespaced, so its name and its
  filename differ (``claude_code.stderr.log`` ← ``claude.stderr.log``).

  The name is also what the artifact lands under on the host, so it must be a
  plain filename — enforced by :func:`merge_contributions`.

  Attributes:
    artifacts: Canonical artifact name → its in-sandbox filename.
    inline_artifacts: Canonical artifact name → content the observer holds.
    metrics: Metric name → value.
  """

  artifacts: dict[str, str] = field(default_factory=dict)
  inline_artifacts: dict[str, bytes] = field(default_factory=dict)
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


def _check_claim(name: str, *taken: Mapping[str, object]) -> None:
  """Vet one artifact name: a plain filename, not already claimed.

  Args:
    name: The canonical artifact name being contributed.
    *taken: The name → value maps already filled, one per channel.

  Raises:
    SandboxError: If the name is not usable as a filename, or is a duplicate.
  """
  # The manager lands each artifact under its *name* (see ``_collect``), so a
  # name that is a path — or worse, an absolute one — would write outside the
  # output dir. Names are dot-separated logical names by construction
  # (``qualified_name``); this makes that an enforced property, not a habit.
  if not name or name in {".", ".."} or "/" in name or "\\" in name:
    raise SandboxError(f"artifact name {name!r} is not a plain filename")
  if any(name in claimed for claimed in taken):
    raise SandboxError(f"two observers contributed artifact {name!r}")


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
    SandboxError: If an artifact name is not a plain filename, or if two
      contributions claim the same artifact or metric name (no silent
      last-writer-wins).
  """
  artifacts: dict[str, str] = {}
  inline: dict[str, bytes] = {}
  metrics: dict[str, float] = {}
  for contribution in contributions:
    for name, filename in contribution.artifacts.items():
      _check_claim(name, artifacts, inline)
      artifacts[name] = filename
    for name, content in contribution.inline_artifacts.items():
      _check_claim(name, artifacts, inline)
      inline[name] = content
    for name, value in contribution.metrics.items():
      if name in metrics:
        raise SandboxError(f"two observers contributed metric {name!r}")
      metrics[name] = value
  return Contribution(
      artifacts=artifacts, inline_artifacts=inline, metrics=metrics
  )
