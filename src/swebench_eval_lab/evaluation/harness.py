"""Fetch the per-instance test harness (run_script + parser) from Scale's repo.

The authoritative ``run_script.sh`` (native test invocation) and ``parser.py``
(test output -> structured results) for each instance live in the
MIT-licensed ``scaleapi/SWE-bench_Pro-os`` repo under
``run_scripts/<instance_id>/``. Rather than vendoring ~1000 files into git or
carrying the whole harness as a submodule, we fetch just the two files we need
per instance, from a **pinned commit**, into a gitignored cache. Reproducible
(pinned), keeps the repo small, and clearly
attributes the source. Everything else eval needs (``fail_to_pass`` /
``pass_to_pass`` / ``before_repo_set_cmd`` / ``selected_test_files_to_run`` /
``dockerhub_tag``) is already a real column in our dataset.
"""

from __future__ import annotations

from pathlib import Path
import urllib.request

from swebench_eval_lab.core.paths import cache_root, find_repo_root

# scaleapi/SWE-bench_Pro-os @ this commit (MIT-licensed). Bump deliberately so
# the fetched scripts stay reproducible and attributable.
SCALE_REPO = "scaleapi/SWE-bench_Pro-os"
SCALE_COMMIT = "ca10a60a5fcae51e6948ffe1485d4153d421e6c5"
_RAW_BASE = "https://raw.githubusercontent.com"

RUN_SCRIPT_NAME = "run_script.sh"
PARSER_NAME = "parser.py"
_FETCH_TIMEOUT_S = 30.0


def harness_url(instance_id: str, filename: str) -> str:
  """Raw-content URL for one harness file at the pinned Scale commit."""
  return (
      f"{_RAW_BASE}/{SCALE_REPO}/{SCALE_COMMIT}"
      f"/run_scripts/{instance_id}/{filename}"
  )


def harness_dir(instance_id: str, *, repo_root: Path | None = None) -> Path:
  """Gitignored cache directory for one instance's fetched harness files."""
  root = repo_root or find_repo_root()
  return cache_root(root) / "eval_harness" / instance_id


def fetch_harness(
    instance_id: str,
    *,
    repo_root: Path | None = None,
    refresh: bool = False,
) -> tuple[Path, Path]:
  """Ensure ``run_script.sh`` + ``parser.py`` are cached; return their paths.

  Idempotent: already-cached files are reused unless ``refresh`` is set.
  """
  directory = harness_dir(instance_id, repo_root=repo_root)
  directory.mkdir(parents=True, exist_ok=True)
  fetched: list[Path] = []
  for name in (RUN_SCRIPT_NAME, PARSER_NAME):
    dest = directory / name
    if refresh or not dest.is_file():
      _download(harness_url(instance_id, name), dest)
    fetched.append(dest)
  return fetched[0], fetched[1]


def _download(url: str, dest: Path) -> None:
  with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as response:
    data = response.read()
  _ = dest.write_bytes(data)
