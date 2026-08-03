"""The task layer: one unit of work in one sandbox (ADR-0007).

A ``Task`` names the five-step shape both shipped compositions share — stage
mounts, compose observers, run one main action, let the observers extract,
assemble the result — and writes it exactly once (``execute``). Subclasses
supply the parts through three total hooks (``mounts`` / ``observers`` /
``action``) plus the ``input_schema`` declaration a workflow resolves; the
concrete tasks live with their domains (``swe_lab.rollout.CodingAgentTask``,
``swe_lab.evaluation.methods.unit_test.UnitTestEvalTask``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace
import time
from typing import Any, final, Protocol, runtime_checkable

from etils import epath

from swe_lab.datasets.instance import TaskInstance
from swe_lab.sandbox import (
    ArtifactSchema,
    ExecResult,
    merge_mounts,
    merge_output_schemas,
    Mounts,
    RunResult,
    RunStatus,
    Sandbox,
    SandboxError,
    SandboxFs,
    SandboxManager,
    SandboxObserver,
)


@final
class Upstream:
  """The construction-time mode marker for a workflow-supplied input.

  ``UPSTREAM`` (its only shipped instance) is what a task field takes when the
  value is not a constructor literal but an upstream task's output: the task
  declares the store name in ``input_schema()`` and stages no mount of its
  own — the workflow resolves the name against an earlier task's outputs and
  feeds the file through ``execute``'s ``extra_mounts``. Not a resolution
  placeholder: it carries no store path; matching is the workflow's job.
  """


UPSTREAM = Upstream()


@dataclass(frozen=True)
class TaskResult:
  """What one execution of a task yields.

  Attributes:
    run: The engine result (status, artifacts as host paths, metrics). A main
      action that timed out is promoted to ``RunStatus.TIMEOUT`` here — the
      engine cannot see it (a timeout does not raise, it comes back as a
      timed-out ``ExecResult``), and a killed run must not read as one that
      merely produced nothing.
    exec_result: The main action's own outcome; ``None`` if it never ran.
    output_schema: The task's merged schema — what this run was *supposed* to
      produce. The ADR-0007 §5 validation gate reads it against
      ``run.artifacts`` (a required name with no artifact fails the attempt);
      a workflow's edge mounting resolves upstream names through it.
    observers: Every composed observer, in composition order. The typed
      results live on them (an eval's verdict, an extractor's patch), and the
      wrappers read them back by type.
  """

  run: RunResult
  exec_result: ExecResult | None
  output_schema: tuple[ArtifactSchema, ...]
  observers: tuple[SandboxObserver, ...]


class Task(ABC):
  """One unit of work in one sandbox (ADR-0007 §1).

  Owns exactly what the manager does not: assembling the mounts, the
  observers, and the derived output schema. Lifecycle is mount → run →
  outputs (§2); subclasses supply the parts and ``execute`` runs the five
  steps once.

  A task is a **declaration** — instance, config, nothing a run dirties — and
  each ``execute`` call is one run: everything stateful is either built fresh
  inside it (the observers, the manager) or handed in fresh (the sandbox).
  That is why ``sandbox`` is an *argument*, not a field: a retry calls
  ``execute`` again on the same task with a fresh sandbox per attempt, and
  the caller owns every construction knob (backend, workspace, network) per
  the repo's inject-collaborators rule. Re-executable sequentially, not
  concurrently — a task may keep a per-run observer reference on itself for
  ``action``.

  **Three hooks, one channel each — and each hook is total.** ``mounts()`` is
  *all* of this task's mounts; ``observers()`` is *all* of its observers.
  There is no "the task's own" versus "gathered for you": a subclass
  overrides the hook and merges in whatever it uses — the instance's
  material, a harness's files, anything. ``execute`` takes the hooks' word
  for it, adds only what a task cannot know (the backend's observers, the
  caller's extras), and runs.

  Attributes:
    instance: The dataset instance this task is bound to (ADR-0007 §2),
      provided by the concrete subclass. Deliberately untyped in the verdict
      dimension: a task's outputs are whatever its observers declare — a
      verdict is one subclass's output, not part of this contract — so only
      a subclass that needs the typed view (the eval task's spec
      compilation) declares its own generic.
  """

  instance: TaskInstance[Any]

  def mounts(self) -> Mounts:
    """Return ALL files this task stages. Default: the instance's material.

    A subclass overrides and merges in whatever else it uses::

        return merge_mounts(super().mounts(), self.harness.mounts(...), ...)

    Returns:
      Target path → mount; what this returns **is** the staging set (plus the
      observers' own mounts, which the manager merges as everywhere else).
    """
    return dict(self.instance.mounts())

  def observers(self) -> Sequence[SandboxObserver]:
    """Return ALL of this task's observers. Default: none.

    One per thing the task extracts, plus a harness's own if it uses one.
    Fresh instances per call — observers are single-run, and so is each
    execution; a task that keeps a reference for ``action`` (an eval's retry
    loop drives its parse observer) stores it on itself.

    Returns:
      The observers, in composition order after the backend's own.
    """
    return ()

  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the upstream artifacts this task consumes, by store name.

    Default: none. A workflow resolves each name against earlier tasks'
    outputs, mounts it read-only via ``execute``'s ``extra_mounts``, and a
    required name with no artifact is the *distinct* edge failure of ADR-0007
    §5 — the task never reaches into the store itself.

    Returns:
      The inputs, empty for a task that consumes nothing.
    """
    return ()

  @abstractmethod
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    """Run the main action: exec the harness, or run the entryscript.

    Args:
      sb: The live sandbox to run in.
      timeout: Seconds before the action is killed.

    Returns:
      The main action's outcome; a task that execs more than once returns the
      result of the main action.
    """
    ...

  @final
  def execute(
      self,
      sandbox: Sandbox,
      *,
      output_dir: epath.PathLike,
      timeout: float,
      extra_mounts: Mounts | None = None,
      extra_observers: Sequence[SandboxObserver] = (),
  ) -> TaskResult:
    """Run the five steps once against a fresh sandbox.

    The hooks are total, so this adds only what the task cannot know: the
    backend's own observers (composed first — they measure the whole run,
    ADR-0007 §3) and the caller's extras (composed last, so they see the run
    post-processed). A run failure is *recorded* in ``TaskResult.run`` rather
    than raised — the caller gates on ``run.status`` — while an assembly
    error (a ``SandboxError`` from two contributors claiming one mount
    target or one output name) propagates from the merge, before anything
    runs.

    Args:
      sandbox: The built, not-yet-up sandbox to run in.
      output_dir: The host directory collected artifacts are fetched into.
      timeout: Seconds before the main action is killed.
      extra_mounts: The caller channel mirroring ``extra_observers`` — a
        workflow feeds resolved inputs through it; duplicate targets are
        refused like everywhere else.
      extra_observers: Extra observers composed after the task's own (e.g. a
        persist observer).

    Returns:
      The task result: engine run, action outcome, derived schema, observers.
    """
    observers = [*sandbox.observers(), *self.observers(), *extra_observers]
    # The task's output schema is derived — and a duplicate store name across
    # observers fails HERE, at assembly, like a duplicate mount target.
    schema = merge_output_schemas(*(o.output_schema() for o in observers))
    manager = SandboxManager(
        sandbox=sandbox,
        output_dir=epath.Path(output_dir),
        observers=observers,
        mounts=merge_mounts(self.mounts(), extra_mounts or {}),
    )
    exec_result: ExecResult | None = None
    try:
      with manager.session() as sb:
        started = time.monotonic()
        try:
          exec_result = self.action(sb, timeout=timeout)
        finally:
          _hand_exec_outcome(observers, exec_result, time.monotonic() - started)
    except SandboxError:
      pass  # recorded in manager.result — the caller gates on run.status
    return TaskResult(
        run=_promote_timeout(manager.result, exec_result),
        exec_result=exec_result,
        output_schema=schema,
        observers=tuple(observers),
    )


@runtime_checkable
class _ExecOutcomeCarrier(Protocol):
  """The structural shape of an observer that reports the action's outcome.

  A Protocol (not an ABC) per ADR-0002: this is a data shape, not behavior —
  a harness-outcome collector and an eval parser both happen to carry these
  two fields, and the handoff matches them structurally rather than making
  them share a base class.

  Attributes:
    exec_result: The main action's outcome, for ``before_destroy`` to report.
    wall_seconds: How long the action ran.
  """

  exec_result: ExecResult | None
  wall_seconds: float | None


def _hand_exec_outcome(
    observers: Sequence[SandboxObserver],
    exec_result: ExecResult | None,
    wall_seconds: float,
) -> None:
  """Hand the action's outcome to every observer that carries the fields.

  The handoff both compositions did by hand, generalized: an observer with an
  ``exec_result`` / ``wall_seconds`` field (a harness-outcome collector, an
  eval parser) gets them before teardown, so ``before_destroy`` can report
  how the action ended. A field the action's own accounting already set is
  left alone — an eval retry loop hands its parser each attempt's result and
  the wall time *it* means (script cost, excluding grading), and clobbering
  that with the whole action's duration would quietly change the metric.

  Args:
    observers: The run's composed observers.
    exec_result: The main action's outcome; ``None`` if it never returned.
    wall_seconds: How long the action ran, including a raise.
  """
  for observer in observers:
    if not isinstance(observer, _ExecOutcomeCarrier):
      continue
    if observer.exec_result is None:
      observer.exec_result = exec_result
    if observer.wall_seconds is None:
      observer.wall_seconds = wall_seconds


def _promote_timeout(
    result: RunResult, exec_result: ExecResult | None
) -> RunResult:
  """Promote a timed-out main action to ``RunStatus.TIMEOUT``.

  The engine cannot see this itself: a timeout does not raise, it comes back
  as a timed-out ``ExecResult``, so the manager assembles ``SUCCESS``. Only
  the task knows better — and a killed action is a *budget* signal, not the
  infra failure a bare status would suggest.

  Args:
    result: The engine's assembled result.
    exec_result: The main action's outcome, if it ran.

  Returns:
    ``result`` unchanged, or a copy with ``TIMEOUT`` when the action was
    killed.
  """
  if exec_result is not None and exec_result.timed_out:
    return replace(result, status=RunStatus.TIMEOUT)
  return result
