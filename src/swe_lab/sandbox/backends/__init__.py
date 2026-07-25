"""Concrete sandbox backends + the config seam that selects one.

Each backend realizes the ``SandboxBackend`` ABC one way: ``DockerHostBackend``
drives Docker from the host (A-host); ``GitHubJobBackend`` runs in the job's own
shell (A-ghjob). ``build_backend`` is the single construction seam both CLIs use
so "backend chosen by config" is one option, not per-CLI branching.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from ..backend import SandboxBackend
from .ghjob import GitHubJobBackend
from .host import DockerHostBackend

__all__ = [
    "BackendKind",
    "DockerHostBackend",
    "GitHubJobBackend",
    "build_backend",
]


class BackendKind(StrEnum):
  """Which backend a run uses."""

  HOST = "host"  # A-host: docker create/start/exec/rm from the host
  GHJOB = "ghjob"  # A-ghjob: the GitHub job is the container


def build_backend(
    kind: BackendKind,
    *,
    network: bool = True,
    pull: bool = True,
    env: Mapping[str, str] | None = None,
    pass_env: Sequence[str] = (),
) -> SandboxBackend:
  """Construct the selected backend from a common set of run settings.

  A-ghjob ignores ``network`` / ``pull`` (Docker-only concepts — the job is
  already the live container with its own network), so callers pass one option
  set and this seam maps it onto whichever backend is chosen.

  Args:
    kind: Which backend to build.
    network: Whether the container gets network access (A-host only).
    pull: Whether to pull the image before the run (A-host only).
    env: Variables set on each exec as ``KEY=VALUE``.
    pass_env: Names of variables inherited by reference (value never on argv).

  Returns:
    The constructed backend.
  """
  env = dict(env or {})
  if kind is BackendKind.GHJOB:
    return GitHubJobBackend(env=env, pass_env=pass_env)
  return DockerHostBackend(
      network=network, pull=pull, env=env, pass_env=pass_env
  )
