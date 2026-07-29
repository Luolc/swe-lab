"""SWE-Bench Pro auxiliary files: the per-instance run_script + parser.

Everything specific to SWE-Bench Pro about obtaining the auxiliary grading files
lives here (the data records are in ``record``; compiling a runnable eval is in
``unit_test``): the per-instance test harness — a ``run_script`` plus a
``parser`` — is fetched from Scale's repo at a pinned commit and cached, so we
reuse it without vendoring ~1000 files into git or carrying the whole repo as a
submodule.
"""

from __future__ import annotations

import urllib.request

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

from .constants import (
    GITHUB_RAW_BASE,
    HARNESS_FETCH_TIMEOUT_S,
    HARNESS_SUBDIR,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    SCALE_SWEBENCH_PRO_COMMIT,
    SCALE_SWEBENCH_PRO_REPO,
)


def github_raw_url(instance_id: str, filename: str) -> str:
  """Return the raw GitHub URL of one auxiliary file at the pinned commit."""
  return (
      f"{GITHUB_RAW_BASE}/{SCALE_SWEBENCH_PRO_REPO}/{SCALE_SWEBENCH_PRO_COMMIT}"
      f"/run_scripts/{instance_id}/{filename}"
  )


def auxiliary_dir(
    instance_id: str, *, repo_root: epath.PathLike | None = None
) -> epath.Path:
  """Return the gitignored cache dir for one instance's auxiliary files."""
  root = repo_root or find_repo_root()
  return cache_root(root) / HARNESS_SUBDIR / instance_id


def fetch_auxiliary(
    instance_id: str,
    *,
    repo_root: epath.PathLike | None = None,
    refresh: bool = False,
) -> tuple[epath.Path, epath.Path]:
  """Ensure ``run_script.sh`` + ``parser.py`` are cached; return their paths.

  Idempotent: already-cached files are reused unless ``refresh`` is set. This is
  how we reuse Scale's per-instance run_script + parser without vendoring ~1000
  files into git or carrying the whole repo as a submodule.
  """
  directory = auxiliary_dir(instance_id, repo_root=repo_root)
  directory.mkdir(parents=True, exist_ok=True)
  fetched: list[epath.Path] = []
  for name in (RUN_SCRIPT_NAME, PARSER_NAME):
    dest = directory / name
    if refresh or not dest.is_file():
      _download(github_raw_url(instance_id, name), dest)
    fetched.append(dest)
  return fetched[0], fetched[1]


def _download(url: str, dest: epath.PathLike) -> None:
  with urllib.request.urlopen(url, timeout=HARNESS_FETCH_TIMEOUT_S) as response:
    data = response.read()
  _ = epath.Path(dest).write_bytes(data)
