"""Compile a SWE-Bench Pro instance into the unit-test evaluation method.

Everything SWE-Bench-Pro-specific about *grading* lives here: the eval script
(ported from Scale's ``create_entryscript``), the compiled expectation, and a
stateless grader that reads the workspace back. ``compile_unit_test`` turns a
record into the general ``(SandboxSpec, UnitTestSpec)`` the method consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import shlex

from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Mount, Mounts, SandboxSpec

from .constants import (
    BASH,
    OUTPUT_JSON_NAME,
    PARSER_NAME,
    PATCH_NAME,
    PYTHON,
    RUN_SCRIPT_NAME,
    STDERR_LOG_NAME,
    STDOUT_LOG_NAME,
    WORKDIR,
)
from .execution import fetch_harness, image_ref
from .record import SweBenchProInstance

# The compiled expectation, staged into the workspace and read by the grader.
REQUIRED_TESTS_NAME = "required_tests.json"
# The workspace path as seen in-container; set by the backend on every run.
_WS = '"$SANDBOX_WORKSPACE"'


class OutputState(StrEnum):
  """Whether the parser's ``output.json`` could be read."""

  OK = "ok"
  ABSENT = "absent"  # the parser never produced output.json
  UNPARSEABLE = "unparseable"  # present but corrupt/unreadable


@dataclass(frozen=True, slots=True)
class SweBenchProVerdict:
  """The graded outcome of one SWE-Bench Pro run.

  Attributes:
    passed: Names of the tests the parser reported as passed.
    missing: Required test names not in ``passed``.
    output_state: Whether ``output.json`` was found and readable.
  """

  passed: frozenset[str]
  missing: frozenset[str]
  output_state: OutputState

  @property
  def score(self) -> float:
    """1.0 iff the output parsed and every required test passed, else 0.0."""
    ok = self.output_state is OutputState.OK and not self.missing
    return 1.0 if ok else 0.0

  @property
  def resolved(self) -> bool:
    """Whether the run is a full pass (``score >= 1.0``)."""
    return self.score >= 1.0


@dataclass(frozen=True)
class SweBenchProGrader:
  """Stateless grader: reads the workspace files a run left behind.

  Reads the parser's ``output.json`` (results) and the compiled
  ``required_tests.json`` (expectation), so it carries no per-instance state
  and any persisted workspace re-grades without the dataset record.
  """

  def grade(self, workspace: Path) -> SweBenchProVerdict:
    """Grade one run from ``output.json`` + ``required_tests.json``.

    Args:
      workspace: The workspace the run left behind.

    Returns:
      The verdict; ``resolved`` iff the output parsed and the required tests
      (``fail_to_pass ∪ pass_to_pass``) all passed.
    """
    required = frozenset(
        json.loads((workspace / REQUIRED_TESTS_NAME).read_text())
    )
    passed, output_state = _parse_output(workspace / OUTPUT_JSON_NAME)
    return SweBenchProVerdict(
        passed=passed,
        missing=required - passed,
        output_state=output_state,
    )


def _parse_output(
    output_json: Path,
) -> tuple[frozenset[str], OutputState]:
  """Read the passed-test set + a state distinguishing absent from corrupt.

  Distinguishing "absent" from "unparseable" from "parsed" is what keeps a
  crashed parser (a harness fault) from masquerading as "no tests passed" (a
  real result).
  """
  if not output_json.is_file():
    return frozenset(), OutputState.ABSENT
  try:
    data = json.loads(output_json.read_text())
  except (json.JSONDecodeError, OSError):
    return frozenset(), OutputState.UNPARSEABLE
  if not isinstance(data, dict):
    return frozenset(), OutputState.UNPARSEABLE
  tests = data.get("tests", [])
  passed = frozenset(
      test["name"]
      for test in tests
      if isinstance(test, dict) and test.get("status") == "PASSED"
  )
  return passed, OutputState.OK


