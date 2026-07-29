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

from collections.abc import Mapping
import contextlib
from dataclasses import dataclass

from etils import epath

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses import (
    Harness,
    HarnessOutcomeObserver,
    PROMPT_NAME,
)
from swe_lab.sandbox import (
    Inline,
    Mount,
    RunStatus,
    Sandbox,
    SandboxManager,
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
    prompt: The dataset-derived solve prompt (staged under ``PROMPT_NAME``).
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

  Returns:
    The rollout outcome (patch, flags, conversation, status).
  """
  spec = sandbox.spec
  conversation = ConversationObserver(producer=harness)
  extract = DiffExtractObserver(exclude_globs=exclude_globs)
  outcome = HarnessOutcomeObserver(harness=harness)
  # The prompt is dataset-derived, staged by the composition; the harness
  # contributes its own script and any read-only asset (e.g. a pinned binary).
  mounts = {PROMPT_NAME: Mount(Inline(prompt.encode()))} | harness.mounts(
      spec.workdir
  )
  manager = SandboxManager(
      sandbox=sandbox,
      output_dir=epath.Path(output_dir),
      observers=[conversation, extract, outcome],
      mounts=mounts,
  )
  with proxy or contextlib.nullcontext(), manager.session() as sb:
    harness.run(sb, timeout=timeout, env=agent_env)

  return RolloutOutcome(
      instance_id=spec.instance_id,
      patch=extract.patch,
      is_empty=extract.is_empty,
      binary_stripped=extract.binary_stripped,
      complete=outcome.complete,
      conversation=conversation.conversation or Conversation(messages=[]),
      status=manager.result.status,
      workspace=epath.Path(output_dir),
      artifacts=manager.result.artifacts,
      metrics=manager.result.metrics,
  )
