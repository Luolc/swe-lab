"""Tests for compile_unit_test: mounts + the ported eval-script builder.

Both are pure functions over an instance's *fields* (not the record), so these
tests pass the fields directly — the run-script / parser bytes inline, with no
harness fetch or cache. The instance's own runnable surface (the ``run_script``
/ ``parser`` properties, ``sandbox_spec``, ``solve_prompt``) is exercised in
test_swebench_pro.py.
"""

import json

from swe_lab.datasets.swebench_pro.constants import (
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    WORKDIR,
)
from swe_lab.datasets.swebench_pro.record import SweBenchProInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    _build_eval_script,
    compile_unit_test,
    REQUIRED_TESTS_NAME,
)
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


def _script(
    inst: SweBenchProInstance,
    *,
    apply_patch: bool,
    checkout_golden_tests: bool,
) -> str:
  # Feed the builder the instance's fields, the way SweBenchProInstance does.
  return _build_eval_script(
      base_commit=inst.base_commit,
      selected_test_files_to_run=inst.selected_test_files_to_run,
      golden_test_checkout_cmd=inst.golden_test_checkout_cmd,
      apply_patch=apply_patch,
      checkout_golden_tests=checkout_golden_tests,
  )


def _compile(
    inst: SweBenchProInstance,
    *,
    patch: str | None,
    run_script: bytes = b"echo run",
    parser: bytes = b"print('parse')",
    checkout_golden_tests: bool = True,
):
  return compile_unit_test(
      patch=patch,
      checkout_golden_tests=checkout_golden_tests,
      base_commit=inst.base_commit,
      selected_test_files_to_run=inst.selected_test_files_to_run,
      golden_test_checkout_cmd=inst.golden_test_checkout_cmd,
      fail_to_pass=inst.fail_to_pass,
      pass_to_pass=inst.pass_to_pass,
      run_script=run_script,
      parser=parser,
  )


# ─── the ported script builder ───────────────────────────────────────────────


def test_script_uses_sandbox_workspace_and_full_flow():
  script = _script(_instance(), apply_patch=True, checkout_golden_tests=True)
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


def test_script_pins_line_endings_before_any_git_command():
  # Symmetric with extraction (ADR-0001): the patch is diffed with
  # core.autocrlf=false, so nothing downstream may renormalize line endings.
  # Two knobs — autocrlf covers files with no `text` attribute, eol fixes the
  # checkout direction for files that have one. Repo-level and *before*
  # reset/checkout/apply, so they also govern the dataset-authored
  # golden-checkout line we do not write.
  pins = [
      "git config core.autocrlf false",
      "git config core.eol lf",
  ]
  lines = _script(
      _instance(), apply_patch=True, checkout_golden_tests=True
  ).splitlines()
  assert lines[0] == f"cd {WORKDIR}"  # inside the repo, so repo-local
  assert lines[1:3] == pins  # and set before anything else runs
  governed = [
      i
      for i, line in enumerate(lines)
      if line.startswith("git ") and line not in pins
  ]
  assert governed  # not vacuous: there are git commands to govern
  assert all(i > 2 for i in governed)


def test_script_flag_combinations():
  no_patch = _script(_instance(), apply_patch=False, checkout_golden_tests=True)
  assert "git apply" not in no_patch  # base-commit self-check
  no_golden = _script(
      _instance(), apply_patch=True, checkout_golden_tests=False
  )
  assert "git checkout Y -- test/foo.py" not in no_golden


def test_script_empty_before_cmd_has_no_restore_line():
  script = _script(
      _instance(before_repo_set_cmd=""),
      apply_patch=True,
      checkout_golden_tests=True,
  )
  assert "git checkout Y" not in script


def test_script_quotes_selected_tests():
  script = _script(
      _instance(selected_test_files_to_run="['a$b', 'c[d]']"),
      apply_patch=True,
      checkout_golden_tests=True,
  )
  assert "'a$b,c[d]'" in script  # single-quoted, no shell expansion


# ─── compile ─────────────────────────────────────────────────────────────────


def test_compile_mounts_the_harness_expectation_and_patch():
  unit = _compile(
      _instance(), patch="MY DIFF", run_script=b"echo run", parser=b"parse"
  )
  # mounts carry the harness bytes + the compiled expectation + the patch
  assert _content(unit.mounts[RUN_SCRIPT_NAME]) == b"echo run"
  assert _content(unit.mounts[PARSER_NAME]) == b"parse"
  required = json.loads(_content(unit.mounts[REQUIRED_TESTS_NAME]) or b"")
  assert required == ["test_a", "test_b"]  # sorted(fail ∪ pass)
  assert _content(unit.mounts["patch.diff"]) == b"MY DIFF"
  assert "git apply" in unit.eval_script


def test_compile_without_patch_omits_patch_mount_and_apply():
  unit = _compile(_instance(), patch=None)
  assert "patch.diff" not in unit.mounts
  assert "git apply" not in unit.eval_script
