"""Run one instance's unit-test evaluation as a task over the engine.

``UnitTestEvalTask`` is the composition (ADR-0007): it stages the compiled
eval script as ``entryscript.sh``, runs it by its workspace path — re-running
on a failed grade while the ADR-0005 budget lasts — and drives a stateful
``EvalParseObserver`` that grades the workspace in ``before_destroy`` and
holds the typed verdict. ``run_unit_test`` remains the frozen-signature
wrapper for callers that hand a compiled spec rather than an instance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import time
from typing import final, override

from etils import epath

from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import Grader, UnitTestSpec, Verdict
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    ExecResult,
    Inline,
    merge_mounts,
    Mount,
    Mounts,
    qualified_name,
    RunResult,
    Sandbox,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.workflow import Task

ENTRYSCRIPT_NAME = "entryscript.sh"
# The default observer name: namespaces this method's artifacts and metrics,
# so `stdout.log` says whose it is and a second eval method cannot collide.
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
    name: This observer's own identifier, namespacing everything it registers
      (artifacts and metrics) — the same role ``Harness.name`` plays for the
      outcome observer, so a second eval method cannot collide with this one.
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
  name: str = ARTIFACT_NAMESPACE
  verdict: V | None = None
  attempts: int = 1
  retained: dict[str, str] = field(default_factory=dict)
  exec_result: ExecResult | None = None
  wall_seconds: float | None = None

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the eval script and the dataset's byproducts.

    The entryscript always lands (it was staged, so a run whose sandbox came
    up has it); the dataset's outputs are best-effort, mirroring how they are
    registered — a run that died mid-script produces fewer. ``retained`` is
    not declared: which attempts fail is only known at run time, and a schema
    describes what a run is *supposed* to produce.
    """
    return (
        ArtifactSchema(
            qualified_name(self.name, ENTRYSCRIPT_NAME),
            description="the eval script that ran",
        ),
        *(
            ArtifactSchema(
                qualified_name(self.name, name),
                required=False,
                description=f"the eval run's {name}",
            )
            for name in self.native_outputs
        ),
    )

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
        qualified_name(self.name, name): filename
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
        qualified_name(self.name, name): text.encode("utf-8")
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
        qualified_name(self.name, name): value
        for name, value in metrics.items()
    }


