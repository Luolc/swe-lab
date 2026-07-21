"""Tests for compile_unit_test: mounts, the ported script builder, spec."""

import json
from pathlib import Path

import pytest

from swe_lab.core.datasets.swebench_pro.constants import (
    HARNESS_SUBDIR,
    IMAGE_REPO,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    WORKDIR,
)
from swe_lab.core.datasets.swebench_pro.record import SweBenchProInstance
from swe_lab.core.datasets.swebench_pro.unit_test import (
    _build_eval_script,
    compile_unit_test,
    REQUIRED_TESTS_NAME,
)
from swe_lab.core.paths import cache_root

_BASE = {
    "repo": "acme/widget",
    "instance_id": "acme__widget-1",
    "base_commit": "abc123",
    "patch": "PATCH",
    "test_patch": "",
    "problem_statement": "p",
    "requirements": "",
    "interface": "",
    "repo_language": "python",
    "fail_to_pass": "['test_a']",
    "pass_to_pass": "['test_b']",
    "issue_specificity": "[]",
    "issue_categories": "[]",
    "before_repo_set_cmd": "git reset --hard X\ngit checkout Y -- test/foo.py",
    "selected_test_files_to_run": "['test/foo.py']",
    "dockerhub_tag": "widget-tag",
}


def _instance(**overrides: str) -> SweBenchProInstance:
  return SweBenchProInstance.from_raw({**_BASE, **overrides})


def _stage_harness(repo_root: Path, instance_id: str) -> None:
  harness = cache_root(repo_root) / HARNESS_SUBDIR / instance_id
  harness.mkdir(parents=True)
  _ = (harness / RUN_SCRIPT_NAME).write_text("echo run")
  _ = (harness / PARSER_NAME).write_text("print('parse')")


# ─── the ported script builder ───────────────────────────────────────────────


def test_script_uses_sandbox_workspace_and_full_flow():
  script = _build_eval_script(
      _instance(), apply_patch=True, checkout_golden_tests=True
  )
  assert f"cd {WORKDIR}" in script
  assert "git reset --hard abc123" in script
  assert 'git apply -v "$SANDBOX_WORKSPACE"/patch.diff' in script
  # golden restore = the LAST line of before_repo_set_cmd
  assert "git checkout Y -- test/foo.py" in script
  assert "git checkout X" not in script  # not the reset line
  # paths resolve via $SANDBOX_WORKSPACE, never a fixed mount point
  assert "/workspace/" not in script
  assert '"$SANDBOX_WORKSPACE"/run_script.sh' in script
  assert '"$SANDBOX_WORKSPACE"/output.json' in script


def test_script_flag_combinations():
  no_patch = _build_eval_script(
      _instance(), apply_patch=False, checkout_golden_tests=True
  )
  assert "git apply" not in no_patch  # base-commit self-check
  no_golden = _build_eval_script(
      _instance(), apply_patch=True, checkout_golden_tests=False
  )
  assert "git checkout Y -- test/foo.py" not in no_golden


def test_script_empty_before_cmd_has_no_restore_line():
  script = _build_eval_script(
      _instance(before_repo_set_cmd=""),
      apply_patch=True,
      checkout_golden_tests=True,
  )
  assert "git checkout Y" not in script


def test_script_quotes_selected_tests():
  script = _build_eval_script(
      _instance(selected_test_files_to_run="['a$b', 'c[d]']"),
      apply_patch=True,
      checkout_golden_tests=True,
  )
  assert "'a$b,c[d]'" in script  # single-quoted, no shell expansion


# ─── compile ─────────────────────────────────────────────────────────────────


def test_compile_mounts_and_spec(tmp_path: Path):
  _stage_harness(tmp_path, "acme__widget-1")
  spec, unit = compile_unit_test(
      _instance(), patch="MY DIFF", repo_root=tmp_path
  )
  assert spec.instance_id == "acme__widget-1"
  assert spec.image_ref == f"{IMAGE_REPO}:widget-tag"
  assert spec.workdir == WORKDIR
  assert spec.base_commit == "abc123"
  # mounts carry the harness + the compiled expectation + the patch
  assert unit.mounts[RUN_SCRIPT_NAME].content == b"echo run"
  assert unit.mounts[PARSER_NAME].content == b"print('parse')"
  required = json.loads(unit.mounts[REQUIRED_TESTS_NAME].content or b"")
  assert required == ["test_a", "test_b"]  # sorted(fail ∪ pass)
  assert unit.mounts["patch.diff"].content == b"MY DIFF"
  assert "git apply" in unit.eval_script


def test_compile_without_patch_omits_patch_mount_and_apply(tmp_path: Path):
  _stage_harness(tmp_path, "acme__widget-1")
  _, unit = compile_unit_test(_instance(), patch=None, repo_root=tmp_path)
  assert "patch.diff" not in unit.mounts
  assert "git apply" not in unit.eval_script


def test_compile_reuses_cached_harness_no_network(tmp_path: Path):
  # no network: fetch_harness must reuse the pre-staged files
  _stage_harness(tmp_path, "acme__widget-1")
  _, unit = compile_unit_test(_instance(), patch=None, repo_root=tmp_path)
  assert unit.mounts[RUN_SCRIPT_NAME].content == b"echo run"


def test_compile_missing_harness_would_fetch(tmp_path: Path):
  # nothing staged → fetch_harness attempts a download; assert it tries the
  # network (raising) rather than silently succeeding.
  with pytest.raises(Exception):  # noqa: B017 — any network/URL error is fine
    compile_unit_test(_instance(), patch=None, repo_root=tmp_path)
