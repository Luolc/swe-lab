"""Run one instance's unit-test evaluation as a sandbox composition.

The eval script is staged as ``entryscript.sh`` and run by its workspace path;
a stateful ``EvalParseObserver`` grades the workspace in ``before_destroy`` and
holds the typed verdict for the caller to read back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
import time
from typing import override

from etils import epath

from swe_lab.evaluation.verdict import Grader, UnitTestSpec, Verdict
from swe_lab.sandbox import (
    Contribution,
    ExecResult,
    Inline,
    InlineArtifact,
    Mount,
    qualified_name,
    RunResult,
    RunStatus,
    Sandbox,
    SandboxError,
    SandboxFs,
    SandboxManager,
    SandboxObserver,
)

ENTRYSCRIPT_NAME = "entryscript.sh"
# Namespaces this method's artifacts and metrics, so `stdout.log` says whose it
# is and a second eval method cannot collide with it.
ARTIFACT_NAMESPACE = "eval"
_DEFAULT_TIMEOUT_S = 1800.0


@dataclass
class EvalParseObserver[V: Verdict](SandboxObserver):
  """Grade the workspace in ``before_destroy``; collect the run's evidence.

  Single-run, like every stateful observer: construct a fresh one per run.

  Beyond the verdict this registers everything needed to explain a grade after
  the fact — the eval script that ran, the parsed result, the raw test logs, and
  how the execution itself ended. A grading that goes wrong is otherwise a bare
  ``resolved: false`` with nothing to look at.

  Attributes:
    grader: Judges the workspace.
    native_outputs: The eval script's byproducts (artifact name → filename), as
      declared by the dataset's spec; registered only if they landed.
    verdict: The graded verdict; ``None`` until ``before_destroy`` has run.
    exec_result: The entryscript's own result, set by the composition before
      teardown; ``None`` if the body never got to run it.
    wall_seconds: How long the entryscript took, set alongside it.
  """

  grader: Grader[V]
  native_outputs: Mapping[str, str] = field(default_factory=dict)
  verdict: V | None = None
  exec_result: ExecResult | None = None
  wall_seconds: float | None = None

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Grade the run, then register its artifacts and metrics.

    Args:
      sb: The still-live sandbox — read through, never a host path.

    Returns:
      The eval's artifacts (best effort: only files that landed) and its
      metrics (the verdict's, plus how the execution ended).
    """
    self.verdict = self.grader.grade(sb)
    artifacts = {
        qualified_name(ARTIFACT_NAMESPACE, name): filename
        # Same best-effort filter as the diff-extract observer: a run that died
        # mid-script simply registers fewer files, never a broken reference.
        for name, filename in self._declared_outputs().items()
        if sb.exists(filename)
    }
    return Contribution(
        artifacts=artifacts,
        inline_artifacts=self._exec_output(),
        metrics=self._metrics(),
    )

  def _exec_output(self) -> dict[str, InlineArtifact]:
    """Keep the entryscript's *own* stdout/stderr — the fail-fast diagnostic.

    Not the test logs (those are files the script redirects into): this is what
    the script itself said. When ``set -e`` aborts at, say, ``git apply``, the
    test logs are never created and this is the only record of *why* — the exit
    code alone says a step failed, not which or how. Already on the host, so it
    is contributed inline; empty streams are skipped rather than persisted as
    empty objects.
    """
    if self.exec_result is None:
      return {}
    streams = {
        "exec_stdout.log": self.exec_result.stdout,
        "exec_stderr.log": self.exec_result.stderr,
    }
    return {
        qualified_name(ARTIFACT_NAMESPACE, name): InlineArtifact(
            name, text.encode("utf-8")
        )
        for name, text in streams.items()
        if text
    }

  def _declared_outputs(self) -> dict[str, str]:
    """Return the entryscript plus whatever the dataset says it produces."""
    return {ENTRYSCRIPT_NAME: ENTRYSCRIPT_NAME, **self.native_outputs}

  def _metrics(self) -> dict[str, float]:
    """Verdict scalars + how the execution ended, all namespaced."""
    metrics: dict[str, float] = {}
    if self.verdict is not None:
      metrics["score"] = self.verdict.score
      metrics["resolved"] = float(self.verdict.resolved)
      metrics |= self.verdict.metrics()
    if self.wall_seconds is not None:
      metrics["wall_seconds"] = self.wall_seconds
    if self.exec_result is not None:
      metrics["exit_code"] = float(self.exec_result.exit_code)
      metrics["timed_out"] = float(self.exec_result.timed_out)
    return {
        qualified_name(ARTIFACT_NAMESPACE, name): value
        for name, value in metrics.items()
    }


