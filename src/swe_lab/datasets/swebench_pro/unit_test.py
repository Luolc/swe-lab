"""Compile a SWE-Bench Pro instance into the unit-test evaluation method.

Everything SWE-Bench-Pro-specific about *grading* lives here: the eval script
(ported from Scale's ``create_entryscript``), the compiled expectation, and a
stateless grader that reads the run's output files back. ``compile_unit_test``
turns a record into the general ``(SandboxSpec, UnitTestSpec)`` the method
consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import shlex
from typing import override, TYPE_CHECKING

from swe_lab.evaluation.verdict import Grader, UnitTestSpec
from swe_lab.sandbox import Inline, Mount, Mounts, SandboxFs, SandboxSpec

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
from .execution import image_ref

if TYPE_CHECKING:
  # Hints only — importing the record at runtime would cycle (record.py's
  # TaskInstance methods delegate to the compile_* functions below).
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

  def summary(self) -> dict[str, object]:
    """SWE-Bench-Pro report detail: output state + passed / missing."""
    return {
        "output_state": self.output_state.value,
        "passed": sorted(self.passed),
        "missing": sorted(self.missing),
    }


@dataclass(frozen=True)
class SweBenchProGrader(Grader[SweBenchProVerdict]):
  """Stateless grader: reads the output files a run left behind.

  Reads the parser's ``output.json`` (results) and the compiled
  ``required_tests.json`` (expectation) through the sandbox, so it carries no
  per-instance state and any persisted workspace re-grades without the dataset
  record.
  """

  @override
  def grade(self, sb: SandboxFs) -> SweBenchProVerdict:
    """Grade one run from ``output.json`` + ``required_tests.json``.

    Args:
      sb: The sandbox to read the run's output files through.

    Returns:
      The verdict; ``resolved`` iff the output parsed and the required tests
      (``fail_to_pass ∪ pass_to_pass``) all passed.
    """
    required = frozenset(json.loads(sb.read(REQUIRED_TESTS_NAME)))
    passed, output_state = _parse_output(sb)
    return SweBenchProVerdict(
        passed=passed,
        missing=required - passed,
        output_state=output_state,
    )


def _parse_output(sb: SandboxFs) -> tuple[frozenset[str], OutputState]:
  """Read the passed-test set + a state distinguishing absent from corrupt.

  Distinguishing "absent" from "unparseable" from "parsed" is what keeps a
  crashed parser (a harness fault) from masquerading as "no tests passed" (a
  real result).
  """
  if not sb.exists(OUTPUT_JSON_NAME):
    return frozenset(), OutputState.ABSENT
  try:
    data = json.loads(sb.read(OUTPUT_JSON_NAME))
  except (json.JSONDecodeError, OSError, ValueError):
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
  if checkout_golden_tests and instance.golden_test_checkout_cmd:
    lines.append(instance.golden_test_checkout_cmd)
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
) -> UnitTestSpec[SweBenchProVerdict]:
  """Compile one instance's unit-test evaluation spec.

  The run context is ``compile_sandbox_spec(instance)`` — the same for solving
  and grading — so it is not returned here; a caller gets it from there. The
  test harness comes straight from ``instance.run_script`` / ``instance.parser``
  (the instance decides how to obtain them), so this depends on no ``repo_root``
  and does no file round-trip.

  Args:
    instance: The instance to grade.
    patch: The candidate diff to apply, or ``None`` to grade the base commit
      untouched (a self-check that the required tests fail without a fix).
    checkout_golden_tests: Forwarded to the eval script (see its self-check
      modes).

  Returns:
    The compiled unit-test spec.
  """
  required = sorted(
      frozenset(instance.fail_to_pass) | frozenset(instance.pass_to_pass)
  )
  mounts: Mounts = {
      RUN_SCRIPT_NAME: Mount(Inline(instance.run_script)),
      PARSER_NAME: Mount(Inline(instance.parser)),
      REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(required).encode())),
  }
  if patch is not None:
    mounts[PATCH_NAME] = Mount(Inline(patch.encode()))
  eval_script = _build_eval_script(
      instance,
      apply_patch=patch is not None,
      checkout_golden_tests=checkout_golden_tests,
  )
  return UnitTestSpec(
      eval_script=eval_script,
      mounts=mounts,
      grader=SweBenchProGrader(),
  )


def compile_sandbox_spec(instance: SweBenchProInstance) -> SandboxSpec:
  """Compile an instance's run context (no eval mounts) — used by solving.

  Args:
    instance: The instance whose sandbox to bring up.

  Returns:
    The run context: image, workdir, and base commit.
  """
  return SandboxSpec(
      instance_id=instance.instance_id,
      image_ref=image_ref(instance.dockerhub_tag),
      workdir=WORKDIR,
      base_commit=instance.base_commit,
  )


def compile_solve_prompt(instance: SweBenchProInstance) -> str:
  """Compile an instance into the solve prompt handed to the headless agent.

  Mirrors Scale's ``create_problem_statement`` for SWE-Bench Pro: the three text
  columns are concatenated under fixed headers, unconditionally, so the agent
  sees the same task text the benchmark's own harness builds. The prompt is
  dataset-derived, so this mapping lives with the dataset — callers never reach
  into instance fields themselves.

  Args:
    instance: The instance to solve.

  Returns:
    The prompt string staged into the workspace for the agent to read.
  """
  return (
      f"{instance.problem_statement}\n\n"
      f"Requirements:\n{instance.requirements}\n\n"
      f"New interfaces introduced:\n{instance.interface}"
  )
