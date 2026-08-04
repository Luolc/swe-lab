"""The task layer: one unit of work in one sandbox (ADR-0007).

A ``Task`` names the five-step shape both shipped compositions share — stage
mounts, compose observers, run one main action, let the observers extract,
assemble the result — and writes it exactly once (``execute``). Subclasses
supply the parts through three total hooks (``mounts`` / ``observers`` /
``action``) plus the ``input_schema`` declaration a workflow resolves; the
concrete tasks live with their domains (``swe_lab.rollout.CodingAgentTask``,
``swe_lab.evaluation.unit_test.UnitTestTask``).

The instance is **late-bound**: a task is configuration only, and the one
instance arrives at ``execute``, from where every hook receives it. That is
what lets a workflow definition be written statically, once, and invoked
against any instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
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

type InputsBuilder = Callable[
    [SandboxFs, TaskInstance[Any]], Mapping[str, bytes]
]
"""Generates a task's declared inputs from the instance and the live session.

The standalone-mode supplier of what an edge would otherwise carry (the task's
prompt, a gold patch). It runs **inside** the session, so it can compose from
the workspace as well as the instance, and may only fill names the task
declares — see ``Task.execute``.
"""


@dataclass(frozen=True)
class AttemptResult:
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


@dataclass(kw_only=True)
class Task(ABC):
  """One unit of work in one sandbox (ADR-0007 §1).

  Owns exactly what the manager does not: assembling the mounts, the
  observers, and the derived output schema. Lifecycle is mount → run →
  outputs (§2); subclasses supply the parts and ``execute`` runs the five
  steps once.

  A task is a **declaration** — configuration, nothing a run dirties — and
  each ``execute`` call is one run against one ``(sandbox, instance)`` pair:
  everything stateful is either built fresh inside it (the observers, the
  manager) or handed in fresh (the sandbox, the instance). That is why both
  are *arguments*, not fields: a retry calls ``execute`` again on the same
  task with a fresh sandbox per attempt, the caller owns every construction
  knob (backend, workspace, network) per the repo's inject-collaborators
  rule, and one statically-written declaration runs against any instance.
  Re-executable sequentially, not concurrently — a task may keep a per-run
  observer reference on itself for ``action``.

  **Three hooks, one channel each — and each hook is total.** ``mounts()`` is
  *all* of this task's mounts; ``observers()`` is *all* of its observers.
  There is no "the task's own" versus "gathered for you": a subclass
  overrides the hook and merges in whatever it uses — the instance's
  material, a harness's files, anything. ``execute`` takes the hooks' word
  for it, adds only what a task cannot know (the backend's observers, the
  caller's extras), and runs.

  A dataclass so the base field below is genuinely inherited rather than
  redeclared by every subclass; ``kw_only`` exempts it from the positional
  ordering rule, so a subclass keeps its own required positional fields.

  Attributes:
    inputs_builder: Generates this task's declared inputs inside the live
      session (the standalone mode), or ``None`` when something else supplies
      them — a workflow edge, or the caller's own bytes. Same task, both
      modes, no special-casing; ``execute`` consumes it uniformly.
  """

  inputs_builder: InputsBuilder | None = None

  def mounts(self, instance: TaskInstance[Any]) -> Mounts:
    """Return ALL files this task stages. Default: the instance's material.

    A subclass overrides and merges in whatever else it uses::

        return merge_mounts(
            super().mounts(instance), self.harness.mounts(...), ...
        )

    Args:
      instance: The instance this execution is bound to.

    Returns:
      Target path → mount; what this returns **is** the staging set (plus the
      observers' own mounts, which the manager merges as everywhere else).
    """
    return dict(instance.mounts())

  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    """Return ALL of this task's observers. Default: none.

    One per thing the task extracts, plus a harness's own if it uses one.
    Fresh instances per call — an observer is single-run, and so is each
    execution. The typed results are read back off ``AttemptResult.observers``
    afterwards (that is what it is for), so nothing needs a reference kept on
    the task.

    Args:
      instance: The instance this execution is bound to.

    Returns:
      The observers, in composition order after the backend's own.
    """
    del instance
    return ()

  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the artifacts this task consumes, by store name.

    Default: none. **The schema is static** — a fixed property of the task's
    configuration, never of where one run's data happens to come from — and
    every input lands in the workspace the same way: staged as a mount
    through ``execute``'s ``extra_mounts`` (a workflow edge, or the caller's
    own bytes), or written in-session by this task's ``inputs_builder``.
    The task itself never reaches into the store. A required name with no
    artifact behind it is the *distinct* edge failure of ADR-0007 §5.

    Returns:
      The inputs, empty for a task that consumes nothing.
    """
    return ()

  @abstractmethod
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    """Run the main action: exec the harness, or run the entryscript.

    Args:
      sb: The live sandbox to run in.
      instance: The instance this execution is bound to.
      timeout: Seconds before the action is killed.

    Returns:
      The main action's outcome; a task that execs more than once returns the
      result of the main action.
    """
    ...

  def outputs_valid(self, result: AttemptResult) -> bool:
    """Judge whether an execution produced good outputs — the failure call.

    The terminal marker of a task-level run keys off this (ADR-0007 §§6–7):
    the final attempt's validity decides ``succeeded`` versus ``failed``.
    Default: the run ended ``SUCCESS`` and every ``required`` declared output
    exists. Existence alone cannot judge *content* — a ``conversation.json``
    that is present but does not parse is a failed attempt this baseline
    waves through — so a subclass overrides to inspect the artifacts and the
    observers' typed results, composing with ``super().outputs_valid(...)``.

    Deliberately about outputs, not answers: an eval whose verdict is
    *unresolved* has still done its job — "no" is an answer, not a failure.

    Args:
      result: The execution to judge.

    Returns:
      Whether the attempt counts as having produced its outputs.
    """
    if result.run.status is not RunStatus.SUCCESS:
      return False  # TIMEOUT / RUN_ERROR / SETUP_ERROR
    produced = result.run.artifacts
    return all(
        schema.name in produced
        for schema in result.output_schema
        if schema.required
    )

  def should_retry(self, result: AttemptResult) -> bool:
    """Decide whether this attempt needs another one (task-level retry).

    Default: exactly when the attempt failed (``outputs_valid`` is false) —
    an invalid attempt is a retryable failure, and infrastructure failures
    land there too. A subclass overrides to *add* retry-desire on top, never
    to weaken the failure half — eval's flake absorption retries an
    unresolved-but-valid verdict::

        def should_retry(self, result):
          return super().should_retry(result) or not resolved

    Retry-desire is not failure: the terminal marker reads
    :meth:`outputs_valid`, never this.

    Args:
      result: The execution to judge.

    Returns:
      Whether the runner should spend budget on another attempt.
    """
    return not self.outputs_valid(result)

  @final
  def execute(
      self,
      sandbox: Sandbox,
      instance: TaskInstance[Any],
      *,
      output_dir: epath.PathLike,
      timeout: float,
      extra_mounts: Mounts | None = None,
      extra_observers: Sequence[SandboxObserver] = (),
  ) -> AttemptResult:
    """Run the five steps once against a fresh sandbox and one instance.

    The hooks are total, so this adds only what the task cannot know: the
    backend's own observers (composed first — they measure the whole run,
    ADR-0007 §3) and the caller's extras (composed last, so they see the run
    post-processed). A run failure is *recorded* in ``AttemptResult.run`` rather
    than raised — the caller gates on ``run.status`` — while an assembly
    error (a ``SandboxError``: two contributors claiming one mount target or
    one output name, or a required input nobody staged) raises before
    anything runs.

    Args:
      sandbox: The built, not-yet-up sandbox to run in.
      instance: The instance to run against — handed to every hook.
      output_dir: The host directory collected artifacts are fetched into.
      timeout: Seconds before the main action is killed.
      extra_mounts: The caller channel mirroring ``extra_observers`` — a
        workflow feeds resolved inputs through it; duplicate targets are
        refused like everywhere else.
      extra_observers: Extra observers composed after the task's own (e.g. a
        persist observer).

    Returns:
      The task result: engine run, action outcome, derived schema, observers.

    Raises:
      SandboxError: If assembly fails — a required input nobody staged (only
        checkable here when no ``inputs_builder`` runs; see
        :meth:`_build_inputs`) — or (from the merges) two contributors
        claiming one mount target or one output name.
    """
    staged: Mounts = dict(extra_mounts or {})
    if self.inputs_builder is None:
      # Every input arrives by mount, so a required one that nobody staged is
      # detectable HERE — an assembly error like a duplicate mount target,
      # not a mid-script mystery inside the sandbox.
      missing = _missing_required(self.input_schema(), staged.__contains__)
      if missing:
        raise SandboxError(
            f"required input(s) not mounted: {missing}; supply them via"
            " extra_mounts (a workflow edge, or the caller's own bytes)"
        )
    observers = [
        *sandbox.observers(),
        *self.observers(instance),
        *extra_observers,
    ]
    # The task's output schema is derived — and a duplicate store name across
    # observers fails HERE, at assembly, like a duplicate mount target.
    schema = merge_output_schemas(*(o.output_schema() for o in observers))
    manager = SandboxManager(
        sandbox=sandbox,
        output_dir=epath.Path(output_dir),
        observers=observers,
        mounts=merge_mounts(self.mounts(instance), staged),
    )
    exec_result: ExecResult | None = None
    try:
      with manager.session() as sb:
        self._build_inputs(sb, instance, staged=staged)
        started = time.monotonic()
        try:
          exec_result = self.action(sb, instance, timeout=timeout)
        finally:
          _hand_exec_outcome(observers, exec_result, time.monotonic() - started)
    except SandboxError:
      pass  # recorded in manager.result — the caller gates on run.status
    return AttemptResult(
        run=_promote_timeout(manager.result, exec_result),
        exec_result=exec_result,
        output_schema=schema,
        observers=tuple(observers),
    )

  def _build_inputs(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, staged: Mounts
  ) -> None:
    """Run the inputs builder, land its bytes, and re-check requiredness.

    The builder needs the live sandbox (it may compose from the workspace as
    well as the instance), so its files land here — after the session
    started, before the action — and the checks land with them: it may only
    fill *declared* inputs, and colliding with a name something else already
    staged is a bug, not a silent overwrite. Requiredness is verified before
    the action either way; with a builder present that verdict can only be
    reached here, so a misconfiguration costs one container startup and says
    exactly what was missing.

    Args:
      sb: The live sandbox to write into.
      instance: The instance this execution is bound to.
      staged: The mounts already staged for this run (the edge's, the
        caller's).

    Raises:
      SandboxError: If the builder fills an undeclared input, collides with a
        staged one, or leaves a required input absent from the workspace.
    """
    if self.inputs_builder is None:
      return
    generated = self.inputs_builder(sb, instance)
    declared = {schema.name for schema in self.input_schema()}
    undeclared = sorted(generated.keys() - declared)
    if undeclared:
      raise SandboxError(
          f"the inputs builder produced undeclared input(s): {undeclared};"
          f" this task declares {sorted(declared)}"
      )
    collisions = sorted(generated.keys() & staged.keys())
    if collisions:
      raise SandboxError(
          f"the inputs builder would overwrite staged input(s):"
          f" {collisions}; a task supplied from outside (a workflow edge, a"
          " caller's bytes) must not also build them — set"
          " inputs_builder=None"
      )
    for name, data in generated.items():
      sb.write(name, data)
    missing = _missing_required(self.input_schema(), sb.exists)
    if missing:
      raise SandboxError(
          f"required input(s) missing after the inputs builder ran:"
          f" {missing}"
      )


def _missing_required(
    schemas: Sequence[ArtifactSchema], is_present: Callable[[str], bool]
) -> list[str]:
  """Return the required input names ``is_present`` does not vouch for.

  Args:
    schemas: The task's declared inputs.
    is_present: Answers whether one input is there — membership in the staged
      mounts before the session, existence in the workspace inside it.

  Returns:
    The missing required names, in declaration order.
  """
  return [
      schema.name
      for schema in schemas
      if schema.required and not is_present(schema.name)
  ]


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
  left alone: an action that measures something narrower than "how long
  ``action`` took" — the cost of one exec, excluding what the task does around
  it — has the last word, and clobbering it would quietly change the metric.

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