@dataclass
class UnitTestEvalTask[V: Verdict](Task):
  """Grade a patch against the bound instance's unit tests (ADR-0007).

  The eval composition as a task: the spec is compiled from the instance once
  at construction, its script staged as ``entryscript.sh``, and the run
  graded by an ``EvalParseObserver`` carrying the dataset's grader directly —
  no vehicle in between (ADR-0007 §4). Single-run per ``execute``, like every
  task.

  The patch, when this task applies one, is an **input** (``input_schema``)
  — it always arrives as a mounted ``patch.diff``, whether a workflow edge
  resolves it from an upstream task's output or a standalone caller hands
  the bytes through ``execute``'s ``extra_mounts``. One channel, so the
  schema is a fixed property of the task's configuration, never of where
  this particular run's data happens to come from.

  Attributes:
    instance: The dataset instance whose tests judge the patch.
    apply_patch: Compile the eval script to apply ``patch.diff`` and declare
      it as this task's required input. ``False`` grades the tree the
      compiled spec produces untouched — the base-commit self-check, and the
      wrapper's spec path, where the precompiled spec already settled the
      question.
    retries: Extra attempts after a failed grade (ADR-0005, in-run). ``0``
      disables retrying. **A spec carrying its own ``retries`` overrides
      this**, because the dataset knows an instance's measured rate and the
      caller does not.
    eval_env: Extra environment for the eval script. For a secret, use the
      sandbox's ``pass_env`` instead — that passes it by reference, so the
      value never reaches a command line.
  """

  instance: TaskInstance[V]
  apply_patch: bool = True
  retries: int = 1
  eval_env: Mapping[str, str] | None = None
  _spec: UnitTestSpec[V] = field(init=False, repr=False)
  _retries: int = field(init=False, repr=False)
  _parse: EvalParseObserver[V] = field(init=False, repr=False)

  def __post_init__(self) -> None:
    """Compile the spec once and resolve the retry budget (ADR-0005).

    In apply mode the spec is compiled with an empty placeholder patch — the
    script must ``git apply patch.diff``, but the actual bytes are this
    task's declared input and arrive by mount; the placeholder never
    survives (``mounts`` drops it).

    Validation happens here — at construction, before anything runs — so a
    bad budget raises to the caller instead of surfacing as a failed run.

    Raises:
      ValueError: If a retry budget (the caller's or the spec's) is negative.
    """
    if self.retries < 0:
      raise ValueError(f"retries must be >= 0, got {self.retries}")
    self._spec = self.instance.unit_test_spec(
        patch="" if self.apply_patch else None
    )
    # A spec may know better than its caller: the dataset has the measured
    # rate, the caller only has a default (ADR-0005).
    if self._spec.retries is not None:
      if self._spec.retries < 0:
        raise ValueError(f"spec retries must be >= 0, got {self._spec.retries}")
      self._retries = self._spec.retries
    else:
      self._retries = self.retries

  @override
  def mounts(self) -> Mounts:
    """Stage the compiled spec's files plus the entryscript.

    The compiled spec is self-contained — its ``mounts`` carry everything the
    eval script reads, including any instance fix's files — so the
    instance-material default is deliberately not merged on top of it. In
    apply mode the compilation placeholder for ``patch.diff`` is dropped:
    the patch is this task's *input*, staged by whoever supplies it (a
    workflow edge, or the caller's ``extra_mounts``), never by the task.

    Returns:
      The staging set: the spec's mounts (minus the patch placeholder in
      apply mode) plus the executable entryscript.
    """
    mounts = dict(self._spec.mounts)
    if self.apply_patch:
      del mounts[PATCH_NAME]
    return merge_mounts(
        mounts,
        {
            ENTRYSCRIPT_NAME: Mount(
                Inline(self._spec.eval_script.encode()), executable=True
            )
        },
    )

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Return the parse observer, carrying the dataset's grader directly.

    The reference is kept on the task because ``action``'s retry loop drives
    this observer between attempts — the task is single-run per ``execute``,
    like the observer itself.

    Returns:
      The one-element observer set.
    """
    self._parse = EvalParseObserver(
        self._spec.grader, native_outputs=self._spec.native_outputs
    )
    return (self._parse,)

  @override
  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the patch input — fixed by configuration, not by data origin.

    Returns:
      The ``patch.diff`` input in apply mode, else nothing.
    """
    if self.apply_patch:
      return (
          ArtifactSchema(
              PATCH_NAME, description="the candidate patch to grade"
          ),
      )
    return ()

  @override
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    """Run the entryscript, re-running while it fails and budget remains.

    In-run retry is ADR-0005's and stays inside the action — a workaround
    slated to be replaced by task-level retry (ADR-0007 §6), at which point
    a new ADR supersedes ADR-0005 and this loop goes away. ``timeout`` is
    per attempt, so the worst-case wall clock is ``(retries + 1) * timeout``
    — per-attempt rather than shared, because a shared deadline would make
    the last attempt's budget depend on how slow the earlier ones were.

    Args:
      sb: The live sandbox to run in.
      timeout: Seconds before *each* attempt is killed.

    Returns:
      The last attempt's execution result.
    """
    return _attempt_until_resolved(
        sb,
        self._parse,
        self._spec,
        retries=self._retries,
        timeout=timeout,
        eval_env=self.eval_env,
    )


