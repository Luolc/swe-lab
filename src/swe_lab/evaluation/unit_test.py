"""Run one instance's unit-test evaluation as a task over the engine.

``UnitTestTask`` is the composition (ADR-0007): it stages the compiled script
as ``entryscript.sh``, runs it once by its workspace path, and composes a
``UnitTestParseObserver`` that grades the workspace in ``before_destroy`` and
holds the typed verdict. Retrying a flaky grade is the runner's job now, one
fresh sandbox per attempt (ADR-0008).

The task is **stateless**: it compiles the spec from the instance it is handed,
and reads its verdict back off the execution's own observers — so the same
declaration runs against any instance, any number of times.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, override

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
    SandboxError,
    SandboxFs,
    SandboxObserver,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.workflow import AttemptResult, Task

ENTRYSCRIPT_NAME = "entryscript.sh"
# The default observer name: namespaces this method's artifacts and metrics,
# so `stdout.log` says whose it is and a second evaluation method cannot
# collide. It is this method's name — the same word the store key, the
# registered workflow, and this module all use.
ARTIFACT_NAMESPACE = "unit_test"


@dataclass
class UnitTestParseObserver[V: Verdict](SandboxObserver):
  """Grade the workspace in ``before_destroy``; collect the run's evidence.

  Single-run, like every stateful observer: construct a fresh one per run.

  Beyond the verdict this registers everything needed to explain a grade after
  the fact — the script that ran, the parsed result, the raw test logs, and
  how the execution itself ended. A grading that goes wrong is otherwise a bare
  ``resolved: false`` with nothing to look at.

  Attributes:
    grader: Judges the workspace.
    native_outputs: The script's byproducts (artifact name → filename), as
      declared by the dataset's spec; registered only if they landed.
    name: This observer's own identifier, namespacing everything it registers
      (artifacts and metrics) — the same role ``Harness.name`` plays for the
      outcome observer, so a second method cannot collide with this one.
    verdict: The graded verdict; ``None`` until ``before_destroy`` has run.
    exec_result: The entryscript's own result, set by the composition before
      teardown; ``None`` if the body never got to run it.
    wall_seconds: How long the entryscript took.
  """

  grader: Grader[V]
  native_outputs: Mapping[str, str] = field(default_factory=dict)
  name: str = ARTIFACT_NAMESPACE
  verdict: V | None = None
  exec_result: ExecResult | None = None
  wall_seconds: float | None = None

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the compiled script and the dataset's byproducts.

    The entryscript always lands (it was staged, so a run whose sandbox came
    up has it); the dataset's outputs are best-effort, mirroring how they are
    registered — a run that died mid-script produces fewer.
    """
    return (
        ArtifactSchema(
            qualified_name(self.name, ENTRYSCRIPT_NAME),
            description="the script that ran",
        ),
        *(
            ArtifactSchema(
                qualified_name(self.name, name),
                required=False,
                description=f"the run's {name}",
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
      The run's artifacts (best effort: only files that landed) and its
      metrics (the verdict's, plus how the execution ended).
    """
    self.verdict = self.grader.grade(sb)
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
    """Return the entryscript plus the dataset's own outputs."""
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
        qualified_name(self.name, name): value
        for name, value in metrics.items()
    }


def gold_patch(
    sb: SandboxFs, instance: TaskInstance[Any]
) -> Mapping[str, bytes]:
  """Build the task's patch input from the instance's own gold patch.

  The standalone self-check shape: grading the reference solution needs no
  upstream task and no caller-held bytes, so the task supplies its own input
  (``UnitTestTask(inputs_builder=gold_patch)``).

  Fills the **default** patch name; a task configured with a custom
  ``patch_name`` pairs with its own builder — the mismatch trips the
  only-declared-inputs check loudly, rather than applying nothing.

  Args:
    sb: Unused — the gold patch is the instance's, not the workspace's.
    instance: The instance whose reference solution is graded.

  Returns:
    The patch input, by store name.

  Raises:
    SandboxError: If the dataset carries no reference solution — asking to
      grade a gold patch that does not exist is a caller error, not an
      unresolved verdict.
  """
  del sb
  patch = instance.gold_patch()
  if patch is None:
    raise SandboxError(
        f"instance {instance.instance_id!r} carries no gold patch to grade"
    )
  return {PATCH_NAME: patch.encode("utf-8")}


@dataclass
class UnitTestTask[V: Verdict](Task):
  """Grade a patch against an instance's unit tests (ADR-0007).

  The evaluation as a task: the spec is compiled from the instance the
  execution binds, its script staged as ``entryscript.sh``, and the run graded
  by an ``UnitTestParseObserver`` carrying the dataset's grader directly — no
  vehicle in between (ADR-0007 §4).

  The patch, when this task applies one, is an **input** (``input_schema``):
  it arrives as a mounted ``patch.diff``, from a workflow edge, from a
  standalone caller's ``extra_mounts``, or from this task's own
  ``inputs_builder`` (``gold_patch`` for the reference self-check). One
  channel, so the schema is a fixed property of the task's configuration,
  never of where this particular run's data happens to come from.

  Attributes:
    apply_patch: Compile the script to apply the patch, and declare it as
      this task's required input. ``False`` grades the instance's tree
      untouched — the base-commit self-check.
    patch_name: Which workspace file the patch arrives as. Static
      configuration: ``input_schema`` declares it and the compiled script
      reads it, so the declaration and the script cannot drift apart. The
      default is the store name the rollout side produces, which is what makes
      the rollout → unit-test edge match by name.
    env: Extra environment for this task's own action — the compiled script.
      Distinct from the sandbox's ``env``, which every exec of the run gets;
      this is the grading script's alone. For a secret, use the sandbox's
      ``pass_env`` instead — that passes it by reference, so the value never
      reaches a command line.
  """

  apply_patch: bool = True
  patch_name: str = PATCH_NAME
  env: Mapping[str, str] | None = None

  def _compile(self, instance: TaskInstance[Any]) -> UnitTestSpec[V]:
    """Compile the bound instance's unit-test spec.

    No self-stash: each hook compiles from the instance it is handed.
    Compilation is pure and repeatable by contract (the fixes seam already
    promises that compiling twice yields the same thing twice) and cheap (the
    auxiliary files are disk-cached), so paying it twice per execution buys a
    genuinely stateless task — no lazy assignment, no hook-order coupling.

    Args:
      instance: The instance whose tests judge the patch.

    Returns:
      The compiled spec.
    """
    spec: UnitTestSpec[V] = instance.unit_test_spec(
        apply_patch=self.apply_patch, patch_name=self.patch_name
    )
    return spec

  @override
  def mounts(self, instance: TaskInstance[Any]) -> Mounts:
    """Stage the compiled spec's files plus the entryscript.

    The compiled spec is self-contained — its ``mounts`` carry everything the
    script reads, including any instance fix's files — so the
    instance-material default is deliberately not merged on top of it. The
    patch is not among them: it is this task's *input*, staged by whoever
    supplies it.

    Args:
      instance: The instance whose tests judge the patch.

    Returns:
      The staging set: the spec's mounts plus the executable entryscript.
    """
    spec = self._compile(instance)
    return merge_mounts(
        dict(spec.mounts),
        {
            ENTRYSCRIPT_NAME: Mount(
                Inline(spec.eval_script.encode()), executable=True
            )
        },
    )

  @override
  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    """Return the parse observer, carrying the dataset's grader directly.

    Args:
      instance: The instance whose grader judges the run.

    Returns:
      The one-element observer set.
    """
    spec = self._compile(instance)  # same spec, by the purity contract
    return (
        UnitTestParseObserver(spec.grader, native_outputs=spec.native_outputs),
    )

  @override
  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the patch input — fixed by configuration, not by data origin.

    Returns:
      The patch input in apply mode, else nothing.
    """
    if self.apply_patch:
      return (
          ArtifactSchema(
              self.patch_name, description="the candidate patch to grade"
          ),
      )
    return ()

  @override
  def outputs_valid(self, result: AttemptResult) -> bool:
    """Require a typed verdict on top of the baseline validity.

    Composes the baseline (status + required outputs) with the one thing
    existence cannot see: grading ran and produced a typed verdict. A
    dataset-specific subclass can compose further (e.g. reject a verdict
    whose parser output was unreadable).

    Args:
      result: The execution to judge.

    Returns:
      Whether the attempt produced a graded verdict.
    """
    return super().outputs_valid(result) and verdict_of(result) is not None

  @override
  def should_retry(self, result: AttemptResult) -> bool:
    """Retry on failure — and on an unresolved verdict, to absorb a flake.

    The flake half is what ADR-0005's in-run loop used to do, at the level
    that now owns retrying (ADR-0008): the patch is fixed, an unresolved
    verdict *might* be harness noise, and another attempt — in a fresh
    sandbox, separately persisted — answers that. Not failure: the terminal
    marker still reads ``outputs_valid``, so a genuinely failing patch that
    exhausts the budget is a *succeeded* task whose answer is "unresolved".

    Args:
      result: The execution to judge.

    Returns:
      Whether to spend budget on another attempt.
    """
    verdict = verdict_of(result)
    return super().should_retry(result) or (
        verdict is not None and not verdict.resolved
    )

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    """Run the entryscript once.

    Args:
      sb: The live sandbox to run in.
      instance: Unused — everything the script needs is already staged.
      timeout: Seconds before the run is killed.

    Returns:
      The entryscript's execution result.
    """
    del instance
    return sb.run_script(ENTRYSCRIPT_NAME, timeout=timeout, env=self.env)


def verdict_of(result: AttemptResult) -> Verdict | None:
  """Read the graded verdict back off an execution's own observers.

  The parse observer is composed by the task and travels on the result
  (``AttemptResult.observers``, composition order), which is what lets the
  validity hooks — and any caller — stay out of task state.

  Args:
    result: The execution to read.

  Returns:
    The verdict, or ``None`` when grading never ran (a setup failure), or when
    the result came from a task that composed no unit-test observer.
  """
  parse = next(
      (o for o in result.observers if isinstance(o, UnitTestParseObserver)),
      None,
  )
  return parse.verdict if parse is not None else None
