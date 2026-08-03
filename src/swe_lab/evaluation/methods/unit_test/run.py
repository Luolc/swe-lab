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
    attempts: How many attempts the composition made (ADR-0005), set before
      teardown. Annotated onto the verdict, which derives ``flaky`` from it.
    retained: Extra artifacts kept from *failed* attempts (artifact name →
      workspace filename). The failing attempt is the one worth reading and the
      one a naive retry would overwrite.
    exec_result: The entryscript's own result, set by the composition before
      teardown; ``None`` if the body never got to run it.
    wall_seconds: How long the entryscript took — the *total* across
      attempts, since what a sweep wants from this is what the run cost.
  """

  grader: Grader[V]
  native_outputs: Mapping[str, str] = field(default_factory=dict)
  verdict: V | None = None
  attempts: int = 1
  retained: dict[str, str] = field(default_factory=dict)
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
    self.verdict = self.grader.grade(sb).with_attempts(self.attempts)
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

  def _exec_output(self) -> dict[str, bytes]:
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
        qualified_name(ARTIFACT_NAMESPACE, name): text.encode("utf-8")
        for name, text in streams.items()
        if text
    }

  def _declared_outputs(self) -> dict[str, str]:
    """Return the entryscript, the dataset's outputs, and retained attempts."""
    return {
        ENTRYSCRIPT_NAME: ENTRYSCRIPT_NAME,
        **self.native_outputs,
        **self.retained,
    }

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
    retries: int = 1,
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
    timeout: Seconds before *each* attempt is killed, so the worst-case wall
      clock is ``(retries + 1) * timeout``. Per-attempt rather than shared,
      because a shared deadline would make the last attempt's budget depend on
      how slow the earlier ones were.
    retries: Extra attempts allowed after a failed one (ADR-0005). ``0``
      disables retrying. The candidate patch is identical on every attempt, so
      this removes harness nondeterminism, not model error — but it costs a
      full re-run for every genuinely failing instance, which is why it is a
      knob and why the default is 1 rather than 2. **A spec carrying its own
      ``retries`` overrides this**, because the dataset knows an instance's
      measured rate and the caller does not.
    eval_env: Extra environment for the eval script (mirrors ``run_rollout``'s
      ``agent_env``). For a secret, use the sandbox's ``pass_env`` instead —
      that passes it by reference, so the value never reaches a command line.
    observers: Extra observers, composed **after** this method's own so they
      see the run once it has post-processed (e.g. a persist observer).

  Returns:
    The engine ``RunResult`` and the verdict, which carries ``attempts`` and
    derives ``flaky`` from it (resolved only after a retry). A setup failure
    (bad mounts, or the sandbox failing to come up) is captured in
    ``RunResult.status`` /
    ``RunResult.error`` rather than raised, and leaves the verdict ``None``
    (grading never ran) — so a caller has one code path and gates on
    ``RunResult.status``.

  Raises:
    ValueError: If ``retries`` is negative.
  """
  if retries < 0:
    raise ValueError(f"retries must be >= 0, got {retries}")
  # A spec may know better than its caller: the dataset has the measured rate,
  # the caller only has a default (ADR-0005).
  if unit_test_spec.retries is not None:
    if unit_test_spec.retries < 0:
      raise ValueError(
          f"spec retries must be >= 0, got {unit_test_spec.retries}"
      )
    retries = unit_test_spec.retries
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
      # Backend observers first: they measure the whole run (ADR-0007 §3).
      observers=[*sandbox.observers(), parse, *observers],
      mounts=mounts,
  )
  try:
    with manager.session() as sb:
      _attempt_until_resolved(
          sb,
          parse,
          unit_test_spec,
          retries=retries,
          timeout=timeout,
          eval_env=eval_env,
      )
  except SandboxError:
    pass  # the failure is recorded in manager.result; return it, don't raise
  return _with_timeout_status(manager.result, parse.exec_result), parse.verdict


def _attempt_until_resolved[V: Verdict](
    sb: SandboxFs,
    parse: EvalParseObserver[V],
    unit_test_spec: UnitTestSpec[V],
    *,
    retries: int,
    timeout: float,
    eval_env: Mapping[str, str] | None,
) -> None:
  """Run the entryscript, re-running it while it fails and budget remains.

  Re-running is a *clean* repeat, not a resumption: the entryscript begins with
  ``git reset --hard`` + ``git checkout`` of the base commit and re-applies the
  patch and golden tests, so an attempt inherits nothing from its predecessor
  but the container's warm caches (ADR-0005).

  The patch never changes between attempts, so this averages out the harness's
  nondeterminism rather than giving the candidate another chance.

  Args:
    sb: The live sandbox to run in.
    parse: The observer the attempt state is handed to before teardown.
    unit_test_spec: Supplies the grader that decides whether to retry.
    retries: Extra attempts allowed after the first.
    timeout: Seconds before *each* attempt is killed.
    eval_env: Extra environment for the entryscript.
  """
  elapsed = 0.0
  for attempt in range(1, retries + 2):
    parse.attempts = attempt
    started = time.monotonic()
    try:
      parse.exec_result = sb.run_script(
          ENTRYSCRIPT_NAME, timeout=timeout, env=eval_env
      )
    finally:
      # Total across attempts, not the last one: what a sweep needs from this
      # is what the run *cost*.
      elapsed += time.monotonic() - started
      parse.wall_seconds = elapsed
    if attempt > retries:
      return  # budget spent; the observer grades whatever this left behind
    if unit_test_spec.grader.grade(sb).resolved:
      return
    _retain_attempt(sb, parse, attempt, unit_test_spec.native_outputs)


def _retain_attempt[V: Verdict](
    sb: SandboxFs,
    parse: EvalParseObserver[V],
    attempt: int,
    native_outputs: Mapping[str, str],
) -> None:
  """Copy a failed attempt's outputs aside so the retry cannot overwrite them.

  Copied *through the sandbox* rather than with a shell command, so it works on
  every backend and in a Docker-free test.

  Args:
    sb: The live sandbox holding the attempt's outputs.
    parse: The observer whose ``retained`` map registers them for collection.
    attempt: Which attempt these outputs came from, 1-based.
    native_outputs: The dataset's byproducts (artifact name → filename).
  """
  for filename in sorted(set(native_outputs.values())):
    if not sb.exists(filename):
      continue  # best effort: an attempt that died early leaves fewer files
    kept = f"attempt{attempt}.{filename}"
    sb.write(kept, sb.read(filename))
    parse.retained[kept] = kept


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
