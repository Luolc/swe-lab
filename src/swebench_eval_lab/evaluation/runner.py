"""Grade a patch by running an instance's tests in its container.

Dataset-agnostic: consumes an :class:`EvalSpec` (built by the dataset adapter)
plus a candidate patch, assembles the workspace Scale's harness expects
(``patch.diff`` + ``run_script.sh`` + ``parser.py`` + ``entryscript.sh``), runs
it in the container, and decides resolved from ``output.json``. The entryscript
mirrors Scale's ``swe_bench_pro_eval.py`` (cd workdir → reset+checkout
base_commit → apply patch → last line of before_repo_set_cmd → run_script →
parser).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex

from swebench_eval_lab.core.benchmark import EvalSpec
from swebench_eval_lab.core.docker.provider import DockerProvider
from swebench_eval_lab.core.patch import strip_binary_hunks
from swebench_eval_lab.core.paths import cache_root, find_repo_root

_ENTRYSCRIPT = "entryscript.sh"
_OUTPUT_JSON = "output.json"


@dataclass(frozen=True)
class EvalResult:
  """Outcome of grading one patch."""

  instance_id: str
  resolved: bool
  passed: tuple[str, ...]
  missing: tuple[str, ...]  # required tests not in passed
  returncode: int
  timed_out: bool
  output_found: bool


def build_entryscript(spec: EvalSpec) -> str:
  """The in-container script (mirrors Scale's create_entryscript).

  Scale's reference implementation (swe_bench_pro_eval.py:create_entryscript)
  scrapes every ``ENV`` line from the per-instance base and instance Dockerfiles
  and re-emits them as ``export`` statements at the top of this script.  We
  deliberately omit that step.

  Docker's ``ENV`` instruction bakes variables into the image's environment:
  they are automatically inherited by every process started in a container run
  from that image, regardless of the entrypoint or the script being executed.
  There
  is no need to ``export`` them again inside the script — they are already
  present.  This was verified empirically: running
  ``docker run --entrypoint /bin/bash <image> -c env`` on a known instance
  (ansible, 2026-07-15) showed ``PYTEST_ADDOPTS``, ``UV_HTTP_TIMEOUT``, and
  ``DEBIAN_FRONTEND`` all set without any manual export, and the gold self-test
  for that instance resolved correctly.

  Scale's re-export is therefore redundant.  Omitting it avoids the need to
  fetch and parse Dockerfiles (one HTTP round-trip per instance per run) while
  producing identical behaviour.
  """
  # In SWE-bench Pro, ``before_repo_set_cmd`` is always a 4-line block: the
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
  # expanding $ in test names (e.g. TestMalformedOpMsg/empty_$db_key → empty)
  # and glob-expanding [...] brackets from pytest parametrize IDs.  The current
  # dataset silently works without this because the one affected instance also
  # has the parent test name in the selected list, which causes Go to run all
  # subtests regardless; quoting is nonetheless the correct defensive practice.
  selected = shlex.quote(",".join(spec.selected_tests))
  return (
      f"cd {spec.workdir}\n"
      f"git reset --hard {spec.base_commit}\n"
      f"git checkout {spec.base_commit}\n"
      "git apply -v /workspace/patch.diff\n"
      f"{golden_test_checkout}\n"
      f"bash /workspace/run_script.sh {selected}"
      " > /workspace/stdout.log 2> /workspace/stderr.log\n"
      "python /workspace/parser.py /workspace/stdout.log"
      " /workspace/stderr.log /workspace/output.json\n"
  )


def evaluate(
    spec: EvalSpec,
    patch: str,
    provider: DockerProvider | None = None,
    *,
    repo_root: Path | None = None,
    timeout: float = 1800.0,
    network: bool = True,
) -> EvalResult:
  """Apply ``patch``, run the instance's tests in its image, and grade."""
  provider = provider or DockerProvider()
  root = repo_root or find_repo_root()
  workspace = cache_root(root) / "eval_workspaces" / spec.instance_id
  workspace.mkdir(parents=True, exist_ok=True)
  # Match Scale's grader: strip binary hunks before `git apply` so a
  # binary-containing patch it would strip-then-apply doesn't fail our strict
  # single apply (see docs/patch-extraction.md §1, §8).
  _ = (workspace / "patch.diff").write_text(strip_binary_hunks(patch))
  _ = (workspace / "run_script.sh").write_text(spec.run_script)
  _ = (workspace / "parser.py").write_text(spec.parser)
  _ = (workspace / _ENTRYSCRIPT).write_text(build_entryscript(spec))

  run = provider.run_script(
      spec.image_ref, workspace, _ENTRYSCRIPT, timeout=timeout, network=network
  )

  output_path = workspace / _OUTPUT_JSON
  passed = _passed_tests(output_path)
  output_found = output_path.is_file()
  resolved = output_found and spec.is_resolved(passed)
  missing = tuple(sorted(spec.required_tests - passed))
  return EvalResult(
      instance_id=spec.instance_id,
      resolved=resolved,
      passed=tuple(sorted(passed)),
      missing=missing,
      returncode=run.returncode,
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
