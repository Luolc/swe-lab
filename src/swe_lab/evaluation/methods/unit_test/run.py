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
    build_sandbox,
    Contribution,
    Inline,
    Mount,
    RunResult,
    SandboxError,
    SandboxFs,
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
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Grade the run's output files, storing the verdict on this observer."""
    self.verdict = self.grader.grade(sb)
    return None


def run_unit_test[V: Verdict](
    sandbox_spec: SandboxSpec,
    unit_test_spec: UnitTestSpec[V],
    *,
    backend: str,
    workspace: Path,
    timeout: float = _DEFAULT_TIMEOUT_S,
    pull: bool = True,
    network: bool = False,
    observers: Sequence[SandboxObserver] = (),
) -> tuple[RunResult, V | None]:
  """Run and grade one instance's unit-test evaluation.

  Args:
    sandbox_spec: The run context (image, workdir, base commit).
    unit_test_spec: The compiled eval script, mounts, and grader.
    backend: The registered backend name that realizes the sandbox.
    workspace: The host workspace directory for this run.
    timeout: Seconds before the eval script is killed.
    pull: Whether to pull the image before the run (A-host only).
    network: Whether the grading container gets network access.
    observers: Extra observers composed alongside the eval-parse observer
      (e.g. a persist observer).

  Returns:
    The engine ``RunResult`` and the verdict. A setup failure (bad mounts, or
    the sandbox failing to come up) is captured in ``RunResult.status`` /
    ``RunResult.error`` rather than raised, and leaves the verdict ``None``
    (grading never ran) — so a caller has one code path and gates on
    ``RunResult.status``.
  """
  parse: EvalParseObserver[V] = EvalParseObserver(unit_test_spec.grader)
  mounts = dict(unit_test_spec.mounts)
  mounts[ENTRYSCRIPT_NAME] = Mount(
      Inline(unit_test_spec.eval_script.encode()), executable=True
  )
  sandbox = build_sandbox(
      backend, sandbox_spec, workspace, network=network, pull=pull
  )
  manager = SandboxManager(
      sandbox=sandbox,
      output_dir=workspace,
      observers=[*observers, parse],
      mounts=mounts,
  )
  try:
    with manager.session() as sb:
      _ = sb.run_script(ENTRYSCRIPT_NAME, timeout=timeout)
  except SandboxError:
    pass  # the failure is recorded in manager.result; return it, don't raise
  return manager.result, parse.verdict