def run_unit_test[V: Verdict](
    sandbox: Sandbox,
    unit_test_spec: UnitTestSpec[V],
    *,
    output_dir: epath.PathLike,
    timeout: float = _DEFAULT_TIMEOUT_S,
    eval_env: Mapping[str, str] | None = None,
    observers: Sequence[SandboxObserver] = (),
) -> tuple[RunResult, V | None]:
  """Run and grade one instance's unit-test evaluation.

  The sandbox is **injected, already constructed** — this function neither
  chooses a backend nor threads its construction options (workspace / pull /
  network / …). The caller builds it (``build_sandbox(...)`` or a fake) and owns
  every construction knob, so adding one never touches this signature and a test
  passes a ``FakeSandbox`` directly.

  Args:
    sandbox: The live sandbox to run the eval in.
    unit_test_spec: The compiled eval script, mounts, and grader.
    output_dir: The host directory collected artifacts are fetched into.
    timeout: Seconds before the eval script is killed.
    eval_env: Extra environment for the eval script (mirrors ``run_rollout``'s
      ``agent_env``). For a secret, use the sandbox's ``pass_env`` instead —
      that passes it by reference, so the value never reaches a command line.
    observers: Extra observers, composed **after** this method's own so they
      see the run once it has post-processed (e.g. a persist observer).

  Returns:
    The engine ``RunResult`` and the verdict. A setup failure (bad mounts, or
    the sandbox failing to come up) is captured in ``RunResult.status`` /
    ``RunResult.error`` rather than raised, and leaves the verdict ``None``
    (grading never ran) — so a caller has one code path and gates on
    ``RunResult.status``.
  """
  parse: EvalParseObserver[V] = EvalParseObserver(
      unit_test_spec.grader, native_outputs=unit_test_spec.native_outputs
  )
  mounts = dict(unit_test_spec.mounts)
  mounts[ENTRYSCRIPT_NAME] = Mount(
      Inline(unit_test_spec.eval_script.encode()), executable=True
  )
  manager = SandboxManager(
      sandbox=sandbox,
      output_dir=epath.Path(output_dir),
      observers=[parse, *observers],
      mounts=mounts,
  )
  try:
    with manager.session() as sb:
      # Hand the execution's own outcome to the observer *before* teardown, so
      # before_destroy can report it. Dropping it (as this used to) made a
      # timed-out eval indistinguishable from one that merely produced nothing.
      started = time.monotonic()
      try:
        parse.exec_result = sb.run_script(
            ENTRYSCRIPT_NAME, timeout=timeout, env=eval_env
        )
      finally:
        parse.wall_seconds = time.monotonic() - started
  except SandboxError:
    pass  # the failure is recorded in manager.result; return it, don't raise
  return _with_timeout_status(manager.result, parse.exec_result), parse.verdict


def _with_timeout_status(
    result: RunResult, exec_result: ExecResult | None
) -> RunResult:
  """Promote a timed-out execution to ``RunStatus.TIMEOUT``.

  The engine cannot see this itself: a timeout does not raise, it comes back as
  a timed-out ``ExecResult``, so the manager assembles ``SUCCESS``. Only the
  method knows better, and says so here rather than leaving a killed run
  indistinguishable from one that produced nothing.

  Args:
    result: The engine's assembled result.
    exec_result: The entryscript's outcome, if it ran.

  Returns:
    ``result`` unchanged, or a copy with ``TIMEOUT`` when the body was killed.
  """
  if exec_result is not None and exec_result.timed_out:
    return replace(result, status=RunStatus.TIMEOUT)
  return result
