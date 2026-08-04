"""The ``claude_code`` harness: run Claude Code headless in the sandbox.

Stages its invocation script, runs the agent, and converts the event-stream
output into a canonical ``Conversation``. It is dataset-agnostic —
``run(prompt=...)`` receives the dataset-derived prompt as text and lands it in
a file of this harness's own choosing; the invocation script reads it from
there.

The **binary is not this harness's to place**: it invokes it at the agreed
absolute path (:data:`~swe_lab.harnesses.claude_code.constants.BINARY_AT`) and
each backend's own observer puts it there the way that backend can (see
``swe_lab.sandbox.backends``). Mounting it from here would have forced one
backend's answer — hand over ~100 MB from the host — on every other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import shlex
from typing import override

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses.base import Harness
from swe_lab.harnesses.observer import HarnessOutcomeObserver
from swe_lab.sandbox import (
    ExecResult,
    Inline,
    Mount,
    Mounts,
    SandboxError,
    SandboxFs,
    SandboxObserver,
)

from .capture import Capture
from .constants import (
    AGENT_ENV_NAME,
    AGENT_HOME,
    AGENT_SCRIPT_NAME,
    AGENT_STDERR_NAME,
    BINARY_AT,
    CONTAINER_PROXY_HOST,
    DEFAULT_MODEL,
    EVENT_STREAM_NAME,
    PROMPT_FILENAME,
    PROXY_LOG_NAME,
)
from .convert import (
    event_stream_complete,
    event_stream_to_conversation,
    proxy_log_complete,
    proxy_log_to_conversation,
)
from .proxy import DEFAULT_BASE_PORT
from .recorder import ProxyRecorder


def _read_text(sb: SandboxFs, name: str) -> str:
  """Read a workspace file as text, tolerant of odd bytes and absence."""
  if not sb.exists(name):
    return ""
  return sb.read(name).decode("utf-8", "backslashreplace")


# A shell variable name; anything else would make the sourced file a syntax
# error, which `set -u` would turn into "the agent never ran" with no clue why.
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _env_exports(env: Mapping[str, str]) -> str:
  """Render caller env as ``export K=V`` lines, values shell-quoted.

  Args:
    env: Variable name → value.

  Returns:
    The sourceable script text, in the given order.

  Raises:
    SandboxError: If a name is not a valid shell identifier.
  """
  bad = sorted(name for name in env if not _ENV_NAME_RE.match(name))
  if bad:
    raise SandboxError(f"invalid environment variable name(s): {bad}")
  lines = [f"export {name}={shlex.quote(value)}" for name, value in env.items()]
  return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  """The Claude Code agent as a sandbox-engine harness plug.

  Attributes:
    model: The ``--model`` alias to run.
    capture: The output-capture strategy — ``STREAM`` (default) or ``PROXY``.
    proxy_port: The host port ``PROXY`` capture records on. This harness runs
      the recorder itself (see ``observers``), so the port is all it needs;
      two runs on one host must not share one.
    proxy_base_url: What the *agent* dials to reach that recorder. Defaults to
      the container→host gateway on the declared port, which is what a
      containerized run needs; set it when the run reaches the host some other
      way. Unused for ``STREAM``.
    bare: Run the agent with ``--bare`` — API-key auth mode, which disables the
      subscription OAuth token. The API key itself is supplied to the sandbox
      by the composition via the environment (``ANTHROPIC_API_KEY``, by
      reference — like the OAuth token), not held by this harness.
  """

  model: str = DEFAULT_MODEL
  capture: Capture = Capture.STREAM
  proxy_port: int = DEFAULT_BASE_PORT
  proxy_base_url: str | None = None
  bare: bool = False

  @property
  @override
  def name(self) -> str:
    """This harness's identifier; namespaces its artifacts."""
    return "claude_code"

  @property
  def agent_proxy_url(self) -> str:
    """The URL the in-container agent dials to reach the recorder."""
    return (
        self.proxy_base_url
        or f"http://{CONTAINER_PROXY_HOST}:{self.proxy_port}"
    )

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Return the generic pair, preceded by the recorder ``PROXY`` needs.

    This harness's own choice (ADR-0007 §3), not an inherited default — the
    pair are generic building blocks that delegate back to
    ``to_conversation`` / ``completed`` / ``native_outputs``, which is where
    everything Claude-Code-specific lives.

    In ``PROXY`` capture the trace *is* a recording this harness has to make,
    so it composes the recorder itself, **first**: its ``before_destroy``
    closes the proxy and lands the log, and only then does the converter read
    it. Nothing above this class has to know a proxy exists.
    """
    recorder = (
        (ProxyRecorder(port=self.proxy_port),)
        if self.capture is Capture.PROXY
        else ()
    )
    return (
        *recorder,
        ConversationObserver(producer=self),
        HarnessOutcomeObserver(harness=self),
    )

  @override
  def mounts(self, workdir: str) -> Mounts:
    """Stage the invocation script and its env file — and nothing else.

    The agent binary is deliberately absent: it is machinery, not this run's
    material, and the backend provisions it at ``BINARY_AT`` (see the module
    docstring).

    The env file is staged **empty**: the script always sources it, and
    ``run(env=...)`` fills it in, so injected variables need no second version
    of the script.

    Args:
      workdir: The repo path the invocation script ``cd``s into.

    Returns:
      The two staged files.
    """
    return {
        AGENT_SCRIPT_NAME: Mount(
            Inline(self._invocation_script(workdir).encode()), executable=True
        ),
        AGENT_ENV_NAME: Mount(Inline(b"")),
    }

  @override
  def run(
      self,
      sb: SandboxFs,
      *,
      prompt: str,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Land the prompt, fill in the env file, then run the staged script.

    Args:
      sb: The live sandbox to run in.
      prompt: The task prompt. Written to this harness's own prompt file
        (ADR-0007 §8 — the caller hands text; where it lands is ours), which
        the invocation script feeds to the agent on stdin.
      timeout: Seconds before the agent run is killed.
      env: Extra ``KEY=VALUE`` exports for the agent, written into the sourced
        env file so they apply after the script's own defaults. A name that is
        not a shell identifier is rejected (see :func:`_env_exports`) rather
        than corrupting the file and skipping the run.

    Returns:
      The agent script's outcome. The script ends in ``|| true`` so the agent's
      own exit code never fails the step — what this still carries is whether
      *we* killed it on timeout.
    """
    sb.write(PROMPT_FILENAME, prompt.encode())
    if env:
      sb.write(AGENT_ENV_NAME, _env_exports(env).encode())
    return sb.run_script(AGENT_SCRIPT_NAME, timeout=timeout)

  @override
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct the run writes into the workspace.

    The trace file depends on the capture strategy: ``STREAM`` writes the
    agent's ``event_stream``; ``PROXY`` records into the proxy log instead.
    Roles carry the payload's format (``.jsonl`` — both traces are
    newline-delimited, one record per line — and ``.log``), so a consumer reads
    the artifact name and knows how to parse it.
    """
    trace = (
        {"proxy_log.jsonl": PROXY_LOG_NAME}
        if self.capture is Capture.PROXY
        else {"event_stream.jsonl": EVENT_STREAM_NAME}
    )
    return trace | {"stderr.log": AGENT_STDERR_NAME}

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    """Convert the run's captured trace into a ``Conversation``.

    Both strategies land on the same typed model — ``STREAM`` from the
    ``event_stream``, ``PROXY`` from the proxy log.
    """
    if self.capture is Capture.PROXY:
      return proxy_log_to_conversation(_read_text(sb, PROXY_LOG_NAME))
    return event_stream_to_conversation(_read_text(sb, EVENT_STREAM_NAME))

  @override
  def completed(self, sb: SandboxFs) -> bool:
    """Read the clean-finish signal from whichever trace the run captured.

    ``STREAM`` reads the terminal ``result`` event; ``PROXY`` reads the last
    record's ``complete`` flag. An absent trace reads as ``False``
    (``_read_text`` is absence-tolerant), so a crashed run reports incomplete
    rather than raising.
    """
    if self.capture is Capture.PROXY:
      return proxy_log_complete(_read_text(sb, PROXY_LOG_NAME))
    return event_stream_complete(_read_text(sb, EVENT_STREAM_NAME))

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

    """
    home = shlex.quote(AGENT_HOME)
    binary = shlex.quote(BINARY_AT)
    prompt = f'"$SANDBOX_WORKSPACE"/{PROMPT_FILENAME}'
    stderr = f'"$SANDBOX_WORKSPACE"/{AGENT_STDERR_NAME}'
    lines = [
        "set -u",
        f"export HOME={home}",
        f"mkdir -p {home}",
        # Some builds refuse --dangerously-skip-permissions as root unless a
        # sandbox is signalled; the throwaway container is our sandbox.
        "export IS_SANDBOX=1",
        # Caller-injected env (empty unless ``run(env=...)`` filled it in).
        # Sourced *here* deliberately: after the defaults above, so a caller can
        # override them, but before the capture wiring below, so it cannot
        # clobber the proxy URL this run was wired to.
        f'. "$SANDBOX_WORKSPACE"/{AGENT_ENV_NAME}',
    ]
    if self.capture is Capture.PROXY:
      # Route the agent's API calls through the recording proxy; its own stdout
      # (a plain JSON result) is not the trace, so discard it.
      lines.append(
          f"export ANTHROPIC_BASE_URL={shlex.quote(self.agent_proxy_url)}"
      )
      output_format = "json"
      capture_redirect = "> /dev/null"
    else:
      event_stream = f'"$SANDBOX_WORKSPACE"/{EVENT_STREAM_NAME}'
      output_format = "stream-json --verbose"
      capture_redirect = f"> {event_stream}"
    bare = " --bare" if self.bare else ""
    lines += [
        f"cd {shlex.quote(workdir)}",
        # Feed the prompt on stdin (``-p`` with no argument reads it) rather
        # than inlining it into the argv — no shell-quoting hazard for a large,
        # arbitrary prompt.
        (
            f"{binary} -p{bare}"
            f" --model {shlex.quote(self.model)}"
            f" --output-format {output_format}"
            " --dangerously-skip-permissions"
            f" < {prompt} {capture_redirect} 2> {stderr} || true"
        ),
    ]
    return "\n".join(lines) + "\n"
