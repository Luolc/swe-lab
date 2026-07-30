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
  """

  passed: frozenset[str]
  missing: frozenset[str]
  output_state: OutputState
  required: frozenset[str] = frozenset()

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
    """SWE-Bench-Pro report detail: output state + passed / missing.

    ``first_missing`` is the scalar a persisted record keeps — the full
    ``missing`` list is for a human report, and would otherwise bloat a shard
    (one instance requires 681 tests). One name is usually enough to see *which*
    test family broke.
    """
    return {
        "output_state": self.output_state.value,
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
  ``False`` for the dataset self-checks. Three **deliberate divergences** from
  the legacy builder, and no others:

  1. the workspace path is ``$SANDBOX_WORKSPACE``, not a fixed mount point;
  2. line endings are pinned — ``core.autocrlf=false`` + ``core.eol=lf`` (see
     the comment below) — knobs the reference entryscript leaves alone, so a
     line-ending-sensitive instance can in principle grade differently here than
     under Scale's harness. Pinned anyway, because matching our own extraction
     (ADR-0001) matters more than matching an unset default: both values *are*
     git's effective default on Linux, so this only bites an image that
     explicitly turned normalization on.
  3. ``HOME`` is guaranteed (see the comment below) — a *fallback*, so an
     image that sets one keeps it and nothing about its caches changes.

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
      # Abort on the first failed *setup* step. Without this a failed
      # `git apply` fell through and the tests ran against the wrong tree,
      # scoring the run unresolved with no hint that the patch never applied —
      # a silently wrong grade. `set +e` is lifted again around the test run
      # below, which is *expected* to exit non-zero.
      "set -e",
      # Guarantee a writable HOME. Some images set none, and a toolchain that
      # needs one then fails every test for a reason that looks nothing like the
      # cause (Go's build cache lives in `$HOME/.cache/go-build`). `:-` keeps an
      # image's own HOME when it has one, so a pre-warmed dependency cache under
      # it still counts — replacing it would force a re-download, and under
      # `--no-network` that is a failure rather than a slowdown.
      f'export HOME="${{HOME:-{EVAL_HOME}}}"',
      'mkdir -p "$HOME"',
      f"cd {WORKDIR}",
      # Pin line endings for every git command below, symmetric with extraction
      # (ADR-0001): a patch is diffed with ``core.autocrlf=false``, so a
      # checkout/apply that renormalizes CRLF<->LF would either fail to apply or
      # silently alter content. Two knobs, because they cover different halves:
      # ``autocrlf`` off stops conversion for files with no ``text`` attribute,
      # and ``eol=lf`` fixes the checkout direction for files that *do* have one
      # (where ``autocrlf=false`` alone leaves it at the platform's ``native``).
      # Both are already git's effective default on Linux, so this only bites an
      # image that turned normalization on — that is the point. A per-path
      # ``eol=`` in the repo's own ``.gitattributes`` still wins; no config
      # overrides that.
      #
      # Set at **repo** level rather than per-invocation ``-c`` because some of
      # what follows we do not author — the dataset's own
      # ``golden_test_checkout_cmd``, and the harness's run script.
      "git config core.autocrlf false",
      "git config core.eol lf",
      f"git reset --hard {base_commit}",
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
      # What the eval script leaves behind. Registered best-effort by the
      # method, so a grading that went wrong can be read after the fact: the
      # parsed result, and — the useful part when it did go wrong — the raw
      # test logs the parser was fed.
      native_outputs={
          OUTPUT_JSON_NAME: OUTPUT_JSON_NAME,
          STDOUT_LOG_NAME: STDOUT_LOG_NAME,
          STDERR_LOG_NAME: STDERR_LOG_NAME,
      },
  )
