"""The rollout composition: one agent run → a graded-ready patch + trace.

``run_rollout`` composes a harness with the shared observers — conversation,
diff-extract, and harness-outcome — over the sandbox engine. Backend-, dataset-
**and harness-agnostic**: the caller builds the sandbox, the harness, and
(optionally) a trace-recording proxy, then passes them in with the
dataset-derived prompt and a host output directory. Nothing here imports a
concrete agent, so a downstream user's own ``Harness`` and their own internal
proxy compose unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import contextlib
from dataclasses import dataclass
import time

from etils import epath

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses import Harness, HarnessOutcomeObserver
from swe_lab.sandbox import (
    ExecResult,
    RunStatus,
    Sandbox,
    SandboxManager,
    SandboxObserver,
)
from swe_lab.sandbox.observers import DiffExtractObserver


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


def _status(status: RunStatus, exec_result: ExecResult | None) -> RunStatus:
  """Promote a timed-out agent run to ``RunStatus.TIMEOUT``.

  The engine cannot see this itself: a timeout does not raise, it comes back as
  a timed-out ``ExecResult``, so the manager assembles ``SUCCESS``. Only the
  composition knows better — and a killed agent is a *budget* signal, not the
  infra failure a bare RUN_ERROR would suggest.

  Args:
    status: The engine's assembled status.
    exec_result: The agent execution's outcome, if it ran.

  Returns:
    ``status`` unchanged, or ``TIMEOUT`` when the agent was killed.
  """
  if exec_result is not None and exec_result.timed_out:
    return RunStatus.TIMEOUT
  return status


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

  Every collaborator is **injected already built** — the caller owns their
  construction (``build_sandbox(...)`` for the sandbox; the agent's own
  constructor for the harness, which carries its model / capture / auth mode;
  a recorder for the proxy). So a new construction option never ripples through
  this signature, a test passes fakes, and a downstream user's own ``Harness``
  and internal proxy compose here unchanged.

  Args:
    sandbox: The built (not-yet-up) sandbox to run in; its ``spec`` carries the
      run context (image / workdir / base_commit / instance_id).
    harness: The agent to run. It supplies its own mounts, the main action, the
      trace → ``Conversation`` conversion, and the completion signal.
    prompt: The dataset-derived task prompt, handed to ``harness.run`` as
      text; the harness lands it wherever it wants it (ADR-0007 §8).
    output_dir: The manager's host-side output directory (created fresh). For a
      host backend it is also the sandbox's bind-mounted workspace, so a
      host-side proxy log lands where the in-sandbox harness reads it.
    timeout: Seconds before the agent run is killed.
    proxy: A recorder held open around the run (e.g. a host-side reverse proxy
      capturing the agent's API traffic). Any context manager will do; ``None``
      means record nothing.
    agent_env: Extra environment for the agent process, handed to the harness.
      For a secret, use the sandbox's ``pass_env`` instead — that passes it by
      reference, so the value never reaches a command line or a staged file.
    exclude_globs: Build-noise denylist for the diff extraction.
    observers: Extra observers, composed **after** this composition's own
      (conversation / diff-extract / harness-outcome) so they see the run once
      it has post-processed — e.g. a persist observer, or a metrics collector
      that wants the extracted patch.

  Returns:
    The rollout outcome (patch, flags, conversation, status).
  """
  spec = sandbox.spec
  # The runner's observers come from its own factory (ADR-0007 §3); the
  # generic pair is looked back up for outcome assembly. Wrapper-level
  # `isinstance` on purpose — Task 19's `TaskResult` replaces this.
  runner_observers = tuple(harness.observers())
  conversation = next(
      (o for o in runner_observers if isinstance(o, ConversationObserver)),
      None,
  )
  outcome = next(
      (o for o in runner_observers if isinstance(o, HarnessOutcomeObserver)),
      None,
  )
  extract = DiffExtractObserver(exclude_globs=exclude_globs)
  manager = SandboxManager(
      sandbox=sandbox,
      output_dir=epath.Path(output_dir),
      # Backend observers first: they measure the whole run (ADR-0007 §3).
      observers=[*sandbox.observers(), *runner_observers, extract, *observers],
      # The prompt is no longer a composition mount — it goes to `run` as
      # text, and the harness lands it itself (ADR-0007 §8).
      mounts=harness.mounts(spec.workdir),
  )
  exec_result: ExecResult | None = None
  with proxy or contextlib.nullcontext(), manager.session() as sb:
    # Hand the execution's own outcome to the observer *before* teardown, so
    # before_destroy can report it. Discarding it left a killed agent
    # indistinguishable from one that simply produced no trace.
    started = time.monotonic()
    try:
      exec_result = harness.run(
          sb, prompt=prompt, timeout=timeout, env=agent_env
      )
    finally:
      if outcome is not None:
        outcome.exec_result = exec_result
        outcome.wall_seconds = time.monotonic() - started

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
      status=_status(manager.result.status, exec_result),
      workspace=epath.Path(output_dir),
      artifacts=manager.result.artifacts,
      metrics=manager.result.metrics,
  )
