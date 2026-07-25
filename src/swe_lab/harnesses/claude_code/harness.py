"""The ``claude_code`` harness: run Claude Code headless in the sandbox.

Stages its invocation script, declares the pinned binary as a read-only asset,
runs the agent, and converts the event-stream output into a canonical
``Conversation``. It is dataset-agnostic — the prompt is staged by the
composition (dataset-derived); the invocation script only reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import override

from swe_lab.conversation import Conversation
from swe_lab.harnesses.base import Harness
from swe_lab.harnesses.claude_code.binary import ensure_claude_binary
from swe_lab.sandbox import (
    Assets,
    Inline,
    LocalFile,
    Mount,
    Mounts,
    Sandbox,
    SandboxError,
)

from .capture import Capture
from .constants import (
    AGENT_HOME,
    AGENT_SCRIPT_NAME,
    AGENT_STDERR_NAME,
    BINARY_AT,
    DEFAULT_MODEL,
    EVENT_STREAM_NAME,
    PROMPT_NAME,
    PROXY_LOG_NAME,
)
from .convert import event_stream_to_conversation, proxy_log_to_conversation


@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  """The Claude Code agent as a sandbox-engine harness plug.

  Attributes:
    model: The ``--model`` alias to run.
    binary_path: Inject a ready binary (Docker-free tests); otherwise the pinned
      binary is provisioned by ``ensure_claude_binary``.
    capture: The output-capture strategy — ``STREAM`` (default) or ``PROXY``.
    proxy_base_url: The API base URL the agent uses in ``PROXY`` capture (the
      composition points this at the host-side proxy); unused for ``STREAM``.
  """

  model: str = DEFAULT_MODEL
  binary_path: Path | None = None
  capture: Capture = Capture.STREAM
  proxy_base_url: str | None = None

  @override
  def mounts(self, workdir: str) -> Mounts:
    """Stage the harness's own file — the invocation script (not the prompt)."""
    return {
        AGENT_SCRIPT_NAME: Mount(
            Inline(self._invocation_script(workdir).encode()), executable=True
        )
    }

  @override
  def assets(self) -> Assets:
    """Place the pinned binary as a read-only asset at its fixed path."""
    binary = self.binary_path or ensure_claude_binary()
    return {BINARY_AT: LocalFile(binary)}

  @override
  def run(self, sb: Sandbox, *, timeout: float) -> None:
    """Run the staged ``agent.sh`` by its workspace path."""
    _ = sb.run(AGENT_SCRIPT_NAME, timeout=timeout)

  @override
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct the run writes into the workspace.

    The trace file depends on the capture strategy: ``STREAM`` writes the
    agent's ``event_stream``; ``PROXY`` records into the proxy log instead.
    """
    trace = (
        {"proxy_log": PROXY_LOG_NAME}
        if self.capture is Capture.PROXY
        else {"event_stream": EVENT_STREAM_NAME}
    )
    return trace | {"stderr": AGENT_STDERR_NAME}

  @override
  def to_conversation(self, workspace: Path) -> Conversation:
    """Convert the run's captured trace into a ``Conversation``.

    Both strategies land on the same typed model — ``STREAM`` from the
    ``event_stream``, ``PROXY`` from the proxy log.
    """
    if self.capture is Capture.PROXY:
      return proxy_log_to_conversation(workspace / PROXY_LOG_NAME)
    return event_stream_to_conversation(workspace / EVENT_STREAM_NAME)

  def _invocation_script(self, workdir: str) -> str:
    """Build the run script: run the agent, redirect its outputs, never fail.

    In ``STREAM`` capture the agent's ``stream-json`` stdout *is* the trace
    (redirected to the event-stream file). In ``PROXY`` capture the host-side
    proxy records the trace instead, so the agent points at it via
    ``ANTHROPIC_BASE_URL`` and its own stdout is discarded.

    Args:
      workdir: The repo path (``$WORKDIR``) the agent ``cd``s into.

    Returns:
      The bash script text staged as the invocation mount.

    Raises:
      SandboxError: If ``PROXY`` capture is requested without a base URL.
    """
    home = shlex.quote(AGENT_HOME)
    binary = shlex.quote(BINARY_AT)
    prompt = f'"$SANDBOX_WORKSPACE"/{PROMPT_NAME}'
    stderr = f'"$SANDBOX_WORKSPACE"/{AGENT_STDERR_NAME}'
    lines = [
        "set -u",
        f"export HOME={home}",
        f"mkdir -p {home}",
        # Some builds refuse --dangerously-skip-permissions as root unless a
        # sandbox is signalled; the throwaway container is our sandbox.
        "export IS_SANDBOX=1",
        # Isolate the run from the target repo's CLAUDE.md auto-discovery, so
        # the agent's context is the prompt — not repo-shipped instructions.
        # (--bare would also do this but forces API-key auth, disabling the
        # OAuth token we run on; this env var is the auth-safe equivalent.)
        "export CLAUDE_CODE_DISABLE_CLAUDE_MDS=1",
    ]
    if self.capture is Capture.PROXY:
      if not self.proxy_base_url:
        raise SandboxError("proxy capture requires proxy_base_url to be set")
      # Route the agent's API calls through the recording proxy; its own stdout
      # (a plain JSON result) is not the trace, so discard it.
      lines.append(
          f"export ANTHROPIC_BASE_URL={shlex.quote(self.proxy_base_url)}"
      )
      output_format = "json"
      capture_redirect = "> /dev/null"
    else:
      event_stream = f'"$SANDBOX_WORKSPACE"/{EVENT_STREAM_NAME}'
      output_format = "stream-json --verbose"
      capture_redirect = f"> {event_stream}"
    lines += [
        f"cd {shlex.quote(workdir)}",
        # Feed the prompt on stdin (``-p`` with no argument reads it) rather
        # than inlining it into the argv — no shell-quoting hazard for a large,
        # arbitrary prompt.
        (
            f"{binary} -p"
            f" --model {shlex.quote(self.model)}"
            f" --output-format {output_format}"
            " --dangerously-skip-permissions"
            f" < {prompt} {capture_redirect} 2> {stderr} || true"
        ),
    ]
    return "\n".join(lines) + "\n"
