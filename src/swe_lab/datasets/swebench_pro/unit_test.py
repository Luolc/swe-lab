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
from dataclasses import dataclass, replace
from enum import StrEnum
import json
import shlex
from typing import override

from swe_lab.evaluation.verdict import Grader, UnitTestSpec
from swe_lab.sandbox import Inline, Mount, Mounts, SandboxFs

from .constants import (
    BASH,
    EVAL_HOME,
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
    required: The expectation this was judged against (``fail_to_pass ∪
      pass_to_pass``). Kept so a run can report *how many* tests it was held to,
      not just how many it missed — ``0`` on a verdict built without it.
    attempts: How many evaluation attempts produced this verdict (ADR-0005).
      ``1`` unless the eval was re-run after a failure; the grader always builds
      it at ``1`` and the method annotates it afterwards.
  """

  passed: frozenset[str]
  missing: frozenset[str]
  output_state: OutputState
  required: frozenset[str] = frozenset()
  attempts: int = 1

  @property
  def score(self) -> float:
    """1.0 iff the output parsed and every required test passed, else 0.0."""
    ok = self.output_state is OutputState.OK and not self.missing
    return 1.0 if ok else 0.0

  @property
  def resolved(self) -> bool:
    """Whether the run is a full pass (``score >= 1.0``)."""
    return self.score >= 1.0

  @property
  def flaky(self) -> bool:
    """Whether it resolved only after a retry (ADR-0005).

    Derived rather than stored, so it cannot disagree with ``attempts``: a run
    that failed every attempt is not flaky, it is failed.
    """
    return self.attempts > 1 and self.resolved

  def with_attempts(self, attempts: int) -> SweBenchProVerdict:
    """Return this verdict with the attempt count recorded.

    Args:
      attempts: How many attempts the eval took, ``1`` or more.

    Returns:
      An identical verdict but for ``attempts``.
    """
    return replace(self, attempts=attempts)

  def summary(self) -> dict[str, object]:
    """SWE-Bench-Pro report detail: output state + passed / missing.

    ``first_missing`` is the scalar a persisted record keeps — the full
    ``missing`` list is for a human report, and would otherwise bloat a shard
    (one instance requires 681 tests). One name is usually enough to see *which*
    test family broke.
    """
    return {
        "output_state": self.output_state.value,
        "attempts": self.attempts,
        "flaky": self.flaky,
        "first_missing": min(self.missing) if self.missing else None,
        "passed": sorted(self.passed),
        "missing": sorted(self.missing),
    }

  def metrics(self) -> dict[str, float]:
    """Return counts a sweep can aggregate: passed / missing / required."""
    return {
        "passed": float(len(self.passed)),
        "missing": float(len(self.missing)),
        "required": float(len(self.required)),
        "attempts": float(self.attempts),
        "flaky": float(self.flaky),
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
        required=required,
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
  ``False`` for the dataset self-checks.

  Four **deliberate divergences** from the legacy builder, and no others: the
  workspace path is ``$SANDBOX_WORKSPACE`` rather than a fixed mount point; the
  previous attempt's outputs are deleted up front (ADR-0005); line endings are
  pinned; and ``HOME`` is guaranteed. Each is argued where it is emitted below,
  so the reasoning sits next to the line it justifies.

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
  # Scale's reference scrapes `ENV` lines out of each instance's Dockerfile;
  # unnecessary here, because Docker bakes `ENV` into the image and every
  # container process already inherits it.

  # Quoted, or bash expands a `$` in a test name and globs `[...]` out of a
  # pytest parametrize id.
  selected = shlex.quote(",".join(selected_test_files_to_run))
  lines = [
      # A failed *setup* step has to stop the run. Without this, a `git apply`
      # that failed fell through and the tests graded the wrong tree — an
      # unresolved verdict with nothing saying the patch never applied.
      "set -e",
      # An image that sets no HOME makes a toolchain that needs one fail every
      # test for a reason that looks nothing like the cause (Go's build cache
      # lives in `$HOME/.cache/go-build`). A *fallback*, so the image's own
      # value still wins: an instance image often pre-warms its caches under
      # it, and relocating would force a re-download that `--no-network` turns
      # from a slowdown into a failure.
      #
      # Each tier tests for a non-empty *value* rather than an exit code:
      # `getent` in a pipeline reports `cut`'s status, which succeeds on empty
      # input, so a UID with no passwd entry (`docker run -u 1000`, OpenShift's
      # random UIDs) would leave HOME empty — worse than unset, since `~/.cache`
      # then resolves to the unwritable `/.cache`.
      '[ -n "${HOME:-}" ] || HOME="$(getent passwd "$(id -u)" 2>/dev/null'
      ' | cut -d: -f6)"',
      f'[ -n "${{HOME:-}}" ] || HOME={EVAL_HOME}',
      "export HOME",
      'mkdir -p "$HOME"',
      f"cd {WORKDIR}",
      # Symmetric with extraction (ADR-0001), which diffs under
      # `core.autocrlf=false`: a checkout or apply that renormalized CRLF<->LF
      # would either fail to apply or silently alter content. Two knobs because
      # they cover different halves — `autocrlf` off stops conversion for files
      # with no `text` attribute, `eol=lf` fixes the checkout direction for
      # files that have one. Both are already git's default on Linux, so this
      # only bites an image that turned normalization on, which is the point.
      # Not airtight: a per-path `eol=` in the repo's own `.gitattributes` wins
      # over any config.
      #
      # Repo level rather than a per-invocation `-c`, because some of what
      # follows we do not author: the dataset's `golden_test_checkout_cmd` and
      # the harness's run script.
      "git config core.autocrlf false",
      "git config core.eol lf",
      (
          f"rm -f {_WS}/{OUTPUT_JSON_NAME} {_WS}/{STDOUT_LOG_NAME}"
          f" {_WS}/{STDERR_LOG_NAME}"
      ),
      f"git reset --hard {base_commit}",
      "git clean -fd",
      f"git checkout {base_commit}",
  ]
  if apply_patch:
    lines.append(f"git apply -v {_WS}/{PATCH_NAME}")
  if checkout_golden_tests and golden_test_checkout_cmd:
    lines.append(golden_test_checkout_cmd)
  lines += [
      # A failing test suite is a *result*, not an error: the parser still has
      # to run and turn it into output.json, so the run is gradeable.
      "set +e",
      (
          f"{BASH} {_WS}/{RUN_SCRIPT_NAME} {selected}"
          f" > {_WS}/{STDOUT_LOG_NAME} 2> {_WS}/{STDERR_LOG_NAME}"
      ),
      # The parser, though, must succeed — no output.json means no verdict.
      "set -e",
      (
          f"{PYTHON} {_WS}/{PARSER_NAME} {_WS}/{STDOUT_LOG_NAME}"
          f" {_WS}/{STDERR_LOG_NAME} {_WS}/{OUTPUT_JSON_NAME}"
      ),
  ]
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
      # Kept so a grading that went wrong can be read afterwards: the parsed
      # result, and — the useful part when it did go wrong — the raw test logs
      # the parser was fed. Registered best-effort by the method.
      native_outputs={
          OUTPUT_JSON_NAME: OUTPUT_JSON_NAME,
          STDOUT_LOG_NAME: STDOUT_LOG_NAME,
          STDERR_LOG_NAME: STDERR_LOG_NAME,
      },
  )