def _build_eval_script(
    instance: SweBenchProInstance,
    *,
    apply_patch: bool,
    checkout_golden_tests: bool,
) -> str:
  """Build the in-container eval script (ports Scale's create_entryscript).

  Both flags default (via the caller) to the real grading flow; set them
  ``False`` for the dataset self-checks. The only change from the legacy
  builder is the workspace path: ``$SANDBOX_WORKSPACE`` instead of a fixed
  mount point.

  Args:
    instance: The instance to grade.
    apply_patch: Apply ``patch.diff`` after resetting to the base commit.
    checkout_golden_tests: Restore the golden test files after the reset.

  Returns:
    The entryscript text, newline-terminated.
  """
  # Unlike Scale's reference, we do not scrape ``ENV`` lines from the
  # per-instance Dockerfiles: Docker's ``ENV`` bakes them into the image, so
  # every container process already inherits them.
  #
  # In SWE-Bench Pro, ``before_repo_set_cmd`` is a block whose last line
  # restores the golden test files by path (so a candidate patch cannot game
  # the held-out tests); Scale takes the same ``splitlines()[-1]``.
  before = instance.before_repo_set_cmd.strip()
  golden_test_checkout = before.splitlines()[-1] if before else ""
  # shlex.quote wraps the joined test list in single quotes so bash cannot
  # expand a ``$`` in a test name or glob-expand ``[...]`` from a
  # pytest parametrize id.
  selected = shlex.quote(",".join(instance.selected_test_files_to_run))
  lines = [
      f"cd {WORKDIR}",
      f"git reset --hard {instance.base_commit}",
      f"git checkout {instance.base_commit}",
  ]
  if apply_patch:
    lines.append(f"git apply -v {_WS}/{PATCH_NAME}")
  if checkout_golden_tests and golden_test_checkout:
    lines.append(golden_test_checkout)
  lines.append(
      f"{BASH} {_WS}/{RUN_SCRIPT_NAME} {selected}"
      f" > {_WS}/{STDOUT_LOG_NAME} 2> {_WS}/{STDERR_LOG_NAME}"
  )
  lines.append(
      f"{PYTHON} {_WS}/{PARSER_NAME} {_WS}/{STDOUT_LOG_NAME}"
      f" {_WS}/{STDERR_LOG_NAME} {_WS}/{OUTPUT_JSON_NAME}"
  )
  return "\n".join(lines) + "\n"


def compile_unit_test(
    instance: SweBenchProInstance,
    *,
    patch: str | None,
    checkout_golden_tests: bool = True,
    repo_root: Path | None = None,
) -> tuple[SandboxSpec, UnitTestSpec[SweBenchProVerdict]]:
  """Compile one instance into a runnable unit-test evaluation.

  Args:
    instance: The instance to grade.
    patch: The candidate diff to apply, or ``None`` to grade the base commit
      untouched (a self-check that the required tests fail without a fix).
    checkout_golden_tests: Forwarded to the eval script (see its self-check
      modes).
    repo_root: Repo root used to locate the cached harness; auto-detected
      when omitted.

  Returns:
    The run context and the compiled unit-test spec.
  """
  run_script, parser = fetch_harness(instance.instance_id, repo_root=repo_root)
  required = sorted(
      frozenset(instance.fail_to_pass) | frozenset(instance.pass_to_pass)
  )
  mounts: Mounts = {
      RUN_SCRIPT_NAME: Mount(content=run_script.read_bytes()),
      PARSER_NAME: Mount(content=parser.read_bytes()),
      REQUIRED_TESTS_NAME: Mount(content=json.dumps(required).encode()),
  }
  if patch is not None:
    mounts[PATCH_NAME] = Mount(content=patch.encode())
  eval_script = _build_eval_script(
      instance,
      apply_patch=patch is not None,
      checkout_golden_tests=checkout_golden_tests,
  )
  sandbox_spec = SandboxSpec(
      instance_id=instance.instance_id,
      image_ref=image_ref(instance.dockerhub_tag),
      workdir=WORKDIR,
      base_commit=instance.base_commit,
  )
  unit_spec = UnitTestSpec(
      eval_script=eval_script,
      mounts=mounts,
      grader=SweBenchProGrader(),
  )
  return sandbox_spec, unit_spec
