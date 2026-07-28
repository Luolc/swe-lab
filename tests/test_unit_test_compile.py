"""Tests for compile_unit_test: mounts, the ported script builder, spec."""

import json
from pathlib import Path
from typing import override

import pytest

from swe_lab.datasets.swebench_pro.constants import (
    HARNESS_SUBDIR,
    IMAGE_REPO,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    WORKDIR,
)
import swe_lab.datasets.swebench_pro.execution as execution
from swe_lab.datasets.swebench_pro.record import SweBenchProInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    _build_eval_script,
    compile_sandbox_spec,
    compile_solve_prompt,
    compile_unit_test,
    REQUIRED_TESTS_NAME,
)
from swe_lab.paths import cache_root
from swe_lab.sandbox import Inline, Mount

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


def _content(mount: Mount) -> bytes:
  assert isinstance(mount.resource, Inline)
  return mount.resource.content


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


def _use_staged_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  """Point the default harness cache at tmp_path (where _stage_harness writes).

  `compile_unit_test` no longer takes a repo_root — the harness comes from the
  instance's ``run_script`` / ``parser`` properties, whose default fetch caches
  under ``find_repo_root()``. Redirect that to tmp_path so the staged files win.
  """
  monkeypatch.setattr(execution, "find_repo_root", lambda: tmp_path)


def test_compile_mounts_and_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _use_staged_harness(monkeypatch, tmp_path)
  _stage_harness(tmp_path, "acme__widget-1")
  spec = compile_sandbox_spec(_instance())
  unit = compile_unit_test(_instance(), patch="MY DIFF")
  assert spec.instance_id == "acme__widget-1"
  assert spec.image_ref == f"{IMAGE_REPO}:widget-tag"
  assert spec.workdir == WORKDIR
  assert spec.base_commit == "abc123"
  # mounts carry the harness + the compiled expectation + the patch
  assert _content(unit.mounts[RUN_SCRIPT_NAME]) == b"echo run"
  assert _content(unit.mounts[PARSER_NAME]) == b"print('parse')"
  required = json.loads(_content(unit.mounts[REQUIRED_TESTS_NAME]) or b"")
  assert required == ["test_a", "test_b"]  # sorted(fail ∪ pass)
  assert _content(unit.mounts["patch.diff"]) == b"MY DIFF"
  assert "git apply" in unit.eval_script


def test_compile_without_patch_omits_patch_mount_and_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _use_staged_harness(monkeypatch, tmp_path)
  _stage_harness(tmp_path, "acme__widget-1")
  unit = compile_unit_test(_instance(), patch=None)
  assert "patch.diff" not in unit.mounts
  assert "git apply" not in unit.eval_script


def test_compile_reuses_cached_harness_no_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # no network: the property reuses the pre-staged cache
  _use_staged_harness(monkeypatch, tmp_path)
  _stage_harness(tmp_path, "acme__widget-1")
  unit = compile_unit_test(_instance(), patch=None)
  assert _content(unit.mounts[RUN_SCRIPT_NAME]) == b"echo run"


def test_compile_missing_harness_would_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # nothing staged → the default property fetches; assert it tries the
  # network (raising) rather than silently succeeding.
  _use_staged_harness(monkeypatch, tmp_path)
  with pytest.raises(Exception):  # noqa: B017 — any network/URL error is fine
    compile_unit_test(_instance(), patch=None)


def test_run_script_and_parser_are_overridable():
  # the harness is the instance's business: a subclass supplies it directly,
  # so compile needs no network and no repo_root.
  class _Embedded(SweBenchProInstance):

    @property
    @override
    def run_script(self) -> bytes:
      return b"EMBEDDED RUN"

    @property
    @override
    def parser(self) -> bytes:
      return b"EMBEDDED PARSE"

  unit = compile_unit_test(_Embedded.from_raw(_BASE), patch=None)
  assert _content(unit.mounts[RUN_SCRIPT_NAME]) == b"EMBEDDED RUN"
  assert _content(unit.mounts[PARSER_NAME]) == b"EMBEDDED PARSE"


def test_compile_solve_prompt_combines_the_three_columns():
  # mirrors Scale's create_problem_statement verbatim
  prompt = compile_solve_prompt(
      _instance(
          problem_statement="The widget crashes on empty input.",
          requirements="Must not raise on None.",
          interface="def render(widget) -> str",
      )
  )
  assert prompt == (
      "The widget crashes on empty input.\n\n"
      "Requirements:\nMust not raise on None.\n\n"
      "New interfaces introduced:\ndef render(widget) -> str"
  )


def test_compile_solve_prompt_keeps_headers_when_columns_empty():
  # headers are unconditional, like the original (no per-section omission)
  prompt = compile_solve_prompt(
      _instance(problem_statement="Just the statement.")
  )
  assert "Just the statement." in prompt
  assert "Requirements:" in prompt
  assert "New interfaces introduced:" in prompt
