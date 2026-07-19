"""Declarative mounts: files materialized into the workspace before a run.

Every axis (dataset, harness, eval-method) declares what it needs staged as a
``Mounts`` value; the manager merges the contributions and materializes the
union once, replacing the imperative per-flow staging code the engine
supersedes. A duplicate target path is an error, never a silent overwrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .errors import SandboxError


@dataclass(frozen=True, slots=True)
class Mount:
  """One file to materialize into the workspace before the sandbox comes up.

  Exactly one of ``content``/``source`` is set: ``content`` for small,
  runtime-generated files (scripts), ``source`` for large host-cached files
  (e.g. the pinned agent binary), copied rather than held in memory.

  Attributes:
    content: The file's bytes, for runtime-generated content.
    source: A host path to copy the file from.
    executable: Whether to ``chmod +x`` the materialized file.
  """

  content: bytes | None = None
  source: Path | None = None
  executable: bool = False

  def __post_init__(self) -> None:
    """Validate that exactly one of ``content``/``source`` is set.

    Raises:
      SandboxError: If both or neither are set.
    """
    if (self.content is None) == (self.source is None):
      raise SandboxError(
          "a Mount needs exactly one of content/source, got "
          f"content={'set' if self.content is not None else 'None'}, "
          f"source={self.source!r}"
      )


type Mounts = dict[str, Mount]
"""Workspace-relative target path → the mount to place there."""


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


def materialize(mounts: Mounts, workspace: Path) -> None:
  """Write every mount into the workspace, creating parent directories.

  Args:
    mounts: The merged staging set.
    workspace: The host-side workspace directory to write into.

  Raises:
    SandboxError: If a ``source`` path does not exist.
  """
  for target, mount in mounts.items():
    dest = workspace / target
    dest.parent.mkdir(parents=True, exist_ok=True)
    if mount.content is not None:
      _ = dest.write_bytes(mount.content)
    else:
      assert mount.source is not None  # __post_init__ guarantees it
      if not mount.source.is_file():
        raise SandboxError(
            f"mount source for {target!r} does not exist: {mount.source}"
        )
      _ = shutil.copyfile(mount.source, dest)
    if mount.executable:
      dest.chmod(dest.stat().st_mode | 0o755)
