"""Grade a patch against a SWE-Bench Pro instance in its container.

SWE-Bench-Pro-specific: this ports Scale's ``swe_bench_pro_eval.py``
harness. It stages the workspace Scale's grader expects (``patch.diff`` +
``run_script.sh`` + ``parser.py`` + ``entryscript.sh``), runs the entryscript in
the instance's image (cd workdir → reset+checkout base_commit → apply patch →
restore the golden test files → run_script → parser), and decides resolved from
``output.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex

from swe_lab.core.benchmark import EvalSpec
from swe_lab.core.docker.provider import DockerProvider
from swe_lab.core.paths import cache_root, find_repo_root

from .constants import (
    BASH,
    ENTRYSCRIPT_NAME,
    MOUNT_AT,
    OUTPUT_JSON_NAME,
    PARSER_NAME,
    PATCH_NAME,
    PYTHON,
    RUN_SCRIPT_NAME,
    STDERR_LOG_NAME,
    STDOUT_LOG_NAME,
    WORKSPACES_SUBDIR,
)


@dataclass(frozen=True)
class EvalResult:
  """Outcome of grading one patch.

  Attributes:
    instance_id: The graded instance's id.
    resolved: True iff ``output.json`` was found and every required test
      (``fail_to_pass`` plus ``pass_to_pass``) passed.
    passed: Sorted names of the tests the parser reported as passed.
    missing: Sorted required test names not in ``passed``.
    exit_code: Exit code of the container run.
    timed_out: Whether the container run hit the timeout.
    output_found: Whether the parser produced ``output.json``.
  """

  instance_id: str
  resolved: bool
  passed: tuple[str, ...]
  missing: tuple[str, ...]
  exit_code: int
  timed_out: bool
  output_found: bool


def build_eval_script(
    spec: EvalSpec,
    *,
    apply_patch: bool = True,
    checkout_golden_tests: bool = True,
) -> str:
  """Build the in-container eval script (ports Scale's create_entryscript).

  Both flags default to the real grading flow; set them to ``False`` for
  dataset self-checks.

  Args:
    spec: The eval spec identifying workdir, base commit, and tests.
    apply_patch: Apply ``patch.diff`` after resetting to the base commit. Set
      to ``False`` to grade the base commit untouched, confirming the
      required tests *fail* without the fix.
    checkout_golden_tests: Restore the golden test files after the reset. Set
      to ``False`` to confirm the base commit *passes* when the bug-exposing
      tests aren't added (i.e. the golden tests are what detect the
      regression).

  Returns:
    The entryscript text, newline-terminated.
  """
  # Unlike Scale's reference, we do not scrape ``ENV`` lines from the
  # per-instance Dockerfiles and re-export them.  Docker's ``ENV`` instruction
  # bakes variables into the image; they are automatically inherited by every
  # container process, so re-exporting them is redundant and saves a per-run
  # Dockerfile fetch.
  #
  # In SWE-Bench Pro, ``before_repo_set_cmd`` is always a 4-line block: the
  # first three lines reset the repo (reset/clean/checkout to base_commit); the
  # last line restores the golden test files by path, e.g.
  #   git checkout f327e65d -- test/units/cli/test_galaxy.py test/units/...
  # This ensures a candidate patch cannot modify the test files for grading.
  # Scale's reference takes the same ``split("\n")[-1]`` approach.
  golden_test_checkout = (
      spec.before_repo_set_cmd.strip().splitlines()[-1]
      if spec.before_repo_set_cmd.strip()
      else ""
  )
  # shlex.quote wraps the argument in single quotes, preventing bash from
  # expanding $ in test names (e.g. TestMalformedOpMsg/empty_$db_key → empty_)
  # and glob-expanding [...] brackets from pytest parametrize IDs.  The current
  # dataset silently works without this because the one affected instance also
  # has the parent test name in the selected list, which causes Go to run all
  # subtests regardless; quoting is nonetheless the correct defensive practice.
  selected = shlex.quote(",".join(spec.selected_tests))
  lines = [
      f"cd {spec.workdir}",
      f"git reset --hard {spec.base_commit}",
      f"git checkout {spec.base_commit}",
  ]
  if apply_patch:
    lines.append(f"git apply -v {MOUNT_AT}/{PATCH_NAME}")
  if checkout_golden_tests and golden_test_checkout:
    lines.append(golden_test_checkout)
  lines.append(
      f"{BASH} {MOUNT_AT}/{RUN_SCRIPT_NAME} {selected}"
      f" > {MOUNT_AT}/{STDOUT_LOG_NAME} 2> {MOUNT_AT}/{STDERR_LOG_NAME}"
  )
  lines.append(
      f"{PYTHON} {MOUNT_AT}/{PARSER_NAME} {MOUNT_AT}/{STDOUT_LOG_NAME}"
      f" {MOUNT_AT}/{STDERR_LOG_NAME} {MOUNT_AT}/{OUTPUT_JSON_NAME}"
  )
  return "\n".join(lines) + "\n"


def evaluate(
    spec: EvalSpec,
    *,
    patch: str | None = None,
    provider: DockerProvider | None = None,
    workspace: Path | None = None,
    repo_root: Path | None = None,
    timeout: float = 1800.0,
    network: bool = True,
    checkout_golden_tests: bool = True,
) -> EvalResult:
  """Run the instance's tests in its image and grade the outcome.

  Args:
    spec: The eval spec identifying image, workdir, and tests.
    patch: The candidate diff, applied with ``git apply``; pass ``None`` to
      grade the base commit untouched (e.g. to confirm the required tests
      fail without a fix).
    provider: Runs the container; defaults to a fresh ``DockerProvider``.
    workspace: Directory staged with the harness files and bind-mounted into
      the container; defaults to a per-instance dir under the gitignored
      cache — pass one to run in an isolated location.
    repo_root: Repo root used to locate the default cache; auto-detected when
      omitted.
    timeout: Seconds before the container run is killed.
    network: Whether the container gets network access.
    checkout_golden_tests: Forwarded to :func:`build_eval_script` (see its
      self-check modes).

  Returns:
    The graded result; ``resolved`` is True iff all required tests passed.
  """
  apply_patch = patch is not None
  provider = provider or DockerProvider()
  if workspace is None:
    root = repo_root or find_repo_root()
    workspace = cache_root(root) / WORKSPACES_SUBDIR / spec.instance_id
  workspace.mkdir(parents=True, exist_ok=True)
  # Drop any stale grade artifact so a crashed run can't be graded off a
  # previous run's output.json.
  (workspace / OUTPUT_JSON_NAME).unlink(missing_ok=True)
  if apply_patch:
    # Write the patch verbatim — we grade exactly what we're given. We do NOT
    # strip binary hunks here (Scale does): the patch reaching the grader is
    # already binary-free — gold patches are binary-free across all 731
    # instances, and rollout patches are stripped upstream by the rollout runner
    # (extraction uses git add -N + no --binary, then strip_binary_hunks). So
    # there is nothing left to strip. See ADR-0001.
    _ = (workspace / PATCH_NAME).write_text(patch)
  _ = (workspace / RUN_SCRIPT_NAME).write_text(spec.run_script)
  _ = (workspace / PARSER_NAME).write_text(spec.parser)
  _ = (workspace / ENTRYSCRIPT_NAME).write_text(
      build_eval_script(
          spec,
          apply_patch=apply_patch,
          checkout_golden_tests=checkout_golden_tests,
      )
  )

  run = provider.run_script(
      spec.image_ref,
      workspace,
      ENTRYSCRIPT_NAME,
      mount_at=MOUNT_AT,
      timeout=timeout,
      network=network,
  )

  output_path = workspace / OUTPUT_JSON_NAME
  passed = _passed_tests(output_path)
  output_found = output_path.is_file()
  resolved = output_found and spec.is_resolved(passed)
  missing = tuple(sorted(spec.required_tests - passed))
  return EvalResult(
      instance_id=spec.instance_id,
      resolved=resolved,
      passed=tuple(sorted(passed)),
      missing=missing,
      exit_code=run.exit_code,
      timed_out=run.timed_out,
      output_found=output_found,
  )


def _passed_tests(output_json: Path) -> frozenset[str]:
  if not output_json.is_file():
    return frozenset()
  try:
    data = json.loads(output_json.read_text())
  except (json.JSONDecodeError, OSError):
    return frozenset()
  tests = data.get("tests", []) if isinstance(data, dict) else []
  return frozenset(
      test["name"]
      for test in tests
      if isinstance(test, dict) and test.get("status") == "PASSED"
  )
