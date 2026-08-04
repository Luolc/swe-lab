"""Tests for compile_unit_test: mounts + the ported eval-script builder.

Both are pure functions over an instance's *fields* (not the record), so these
tests pass the fields directly — the run-script / parser bytes inline, with no
harness fetch or cache. The instance's own runnable surface (the ``run_script``
/ ``parser`` properties, ``sandbox_spec``, ``prompt``) is exercised in
test_swebench_pro.py.
"""

import json

from swe_lab.datasets.swebench_pro.constants import (
    PARSER_NAME,
    PATCH_NAME,
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
      patch_name=PATCH_NAME,
      checkout_golden_tests=checkout_golden_tests,
  )


def _compile(
    inst: SweBenchProInstance,
    *,
    apply_patch: bool,
    patch_name: str = PATCH_NAME,
    run_script: bytes = b"echo run",
    parser: bytes = b"print('parse')",
    checkout_golden_tests: bool = True,
):
  return compile_unit_test(
      apply_patch=apply_patch,
      patch_name=patch_name,
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
  cd_at = lines.index(f"cd {WORKDIR}")
  # inside the repo (so repo-local), immediately after the cd
  assert lines[cd_at + 1 : cd_at + 3] == pins
  governed = [
      i
      for i, line in enumerate(lines)
      if line.startswith("git ") and line not in pins
  ]
  assert governed  # not vacuous: there are git commands to govern
  assert all(i > cd_at + 2 for i in governed)


def test_script_fails_fast_on_setup_but_not_on_the_test_run():
  # A failed `git apply` must abort: letting it fall through would run the tests
  # against the wrong tree and score the run unresolved with no hint why.
  # The test run itself is exempt — a failing suite is a result, and the parser
  # still has to turn it into output.json.
  lines = _script(
      _instance(), apply_patch=True, checkout_golden_tests=True
  ).splitlines()
  apply_at = next(
      i for i, line in enumerate(lines) if line.startswith("git apply")
  )
  run_at = next(i for i, line in enumerate(lines) if line.startswith("bash "))
  parse_at = next(
      i for i, line in enumerate(lines) if line.startswith("python ")
  )

  assert lines[0] == "set -e"  # armed before anything runs
  assert lines[run_at - 1] == "set +e"  # disarmed only around the suite
  assert lines[parse_at - 1] == "set -e"  # re-armed: no output.json, no verdict
  assert apply_at < run_at  # the patch is applied while still armed


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


def test_compile_mounts_the_harness_and_the_expectation():
  unit = _compile(
      _instance(), apply_patch=True, run_script=b"echo run", parser=b"parse"
  )
  # mounts carry the harness bytes + the compiled expectation, and nothing else
  assert _content(unit.mounts[RUN_SCRIPT_NAME]) == b"echo run"
  assert _content(unit.mounts[PARSER_NAME]) == b"parse"
  required = json.loads(_content(unit.mounts[REQUIRED_TESTS_NAME]) or b"")
  assert required == ["test_a", "test_b"]  # sorted(fail ∪ pass)


def test_the_eval_script_reads_the_patch_from_the_workspace_mount():
  # The named invariant: a compiled spec never carries patch BYTES. It says
  # which workspace file to apply, and whoever supplies the patch mounts it
  # under exactly that name — so a spec cannot silently grade a patch other
  # than the one the run declared as its input.
  unit = _compile(_instance(), apply_patch=True, patch_name="candidate.diff")
  assert unit.patch_name == "candidate.diff"
  assert "git apply -v " in unit.eval_script
  assert "candidate.diff" in unit.eval_script
  assert PATCH_NAME not in unit.eval_script
  assert not [name for name in unit.mounts if name.endswith(".diff")]


def test_compile_without_a_patch_applies_nothing():
  unit = _compile(_instance(), apply_patch=False)
  assert PATCH_NAME not in unit.mounts
  assert "git apply" not in unit.eval_script


def test_script_resolves_home_in_three_tiers():
  # Some images set no HOME, and a toolchain that needs one then fails every
  # test for a reason that looks nothing like the cause (Go's build cache lives
  # in $HOME/.cache/go-build). Three tiers, each testing for a *non-empty* value
  # rather than an exit code — `getent | cut` succeeds with empty output when
  # the UID has no passwd entry, and an empty HOME is worse than an unset one.
  lines = _script(
      _instance(), apply_patch=True, checkout_golden_tests=True
  ).splitlines()
  tiers = [i for i, line in enumerate(lines) if line.startswith('[ -n "${HOME')]
  assert len(tiers) == 2  # the image's own HOME is tier 1: nothing to do

  # tier 2 asks the passwd database for the account's real home ...
  assert "getent passwd" in lines[tiers[0]]
  assert "cut -d: -f6" in lines[tiers[0]]
  # ... tier 3 is the fallback constant
  assert lines[tiers[1]].endswith("|| HOME=/tmp/eval-home")

  assert lines[tiers[1] + 1] == "export HOME"
  assert lines[tiers[1] + 2] == 'mkdir -p "$HOME"'  # and it exists
  # resolved before anything that might need it
  assert lines.index(f"cd {WORKDIR}") > tiers[1]


def test_untracked_files_are_cleaned_before_the_patch_is_reapplied():
  # `reset --hard` restores tracked files but leaves untracked ones, so a patch
  # that ADDS files makes a retry's `git apply` die with "already exists" and
  # take the whole script down under `set -e` — before the tests ever run.
  # Reproduced in a container before this was written; the dataset's own
  # before_repo_set_cmd cleans here for the same reason.
  lines = _script(
      _instance(), apply_patch=True, checkout_golden_tests=True
  ).splitlines()
  reset = next(i for i, line in enumerate(lines) if "reset --hard" in line)
  clean = lines.index("git clean -fd")
  apply_ = next(
      i for i, line in enumerate(lines) if line.startswith("git apply")
  )
  assert reset < clean < apply_


def test_a_previous_attempts_outputs_are_removed_before_anything_else():
  # An attempt that aborts before the parser runs must not be graded from the
  # last attempt's output.json — that reports a stale verdict as its own, which
  # is how a broken retry looked like a working one.
  lines = _script(
      _instance(), apply_patch=True, checkout_golden_tests=True
  ).splitlines()
  removal = next(i for i, line in enumerate(lines) if line.startswith("rm -f "))
  for name in ("output.json", "stdout.log", "stderr.log"):
    assert name in lines[removal]
  # before the reset, so nothing between them can leave the old files behind
  reset = next(i for i, line in enumerate(lines) if "reset --hard" in line)
  assert removal < reset
  assert lines[0] == "set -e"  # and a failed removal still aborts
