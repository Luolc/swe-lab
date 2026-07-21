"""Run one instance's unit-test evaluation as a sandbox composition.

The eval script is staged as ``entryscript.sh`` and run by its workspace path;
a stateful ``EvalParseObserver`` grades the workspace in ``before_destroy`` and
holds the typed verdict for the caller to read back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import override

from swe_lab.evaluation.verdict import Grader, UnitTestSpec, Verdict
from swe_lab.sandbox import (
    Contribution,
    Mount,
    RunResult,
    Sandbox,
    SandboxBackend,
    SandboxError,
    SandboxManager,
    SandboxObserver,
    SandboxSpec,
)

ENTRYSCRIPT_NAME = "entryscript.sh"
_DEFAULT_TIMEOUT_S = 1800.0


@dataclass
class EvalParseObserver[V: Verdict](SandboxObserver):
  """Grade the workspace in ``before_destroy`` and keep the typed verdict.

  Single-run, like every stateful observer: construct a fresh one per run.

  Attributes:
    grader: Judges the workspace.
    verdict: The graded verdict; ``None`` until ``before_destroy`` has run.
  """

  grader: Grader[V]
  verdict: V | None = None

  @override
  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Grade the workspace, storing the verdict on this observer."""
    self.verdict = self.grader.grade(sb.workspace)
    return None


def run_unit_test[V: Verdict](
    sandbox_spec: SandboxSpec,
    unit_spec: UnitTestSpec[V],
    *,
    backend: SandboxBackend,
    workspace: Path,
    timeout: float = _DEFAULT_TIMEOUT_S,
    observers: Sequence[SandboxObserver] = (),
) -> tuple[RunResult, V | None]:
  """Run and grade one instance's unit-test evaluation.

  Args:
    sandbox_spec: The run context (image, workdir, base commit).
    unit_spec: The compiled eval script, mounts, and grader.
    backend: The backend that realizes the sandbox.
    workspace: The host workspace directory for this run.
    timeout: Seconds before the eval script is killed.
    observers: Extra observers composed alongside the eval-parse observer
      (e.g. a persist observer).

  Returns:
    The engine ``RunResult`` and the verdict. A setup failure (bad mounts, or
    the backend failing to bring the sandbox up) is captured in
    ``RunResult.status`` / ``RunResult.error`` rather than raised, and leaves
    the verdict ``None`` (grading never ran) — so a caller has one code path
    and gates on ``RunResult.status``.
  """
  parse: EvalParseObserver[V] = EvalParseObserver(unit_spec.grader)
  mounts = dict(unit_spec.mounts)
  mounts[ENTRYSCRIPT_NAME] = Mount(
      content=unit_spec.eval_script.encode(), executable=True
  )
  manager = SandboxManager(
      spec=sandbox_spec,
      backend=backend,
      workspace=workspace,
      observers=[*observers, parse],
      mounts=mounts,
  )
  try:
    with manager.sandbox() as sb:
      _ = sb.run(ENTRYSCRIPT_NAME, timeout=timeout)
  except SandboxError:
    pass  # the failure is recorded in manager.result; return it, don't raise
  return manager.result, parse.verdict
