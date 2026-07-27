"""Declarative staging: a resource mounted into the sandbox at a target path.

Every axis (dataset, harness, eval-method) declares what it needs staged as a
``Mounts`` value; the manager merges the contributions and the sandbox mounts
the union after ``up`` (ADR-0003). A duplicate target path is an error, never a
silent overwrite.

A ``Mount`` is a :class:`~swe_lab.sandbox.resources.Resource` (where the bytes
come from) plus a target path and ``executable`` / ``read_only`` flags. There is
**one** placement type: an "asset" is just a **read-only** mount (typically at a
fixed absolute path, e.g. the pinned binary) — not a separate interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import SandboxError
from .resources import Resource


@dataclass(frozen=True, slots=True)
class Mount:
  """A resource mounted into the sandbox at its target path.

  Attributes:
    resource: Where the file's content comes from.
    executable: Whether the mounted file is executable.
    read_only: Whether the run must not modify it (an "asset"). A host sandbox
      may realize a read-only mount by a bind-mount ``:ro``; others copy it and
      revoke write.
  """

  resource: Resource
  executable: bool = False
  read_only: bool = False

  @property
  def mode(self) -> int:
    """The POSIX mode a sandbox gives this mount: executable / read-only."""
    mode = 0o755 if self.executable else 0o644
    if self.read_only:
      mode &= ~0o222  # strip write bits → 0o555 / 0o444
    return mode


type Mounts = dict[str, Mount]
"""Target path (workspace-relative, or absolute for an asset) → Mount."""


def merge_mounts(*contributions: Mounts) -> Mounts:
  """Merge per-axis mount contributions into one staging set.

  Args:
    *contributions: Mount sets, one per contributor (composition extras,
      then each observer), in registration order.

  Returns:
    The union of all contributions.

  Raises:
    SandboxError: If two contributions claim the same target path.
  """
  merged: Mounts = {}
  for contribution in contributions:
    for target, mount in contribution.items():
      if target in merged:
        raise SandboxError(
            f"duplicate mount target {target!r}: two contributors claim it"
        )
      merged[target] = mount
  return merged
