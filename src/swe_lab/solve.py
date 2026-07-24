"""The rollout composition: one agent run → a graded-ready patch + trace.

``run_rollout`` composes a harness + the shared conversation observer + the
shared diff-extract observer over the sandbox engine (spec §The three axes).
Backend-agnostic and dataset-agnostic: the caller passes the run context
(``SandboxSpec``), the dataset-derived prompt, and a backend.

Named ``solve`` (not ``rollout``) only because the legacy ``rollout/`` package
still exists; it moves to ``rollout.py`` at cutover (10b). (task 07)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses.claude_code import (
    ClaudeCodeHarness,
    event_stream_complete,
)
from swe_lab.harnesses.claude_code.constants import (
    EVENT_STREAM_NAME,
    PROMPT_NAME,
)
from swe_lab.sandbox import (
    Inline,
    Mount,
    RunStatus,
    SandboxBackend,
    SandboxManager,
    SandboxSpec,
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
  """

  instance_id: str
  patch: str
  is_empty: bool
  binary_stripped: bool
  complete: bool
  conversation: Conversation
  status: RunStatus
  workspace: Path


def run_rollout(
    spec: SandboxSpec,
    *,
    prompt: str,
    model: str,
    backend: SandboxBackend,
    workspace: Path,
    timeout: float,
    exclude_globs: tuple[str, ...] = (),
) -> RolloutOutcome:
  """Run one agent rollout and extract its patch + trace.

  Args:
    spec: The run context (image / workdir / base_commit / instance_id).
    prompt: The dataset-derived solve prompt (staged as ``prompt.txt``).
    model: The ``--model`` alias for the agent.
    backend: The sandbox backend (its binary asset is wired in here).
    workspace: The run's workspace directory (created fresh).
    timeout: Seconds before the agent run is killed.
    exclude_globs: Build-noise denylist for the diff extraction.

  Returns:
    The rollout outcome (patch, flags, conversation, status).
  """
  harness = ClaudeCodeHarness(model=model)
  conversation = ConversationObserver(producer=harness)
  extract = DiffExtractObserver(exclude_globs=exclude_globs)
  backend = backend.with_assets(harness.assets())  # the binary at /opt
  # prompt.txt is dataset-derived (task 06 §5.6), staged by the composition
  mounts = {PROMPT_NAME: Mount(Inline(prompt.encode()))} | harness.mounts(
      spec.workdir
  )

  manager = SandboxManager(
      spec=spec,
      backend=backend,
      workspace=workspace,
      observers=[conversation, extract],
      mounts=mounts,
  )
  with manager.sandbox() as sb:
    harness.run(sb, timeout=timeout)

  return RolloutOutcome(
      instance_id=spec.instance_id,
      patch=extract.patch,
      is_empty=extract.is_empty,
      binary_stripped=extract.binary_stripped,
      complete=event_stream_complete(workspace / EVENT_STREAM_NAME),
      conversation=conversation.conversation or Conversation(messages=[]),
      status=manager.result.status,
      workspace=workspace,
  )
