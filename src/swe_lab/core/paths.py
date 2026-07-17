"""Filesystem locations used across the project.

Everything is resolved relative to the repository root so the tooling works the
same whether it is invoked from the repo root, a subdirectory, or a scheduled
job. The root is discovered by walking up to the nearest ``pyproject.toml`` (or
taken from ``PROJECT_ROOT`` when set by ``.envrc``).
"""

from __future__ import annotations

import os
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
  """Return the repository root directory.

  Prefers the ``PROJECT_ROOT`` environment variable (exported by ``.envrc``);
  otherwise walks upward from ``start`` (defaulting to this file) until a
  directory containing ``pyproject.toml`` is found.
  """
  env_root = os.environ.get("PROJECT_ROOT")
  if env_root:
    return Path(env_root).resolve()

  origin = (start or Path(__file__)).resolve()
  for candidate in (origin, *origin.parents):
    if (candidate / "pyproject.toml").is_file():
      return candidate
  raise RuntimeError(
      "Could not locate the repository root (no pyproject.toml found above"
      f" {origin})."
  )


def datasets_root(repo_root: Path | None = None) -> Path:
  """Directory holding the per-dataset folders (``datasets/``)."""
  return (repo_root or find_repo_root()) / "datasets"


def cache_root(repo_root: Path | None = None) -> Path:
  """Root of the gitignored local cache (``.cache/``)."""
  return (repo_root or find_repo_root()) / ".cache"


def repo_cache_dir(repo_root: Path | None = None) -> Path:
  """Gitignored cache for provisioned repository checkouts."""
  return cache_root(repo_root) / "repos"


def outputs_root(repo_root: Path | None = None) -> Path:
  """Version-controlled root for per-task deliverables (``outputs/``).

  Each task keeps its results under ``outputs/<task>/`` (e.g. the related-files
  annotations live in ``outputs/related_files/``), so a new task adds a sibling
  folder rather than colliding with the existing output.
  """
  return (repo_root or find_repo_root()) / "outputs"
