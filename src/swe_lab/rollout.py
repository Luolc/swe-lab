"""The rollout composition: one agent run → a graded-ready patch + trace.

``CodingAgentTask`` is the composition (ADR-0007): a harness solves the bound
instance, the shared observers — the harness's own pair plus diff-extract —
watch the run, and an optional proxy records the agent's API traffic around
the main action. Backend-, dataset- **and harness-agnostic**: nothing here
imports a concrete agent, so a downstream user's own ``Harness`` and internal
proxy compose unchanged. ``run_rollout`` remains the frozen-signature wrapper
for callers that hand a sandbox + prompt rather than a dataset instance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import contextlib
from dataclasses import dataclass
from typing import Any, final, override

from etils import epath

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.harnesses import Harness, HarnessOutcomeObserver
from swe_lab.sandbox import (
    ExecResult,
    merge_mounts,
    Mounts,
    RunStatus,
    Sandbox,
    SandboxError,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import DiffExtractObserver
from swe_lab.workflow import Task


@dataclass(frozen=True)
class RolloutOutcome:
  """The result of one rollout — patch + trace + engine status.

  Attributes:
    instance_id: The instance solved.
    patch: The clean, text-only patch (may be ``""``).
    is_empty: Whether the patch is effectively empty (never grades as a pass).
    binary_stripped: Whether a residual binary hunk was stripped host-side.
    complete: Whether the agent finished cleanly (from its event stream).
    conversation: The canonical typed trace.
    status: The engine-level run status.
    workspace: The run's workspace directory.
    artifacts: Collected artifacts (canonical name → host path) — the persist
      input.
    metrics: Scalar run metrics.
  """

  instance_id: str
  patch: str
  is_empty: bool
  binary_stripped: bool
  complete: bool
  conversation: Conversation
  status: RunStatus
  workspace: epath.Path
  artifacts: dict[str, epath.Path]
  metrics: dict[str, float]


@dataclass
class CodingAgentTask(Task):
  """An agent solves the bound instance; outputs a patch and a trace.

  The rollout composition as a task: the harness is a field, its mounts and
  observers folded into the task's total hooks, its ``run`` the main action.
  Every collaborator is **injected already built** — the caller owns their
  construction (the agent's own constructor carries its model / capture /
  auth mode; a recorder for the proxy) — so a new construction option never
  ripples through this class, and a downstream user's own ``Harness`` and
  internal proxy compose here unchanged.

  Attributes:
    instance: The dataset instance to solve.
    harness: The agent to run. It supplies its own mounts, observers, the
      main action, the trace conversion, and the completion signal.
    prompt: The task prompt handed to ``harness.run`` as text; ``None`` asks
      the instance (``instance.prompt()``).
    exclude_globs: Build-noise denylist for the diff extraction.
    agent_env: Extra environment for the agent process, handed to the
      harness. For a secret, use the sandbox's ``pass_env`` instead — that
      passes it by reference, so the value never reaches a command line.
    proxy: A recorder held open around the main action (e.g. a host-side
      reverse proxy capturing the agent's API traffic). Any context manager
      will do; ``None`` means record nothing. Single-use, like the task's
      execution: a re-executed task needs a fresh one.
  """

  instance: TaskInstance[Any]
  harness: Harness
  prompt: str | None = None
  exclude_globs: tuple[str, ...] = ()
  agent_env: Mapping[str, str] | None = None
  proxy: contextlib.AbstractContextManager[object] | None = None

  @override
  def mounts(self) -> Mounts:
    """Stage the instance's material and the harness's own files.

    Returns:
      The merged staging set (duplicate targets refused).
    """
    return merge_mounts(
        super().mounts(),
        self.harness.mounts(self.instance.sandbox_spec().workdir),
    )

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Return the harness's own observers plus the deliverable's extractor.

    Returns:
      The harness's pair (or whatever it chooses) followed by a fresh
      ``DiffExtractObserver`` — the patch belongs to the *task* (ADR-0007
      §3), or the same harness could never run a task producing something
      other than a diff.
    """
    return (
        *self.harness.observers(),
        DiffExtractObserver(exclude_globs=self.exclude_globs),
    )

  @override
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    """Run the agent against the prompt, inside the recording proxy.

    The proxy's lifetime is the agent's — opened around the run and closed
    before ``before_destroy`` reads the log, so the recording is flushed by
    the time conversion happens.

    Args:
      sb: The live sandbox to run in.
      timeout: Seconds before the agent run is killed.

    Returns:
      The agent execution's outcome (a timeout comes back as a timed-out
      ``ExecResult``, not a raise).
    """
    prompt = self.prompt if self.prompt is not None else self.instance.prompt()
    with self.proxy or contextlib.nullcontext():
      return self.harness.run(
          sb, prompt=prompt, timeout=timeout, env=self.agent_env
      )


@final
class _SpecOnlyInstance(TaskInstance[Verdict]):
  """The wrapper path's instance: only the run context is known.

  ``run_rollout`` receives a built sandbox and the prompt as text, not a
  dataset record, so this adapter serves the sandbox's own spec back and
  answers nothing else — the wrapper always passes the prompt explicitly.
  """

  def __init__(self, spec: SandboxSpec) -> None:
    """Wrap the sandbox's run context.

    Args:
      spec: The run context (supplies the instance id and workdir).
    """
    self._spec = spec
    self.instance_id = spec.instance_id

  @override
  def sandbox_spec(self) -> SandboxSpec:
    """Return the run context the caller's sandbox already carries."""
    return self._spec

  @override
  def prompt(self) -> str:
    """Unavailable — the wrapper hands the prompt to the task directly.

    Returns:
      Never — this method only raises.

    Raises:
      NotImplementedError: Always; the wrapper path has no dataset record.
    """
    raise NotImplementedError("run_rollout receives the prompt directly")

  @override
  def gold_patch(self) -> str | None:
    """Unavailable — the wrapper path has no dataset record.

    Returns:
      Never — this method only raises.

    Raises:
      NotImplementedError: Always.
    """
    raise NotImplementedError("a bare run context carries no gold patch")

  @override
  def unit_test_spec(
      self,
      *,
      patch: str | None,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[Verdict]:
    """Unavailable — the wrapper path has no dataset record.

    Args:
      patch: Unused.
      checkout_golden_tests: Unused.

    Returns:
      Never — this method only raises.

    Raises:
      NotImplementedError: Always.
    """
    raise NotImplementedError("a bare run context compiles no eval spec")


def run_rollout(
    sandbox: Sandbox,
    harness: Harness,
    *,
    prompt: str,
    output_dir: epath.PathLike,
    timeout: float,
    proxy: contextlib.AbstractContextManager[object] | None = None,
    agent_env: Mapping[str, str] | None = None,
    exclude_globs: tuple[str, ...] = (),
    observers: Sequence[SandboxObserver] = (),
) -> RolloutOutcome:
  """Run one agent rollout and extract its patch + trace.

  A thin wrapper (frozen signature) over ``CodingAgentTask``: the sandbox's
  spec is adapted into the task's instance seam and the result reshaped into
  ``RolloutOutcome`` — construction and reshaping, no logic of its own.
  Kept for backward compatibility; running the task inside a workflow
  (ADR-0007) is the intended entry point once workflows land, and this
  wrapper is then slated for deprecation.

  Args:
    sandbox: The built (not-yet-up) sandbox to run in; its ``spec`` carries
      the run context (image / workdir / base_commit / instance_id).
    harness: The agent to run.
    prompt: The dataset-derived task prompt, handed to ``harness.run`` as
      text; the harness lands it wherever it wants it (ADR-0007 §8).
    output_dir: The host-side output directory (created fresh). For a host
      backend it is also the sandbox's bind-mounted workspace, so a host-side
      proxy log lands where the in-sandbox harness reads it.
    timeout: Seconds before the agent run is killed.
    proxy: A recorder held open around the agent run; ``None`` records
      nothing.
    agent_env: Extra environment for the agent process (see
      ``CodingAgentTask``).
    exclude_globs: Build-noise denylist for the diff extraction.
    observers: Extra observers, composed **after** the task's own so they see
      the run once it has post-processed (e.g. a persist observer).

  Returns:
    The rollout outcome (patch, flags, conversation, status).

  Raises:
    SandboxError: If setup fails (bad mounts, or the sandbox failing to come
      up) — this wrapper's historical contract, preserved on top of the task
      layer, which records the failure instead of raising.
  """
  spec = sandbox.spec
  task = CodingAgentTask(
      instance=_SpecOnlyInstance(spec),
      harness=harness,
      prompt=prompt,
      exclude_globs=exclude_globs,
      agent_env=agent_env,
      proxy=proxy,
  )
  result = task.execute(
      sandbox,
      output_dir=output_dir,
      timeout=timeout,
      extra_observers=observers,
  )
  error = result.run.error
  if result.run.status is RunStatus.SETUP_ERROR and isinstance(
      error, SandboxError
  ):
    # The historical contract (see the docstring): re-raised with the same
    # type and message, the recorded original chained as the cause.
    raise SandboxError(str(error)) from error
  # The typed results live on the composed observers (TaskResult.observers,
  # composition order); the harness's own come first, so a first-match read
  # finds them even if a caller's extra observer shares a type.
  conversation = next(
      (o for o in result.observers if isinstance(o, ConversationObserver)),
      None,
  )
  outcome = next(
      (o for o in result.observers if isinstance(o, HarnessOutcomeObserver)),
      None,
  )
  extract = next(
      o for o in result.observers if isinstance(o, DiffExtractObserver)
  )
  return RolloutOutcome(
      instance_id=spec.instance_id,
      patch=extract.patch,
      is_empty=extract.is_empty,
      binary_stripped=extract.binary_stripped,
      complete=outcome.complete if outcome is not None else False,
      conversation=(
          conversation.conversation if conversation is not None else None
      )
      or Conversation(messages=[]),
      status=result.run.status,
      workspace=epath.Path(output_dir),
      artifacts=result.run.artifacts,
      metrics=result.run.metrics,
  )
