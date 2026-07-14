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


def build_entryscript(spec: EvalSpec, *, env_exports: str = "") -> str:
  """The in-container script (mirrors Scale's create_entryscript)."""
  setup_lines = spec.before_repo_set_cmd.strip().splitlines()
  last_setup = setup_lines[-1] if setup_lines else ""
  selected = ",".join(spec.selected_tests)
  return (
      f"{env_exports}\n"
      f"cd {spec.workdir}\n"
      f"git reset --hard {spec.base_commit}\n"
      f"git checkout {spec.base_commit}\n"
      "git apply -v /workspace/patch.diff\n"
      f"{last_setup}\n"
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
