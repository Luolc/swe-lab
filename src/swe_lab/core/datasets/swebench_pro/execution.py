"""SWE-Bench Pro execution adapter: build a runnable spec for an instance.

Everything specific to SWE-Bench Pro about *setting up* a run lives here (the
data records are in ``record``; grading the run is in ``grading``): the prebuilt
Docker Hub images, the per-instance test harness (``run_script`` + ``parser``)
fetched from Scale's repo, and the mapping onto the general
:class:`~swe_lab.core.benchmark.EvalSpec`.
Implements ``BenchmarkAdapter[SweBenchProInstance]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import urllib.request

from swe_lab.core.benchmark import EvalSpec
from swe_lab.core.paths import cache_root, find_repo_root

from .constants import (
    GITHUB_RAW_BASE,
    HARNESS_FETCH_TIMEOUT_S,
    HARNESS_SUBDIR,
    IMAGE_REPO,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    SCALE_SWEBENCH_PRO_COMMIT,
    SCALE_SWEBENCH_PRO_REPO,
    WORKDIR,
)
from .record import SweBenchProInstance


def image_ref(dockerhub_tag: str) -> str:
  """The pullable image reference for an instance's ``dockerhub_tag``."""
  return f"{IMAGE_REPO}:{dockerhub_tag}"


def github_raw_url(instance_id: str, filename: str) -> str:
  """Raw GitHub URL for one harness file at the pinned Scale commit."""
  return (
      f"{GITHUB_RAW_BASE}/{SCALE_SWEBENCH_PRO_REPO}/{SCALE_SWEBENCH_PRO_COMMIT}"
      f"/run_scripts/{instance_id}/{filename}"
  )


def harness_dir(instance_id: str, *, repo_root: Path | None = None) -> Path:
  """Gitignored cache directory for one instance's fetched harness files."""
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


@dataclass(frozen=True)
class SweBenchProAdapter:
  """``BenchmarkAdapter`` for SWE-Bench Pro."""

  repo_root: Path | None = None

  def eval_spec(self, instance: SweBenchProInstance) -> EvalSpec:
    run_script, parser = fetch_harness(
        instance.instance_id, repo_root=self.repo_root
    )
    return EvalSpec(
        instance_id=instance.instance_id,
        image_ref=image_ref(instance.dockerhub_tag),
        workdir=WORKDIR,
        base_commit=instance.base_commit,
        before_repo_set_cmd=instance.before_repo_set_cmd,
        run_script=run_script.read_text(),
        parser=parser.read_text(),
        fail_to_pass=instance.fail_to_pass,
        pass_to_pass=instance.pass_to_pass,
        selected_tests=instance.selected_test_files_to_run,
    )
