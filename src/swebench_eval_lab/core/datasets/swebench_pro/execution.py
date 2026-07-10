"""SWE-bench Pro execution adapter: how to run and grade its instances.

Everything SWE-bench-Pro-specific about *running* the benchmark lives here (the
data records are in ``record``): the prebuilt Docker Hub images, the
per-instance test harness (``run_script`` + ``parser``) fetched from Scale's
repo, and the mapping onto the general
:class:`~swebench_eval_lab.core.benchmark.EvalSpec`.
Implements ``BenchmarkAdapter[SweBenchProInstance]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import urllib.request

from swebench_eval_lab.core.benchmark import EvalSpec
from swebench_eval_lab.core.paths import cache_root, find_repo_root

from .record import SweBenchProInstance

# Prebuilt per-instance images on Docker Hub (public mirror of Scale's ECR); the
# dataset's ``dockerhub_tag`` is the tag verbatim.
IMAGE_REPO = "jefzda/sweap-images"
# Every image clones the repo to this path, so eval/rollout run against it.
WORKDIR = "/app"

# scaleapi/SWE-bench_Pro-os (MIT) pinned to an exact commit for reproducibility.
# Why this SHA: it was the tip of origin/main when we built this — pinned
# 2026-07-10; the commit itself is dated 2026-05-18 ("Merge PR #98 from
# scaleapi/miguelrc-scale-patch-1"), i.e. the latest harness at the time. We pin
# a SHA instead of tracking main so the fetched run_script.sh / parser.py can't
# drift under us mid-project. Bump deliberately, and only after re-checking that
# the new scripts still match our eval logic.
SCALE_REPO = "scaleapi/SWE-bench_Pro-os"
SCALE_COMMIT = "ca10a60a5fcae51e6948ffe1485d4153d421e6c5"
_RAW_BASE = "https://raw.githubusercontent.com"

RUN_SCRIPT_NAME = "run_script.sh"
PARSER_NAME = "parser.py"
_FETCH_TIMEOUT_S = 30.0


def image_ref(dockerhub_tag: str) -> str:
  """The pullable image reference for an instance's ``dockerhub_tag``."""
  return f"{IMAGE_REPO}:{dockerhub_tag}"


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
      _download(harness_url(instance_id, name), dest)
    fetched.append(dest)
  return fetched[0], fetched[1]


def _download(url: str, dest: Path) -> None:
  with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as response:
    data = response.read()
  _ = dest.write_bytes(data)


@dataclass(frozen=True)
class SweBenchProAdapter:
  """``BenchmarkAdapter`` for SWE-bench Pro."""

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