@final
class _SpecInstance[V: Verdict](TaskInstance[V]):
  """The wrapper path's instance: the caller compiled the spec themselves.

  ``run_unit_test`` receives a spec, not a dataset record, so this adapter
  serves the precompiled spec back to the task and answers nothing else —
  the wrapper path has nothing else to answer.
  """

  def __init__(
      self, unit_test_spec: UnitTestSpec[V], spec: SandboxSpec
  ) -> None:
    """Wrap the caller's compiled spec and the sandbox's run context.

    Args:
      unit_test_spec: The compiled spec, served back verbatim.
      spec: The run context (supplies the instance id).
    """
    self._unit_test_spec = unit_test_spec
    self._spec = spec
    self.instance_id = spec.instance_id

  @override
  def sandbox_spec(self) -> SandboxSpec:
    """Return the run context the caller's sandbox already carries."""
    return self._spec

  @override
  def prompt(self) -> str:
    """Unavailable — a spec-holding caller grades, it does not solve.

    Returns:
      Never — this method only raises.

    Raises:
      NotImplementedError: Always; the wrapper path has no dataset record.
    """
    raise NotImplementedError("a compiled spec carries no prompt")

  @override
  def gold_patch(self) -> str | None:
    """Unavailable — a compiled spec carries no reference solution.

    Returns:
      Never — this method only raises.

    Raises:
      NotImplementedError: Always; the wrapper path has no dataset record.
    """
    raise NotImplementedError("a compiled spec carries no gold patch")

  @override
  def unit_test_spec(
      self,
      *,
      patch: str | None,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[V]:
    """Serve the precompiled spec back; its inputs are already baked in.

    Args:
      patch: Ignored — the caller compiled the spec with its patch.
      checkout_golden_tests: Ignored, for the same reason.

    Returns:
      The spec the wrapper was handed.
    """
    del patch, checkout_golden_tests
    return self._unit_test_spec


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

  A thin wrapper (frozen signature) over ``UnitTestEvalTask``: the compiled
  spec is adapted into the task's instance seam and the result reshaped into
  the historical pair — construction and reshaping, no logic of its own.
  Kept for backward compatibility; running the task inside a workflow
  (ADR-0007) is the intended entry point once workflows land, and this
  wrapper is then slated for deprecation.

  The sandbox is **injected, already constructed** — this function neither
  chooses a backend nor threads its construction options (workspace / pull /
  network / …). The caller builds it (``build_sandbox(...)`` or a fake) and
  owns every construction knob, so adding one never touches this signature and
  a test passes a ``FakeSandbox`` directly.

  Args:
    sandbox: The live sandbox to run the eval in.
    unit_test_spec: The compiled eval script, mounts, and grader.
    output_dir: The host directory collected artifacts are fetched into.
    timeout: Seconds before *each* attempt is killed (see
      ``UnitTestEvalTask.action``).
    retries: Extra attempts allowed after a failed one (ADR-0005); a spec
      carrying its own ``retries`` overrides this (see ``UnitTestEvalTask``).
    eval_env: Extra environment for the eval script (mirrors ``run_rollout``'s
      ``agent_env``). For a secret, use the sandbox's ``pass_env`` instead —
      that passes it by reference, so the value never reaches a command line.
    observers: Extra observers, composed **after** this method's own so they
      see the run once it has post-processed (e.g. a persist observer).

  Returns:
    The engine ``RunResult`` and the verdict, which carries ``attempts`` and
    derives ``flaky`` from it (resolved only after a retry). A setup failure
    (bad mounts, or the sandbox failing to come up) is captured in
    ``RunResult.status`` / ``RunResult.error`` rather than raised, and leaves
    the verdict ``None`` (grading never ran) — so a caller has one code path
    and gates on ``RunResult.status``.

  Raises:
    ValueError: If a retry budget (this argument's or the spec's) is negative.
  """
  if retries < 0:
    # Also validated by the task's constructor; re-checked here so the
    # docstring's contract is visibly this function's own.
    raise ValueError(f"retries must be >= 0, got {retries}")
  task: UnitTestEvalTask[V] = UnitTestEvalTask(
      instance=_SpecInstance(unit_test_spec, sandbox.spec),
      apply_patch=False,  # the spec already encodes whether a patch applies
      retries=retries,
      eval_env=eval_env,
  )
  result = task.execute(
      sandbox,
      output_dir=output_dir,
      timeout=timeout,
      extra_observers=observers,
  )
  # The observer the task kept for its retry loop holds the typed verdict.
  return result.run, task._parse.verdict


def _attempt_until_resolved[V: Verdict](
    sb: SandboxFs,
    parse: EvalParseObserver[V],
    unit_test_spec: UnitTestSpec[V],
    *,
    retries: int,
    timeout: float,
    eval_env: Mapping[str, str] | None,
) -> ExecResult:
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

  Returns:
    The last attempt's execution result (also handed to ``parse``).
  """
  elapsed = 0.0
  attempt = 0
  while True:
    attempt += 1
    parse.attempts = attempt
    started = time.monotonic()
    try:
      exec_result = sb.run_script(
          ENTRYSCRIPT_NAME, timeout=timeout, env=eval_env
      )
      parse.exec_result = exec_result
    finally:
      # Total across attempts, not the last one: what a sweep needs from this
      # is what the run *cost*.
      elapsed += time.monotonic() - started
      parse.wall_seconds = elapsed
    if attempt > retries:
      return exec_result  # budget spent; grade whatever this left behind
    if unit_test_spec.grader.grade(sb).resolved:
      return exec_result
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
