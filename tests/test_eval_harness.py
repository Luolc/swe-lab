"""Eval image ref + harness fetch URL (pure logic; no network)."""

from __future__ import annotations

from swebench_eval_lab.core.docker.images import image_ref, SWEAP_IMAGE_REPO
from swebench_eval_lab.evaluation.harness import (
    harness_url,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    SCALE_COMMIT,
)


def test_image_ref() -> None:
  tag = "flipt-io.flipt-flipt-io__flipt-6fe76d0"
  assert image_ref(tag) == f"{SWEAP_IMAGE_REPO}:{tag}"


def test_harness_url_is_pinned() -> None:
  url = harness_url("instance_foo__bar-abc", RUN_SCRIPT_NAME)
  assert url == (
      "https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/"
      f"{SCALE_COMMIT}/run_scripts/instance_foo__bar-abc/run_script.sh"
  )
  assert PARSER_NAME == "parser.py"
