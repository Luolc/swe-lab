"""SWE-Bench Pro execution adapter: image ref, pinned harness URL, grading."""

from __future__ import annotations

from swe_lab.core.benchmark import EvalSpec
from swe_lab.core.datasets.swebench_pro.constants import (
    IMAGE_REPO,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    SCALE_SWEBENCH_PRO_COMMIT,
)
from swe_lab.core.datasets.swebench_pro.execution import (
    github_raw_url,
    image_ref,
)


def test_image_ref() -> None:
  tag = "flipt-io.flipt-flipt-io__flipt-6fe76d0"
  assert image_ref(tag) == f"{IMAGE_REPO}:{tag}"


def test_github_raw_url_is_pinned() -> None:
  url = github_raw_url("instance_foo__bar-abc", RUN_SCRIPT_NAME)
  assert url == (
      "https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/"
      f"{SCALE_SWEBENCH_PRO_COMMIT}/run_scripts/instance_foo__bar-abc"
      "/run_script.sh"
  )
  assert PARSER_NAME == "parser.py"


def test_eval_spec_grading() -> None:
  spec = EvalSpec(
      instance_id="i",
      image_ref="r",
      workdir="/app",
      base_commit="c",
      before_repo_set_cmd="",
      run_script="",
      parser="",
      fail_to_pass=("t1", "t2"),
      pass_to_pass=("t3",),
      selected_tests=("t1",),
  )
  assert spec.required_tests == frozenset({"t1", "t2", "t3"})
  assert spec.is_resolved({"t1", "t2", "t3", "extra"})
  assert not spec.is_resolved({"t1", "t2"})
