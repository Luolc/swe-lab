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
from swe_lab.core.agent.binary import ensure_claude_binary
from swe_lab.harnesses.base import Harness
from swe_lab.sandbox import Assets, Inline, LocalFile, Mount, Mounts, Sandbox

from .constants import (
    AGENT_HOME,
    AGENT_SCRIPT_NAME,
    AGENT_STDERR_NAME,
    BINARY_AT,
    DEFAULT_MODEL,
    EVENT_STREAM_NAME,
    PROMPT_NAME,
)
from .convert import event_stream_to_conversation


@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  """The Claude Code agent as a sandbox-engine harness plug.

  Attributes:
    model: The ``--model`` alias to run.
    binary_path: Inject a ready binary (Docker-free tests); otherwise the pinned
      binary is provisioned by ``ensure_claude_binary``.
  """

  model: str = DEFAULT_MODEL
  binary_path: Path | None = None

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
    """Name every native byproduct the run writes into the workspace."""
    return {
        "event_stream": EVENT_STREAM_NAME,
        "stderr": AGENT_STDERR_NAME,
    }

  @override
  def to_conversation(self, workspace: Path) -> Conversation:
    """Convert the run's own ``event_stream`` output into a ``Conversation``."""
    return event_stream_to_conversation(workspace / EVENT_STREAM_NAME)

  def _invocation_script(self, workdir: str) -> str:
    """Build the run script: run the agent, redirect its outputs, never fail."""
    home = shlex.quote(AGENT_HOME)
    binary = shlex.quote(BINARY_AT)
    prompt = f'"$SANDBOX_WORKSPACE"/{PROMPT_NAME}'
    event_stream = f'"$SANDBOX_WORKSPACE"/{EVENT_STREAM_NAME}'
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
        f"cd {shlex.quote(workdir)}",
        # Feed the prompt on stdin (``-p`` with no argument reads it) rather
        # than inlining it into the argv — no shell-quoting hazard for a large,
        # arbitrary prompt.
        (
            f"{binary} -p"
            f" --model {shlex.quote(self.model)}"
            " --output-format stream-json --verbose"
            " --dangerously-skip-permissions"
            f" < {prompt} > {event_stream} 2> {stderr} || true"
        ),
    ]
    return "\n".join(lines) + "\n"
