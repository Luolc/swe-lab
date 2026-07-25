"""SWE-Bench Pro execution setup: image ref + per-instance test harness.

Everything specific to SWE-Bench Pro about *setting up* a run lives here (the
data records are in ``record``; compiling a runnable eval is in ``unit_test``):
the prebuilt Docker Hub images and the per-instance test harness
(``run_script`` + ``parser``) fetched from Scale's repo and cached.
"""

from __future__ import annotations

from pathlib import Path
import urllib.request

from swe_lab.paths import cache_root, find_repo_root

from .constants import (
    GITHUB_RAW_BASE,
    HARNESS_FETCH_TIMEOUT_S,
    HARNESS_SUBDIR,
    IMAGE_REPO,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    SCALE_SWEBENCH_PRO_COMMIT,
    SCALE_SWEBENCH_PRO_REPO,
)


def image_ref(dockerhub_tag: str) -> str:
  """Return the pullable image reference for ``dockerhub_tag``."""
  return f"{IMAGE_REPO}:{dockerhub_tag}"


def github_raw_url(instance_id: str, filename: str) -> str:
  """Return the raw GitHub URL of one harness file at the pinned commit."""
  return (
      f"{GITHUB_RAW_BASE}/{SCALE_SWEBENCH_PRO_REPO}/{SCALE_SWEBENCH_PRO_COMMIT}"
      f"/run_scripts/{instance_id}/{filename}"
  )


def harness_dir(instance_id: str, *, repo_root: Path | None = None) -> Path:
  """Return the gitignored cache dir for one instance's harness files."""
  root = repo_root or find_repo_root()
  return cache_root(root) / HARNESS_SUBDIR / instance_id


def fetch_harness(
    instance_id: str,
    *,
    repo_root: Path | None = None,
    refresh: bool = False,
) -> tuple[Path, Path]:
  """Ensure ``run_script.sh`` + ``parser.py`` are cached; return their paths.

  Idempotent: already-cached files are reused unless ``refresh`` is set. This is
  how we reuse Scale's per-instance harness without vendoring ~1000 files into
  git or carrying the whole repo as a submodule.
  """
  directory = harness_dir(instance_id, repo_root=repo_root)
  directory.mkdir(parents=True, exist_ok=True)
  fetched: list[Path] = []
  for name in (RUN_SCRIPT_NAME, PARSER_NAME):
    dest = directory / name
    if refresh or not dest.is_file():
      _download(github_raw_url(instance_id, name), dest)
    fetched.append(dest)
  return fetched[0], fetched[1]


def _download(url: str, dest: Path) -> None:
  with urllib.request.urlopen(url, timeout=HARNESS_FETCH_TIMEOUT_S) as response:
    data = response.read()
  _ = dest.write_bytes(data)
