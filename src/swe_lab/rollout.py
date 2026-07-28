"""The rollout composition: one agent run → a graded-ready patch + trace.

``run_rollout`` composes a harness + the shared conversation observer + the
shared diff-extract observer over the sandbox engine. Backend-agnostic and
dataset-agnostic: the caller passes the run context (``SandboxSpec``), the
dataset-derived prompt, and a backend name.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
import zlib

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses.claude_code import (
    Capture,
    ClaudeCodeHarness,
    event_stream_complete,
    proxy_log_complete,
)
from swe_lab.harnesses.claude_code.constants import (
    CONTAINER_PROXY_HOST,
    EVENT_STREAM_NAME,
    PROMPT_NAME,
    PROXY_LOG_NAME,
)
from swe_lab.harnesses.claude_code.proxy import (
    build_proxy,
    port_for_index,
    ReverseProxy,
)
from swe_lab.paths import find_repo_root
from swe_lab.sandbox import (
    build_sandbox,
    Inline,
    Mount,
    RunStatus,
    SandboxManager,
    SandboxSpec,
)
from swe_lab.sandbox.observers import DiffExtractObserver

# Proxy ports are drawn from a wide band by a stable hash of the instance id, so
# concurrent rollouts on one host never collide (mirrors W1's per-run distinct
# port discipline, which keyed off the dataset index).
_PROXY_PORT_SPAN = 10000


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
  workspace: Path
  artifacts: dict[str, Path]
  metrics: dict[str, float]


def run_rollout(
    spec: SandboxSpec,
    *,
    prompt: str,
    model: str,
    backend: str,
    workspace: Path,
    timeout: float,
    exclude_globs: tuple[str, ...] = (),
    capture: Capture = Capture.STREAM,
    pull: bool = True,
    pass_env: tuple[str, ...] = (),
    bare: bool = False,
) -> RolloutOutcome:
  """Run one agent rollout and extract its patch + trace.

  Args:
    spec: The run context (image / workdir / base_commit / instance_id).
    prompt: The dataset-derived solve prompt (staged as ``prompt.txt``).
    model: The ``--model`` alias for the agent.
    backend: The registered backend name to run on (e.g. ``host`` / ``ghjob``).
    workspace: The run's workspace directory (created fresh).
    timeout: Seconds before the agent run is killed.
    exclude_globs: Build-noise denylist for the diff extraction.
    capture: The output-capture strategy — ``STREAM`` (default) reads the
      agent's ``event_stream``; ``PROXY`` records via a host-side
      ``cc-reverse-proxy`` writing into the workspace.
    pull: Whether to pull the image before the run (A-host only).
    pass_env: Variables inherited by reference into the sandbox — the agent's
      auth secret (the OAuth token, or ``ANTHROPIC_API_KEY`` in ``bare`` mode).
    bare: Run the agent with ``--bare`` (API-key auth; the key is supplied via
      ``pass_env``, not here).

  Returns:
    The rollout outcome (patch, flags, conversation, status).
  """
  harness, extra_hosts, proxy = _capture_setup(
      spec, model, workspace, capture, bare=bare
  )
  conversation = ConversationObserver(producer=harness)
  extract = DiffExtractObserver(exclude_globs=exclude_globs)
  # prompt.txt is dataset-derived, staged by the composition; the
  # harness contributes its script and the pinned binary (a read-only mount).
  mounts = {PROMPT_NAME: Mount(Inline(prompt.encode()))} | harness.mounts(
      spec.workdir
  )
  sandbox = build_sandbox(
      backend,
      spec,
      workspace,
      network=True,
      pull=pull,
      pass_env=pass_env,
      extra_hosts=extra_hosts,
  )
  manager = SandboxManager(
      sandbox=sandbox,
      output_dir=workspace,
      observers=[conversation, extract],
      mounts=mounts,
  )
  with proxy, manager.session() as sb:
    harness.run(sb, timeout=timeout)

  return RolloutOutcome(
      instance_id=spec.instance_id,
      patch=extract.patch,
      is_empty=extract.is_empty,
      binary_stripped=extract.binary_stripped,
      complete=_run_complete(workspace, capture),
      conversation=conversation.conversation or Conversation(messages=[]),
      status=manager.result.status,
      workspace=workspace,
      artifacts=manager.result.artifacts,
      metrics=manager.result.metrics,
  )


def _capture_setup(
    spec: SandboxSpec,
    model: str,
    workspace: Path,
    capture: Capture,
    *,
    bare: bool = False,
) -> tuple[
    ClaudeCodeHarness,
    tuple[str, ...],
    contextlib.AbstractContextManager[object],
]:
  """Build the harness, host entries, and proxy context for the capture mode.

  For ``STREAM`` there are no extra hosts and the proxy context is a no-op. For
  ``PROXY`` it starts a ``cc-reverse-proxy`` writing into the workspace, points
  the agent at it, and returns the host-gateway ``--add-host`` entry so an
  A-host container can reach the host-side proxy (A-ghjob ignores it).
  """
  if capture is Capture.STREAM:
    return (
        ClaudeCodeHarness(model=model, bare=bare),
        (),
        contextlib.nullcontext(),
    )
  port = port_for_index(
      zlib.crc32(spec.instance_id.encode()) % _PROXY_PORT_SPAN
  )
  base_url = f"http://{CONTAINER_PROXY_HOST}:{port}"
  harness = ClaudeCodeHarness(
      model=model,
      capture=capture,
      proxy_base_url=base_url,
      bare=bare,
  )
  extra_hosts = (f"{CONTAINER_PROXY_HOST}:host-gateway",)
  proxy = ReverseProxy(
      port, workspace / PROXY_LOG_NAME, build_proxy(find_repo_root())
  )
  return harness, extra_hosts, proxy


def _run_complete(workspace: Path, capture: Capture) -> bool:
  """Read the agent-completion signal from whichever trace the run captured."""
  name = PROXY_LOG_NAME if capture is Capture.PROXY else EVENT_STREAM_NAME
  path = workspace / name
  text = path.read_text() if path.is_file() else ""
  if capture is Capture.PROXY:
    return proxy_log_complete(text)
  return event_stream_complete(text)
