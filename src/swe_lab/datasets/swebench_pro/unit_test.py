"""Compile a SWE-Bench Pro instance's fields into the unit-test method.

Everything SWE-Bench-Pro-specific about *grading* lives here: the eval script
(ported from Scale's ``create_entryscript``), the compiled expectation, and a
stateless grader that reads the run's output files back. ``compile_unit_test``
takes the instance's fields directly (not the record) and returns the general
``UnitTestSpec`` the method consumes, so this module never imports the dataset
record — the dependency runs one way, ``record`` → ``unit_test``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import shlex
from typing import override

from swe_lab.evaluation.verdict import Grader, UnitTestSpec
from swe_lab.sandbox import Inline, Mount, Mounts, SandboxFs

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
    *,
    base_commit: str,
    selected_test_files_to_run: Sequence[str],
    golden_test_checkout_cmd: str,
    apply_patch: bool,
    checkout_golden_tests: bool,
) -> str:
  """Build the in-container eval script (ports Scale's create_entryscript).

  Both flags default (via the caller) to the real grading flow; set them
  ``False`` for the dataset self-checks. Two **deliberate divergences** from the
  legacy builder, and no others:

  1. the workspace path is ``$SANDBOX_WORKSPACE``, not a fixed mount point;
  2. ``core.autocrlf`` is pinned ``false`` (see the comment below) — a knob the
     reference entryscript leaves alone, so a line-ending-sensitive instance can
     in principle grade differently here than under Scale's harness. Pinned
     anyway, because matching our own extraction (ADR-0001) matters more than
     matching an unset default: ``false`` *is* git's POSIX default, so this only
     bites an image that explicitly turned normalization on.

  Args:
    base_commit: The commit the working tree is reset to before grading.
    selected_test_files_to_run: The test files passed to the run script.
    golden_test_checkout_cmd: The command restoring the held-out golden tests
      after the reset (``""`` when the instance has none).
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
  selected = shlex.quote(",".join(selected_test_files_to_run))
  lines = [
      f"cd {WORKDIR}",
      # Pin line endings for every git command below, symmetric with extraction
      # (ADR-0001): a patch is diffed with ``core.autocrlf=false``, so a
      # checkout/apply that renormalizes CRLF<->LF would either fail to apply or
      # silently alter content. Set at **repo** level rather than per-invocation
      # ``-c`` because some of what follows we do not author — the dataset's own
      # ``golden_test_checkout_cmd``, and the harness's run script.
      "git config core.autocrlf false",
      f"git reset --hard {base_commit}",
      f"git checkout {base_commit}",
  ]
  if apply_patch:
    lines.append(f"git apply -v {_WS}/{PATCH_NAME}")
  if checkout_golden_tests and golden_test_checkout_cmd:
    lines.append(golden_test_checkout_cmd)
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
    *,
    patch: str | None,
    checkout_golden_tests: bool = True,
    base_commit: str,
    selected_test_files_to_run: Sequence[str],
    golden_test_checkout_cmd: str,
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
    run_script: bytes,
    parser: bytes,
) -> UnitTestSpec[SweBenchProVerdict]:
  """Compile one instance's unit-test evaluation spec from its fields.

  Takes the instance's fields directly rather than the record, so this grading
  module never imports the dataset (no import cycle). The test harness arrives
  as raw ``run_script`` / ``parser`` bytes — how the instance obtained them
  (fetch, cache, or an embedded column) is not this function's concern, so it
  needs no ``repo_root`` and does no file round-trip. The run context is a
  separate concern (``SweBenchProInstance.sandbox_spec``) and is not built here.

  Args:
    patch: The candidate diff to apply, or ``None`` to grade the base commit
      untouched (a self-check that the required tests fail without a fix).
    checkout_golden_tests: Forwarded to the eval script (see its self-check
      modes).
    base_commit: The commit the working tree is reset to before grading.
    selected_test_files_to_run: The test files passed to the run script.
    golden_test_checkout_cmd: The command restoring the held-out golden tests
      after the reset (``""`` when the instance has none).
    fail_to_pass: Tests that must flip to passing (part of the expectation).
    pass_to_pass: Tests that must stay passing (part of the expectation).
    run_script: The test-harness run script's content.
    parser: The output parser's content.

  Returns:
    The compiled unit-test spec.
  """
  required = sorted(frozenset(fail_to_pass) | frozenset(pass_to_pass))
  mounts: Mounts = {
      RUN_SCRIPT_NAME: Mount(Inline(run_script)),
      PARSER_NAME: Mount(Inline(parser)),
      REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(required).encode())),
  }
  if patch is not None:
    mounts[PATCH_NAME] = Mount(Inline(patch.encode()))
  eval_script = _build_eval_script(
      base_commit=base_commit,
      selected_test_files_to_run=selected_test_files_to_run,
      golden_test_checkout_cmd=golden_test_checkout_cmd,
      apply_patch=patch is not None,
      checkout_golden_tests=checkout_golden_tests,
  )
  return UnitTestSpec(
      eval_script=eval_script,
      mounts=mounts,
      grader=SweBenchProGrader(),
  )
