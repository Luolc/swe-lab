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
import logging
import shlex
from typing import override

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses.base import AgentOutcome, Harness
from swe_lab.harnesses.common import (
    AgentInfoObserver,
    env_exports,
    read_text,
    status_tail,
)
from swe_lab.harnesses.observer import HarnessOutcomeObserver
from swe_lab.sandbox import (
    AgentAsset,
    ExecResult,
    Inline,
    Mount,
    Mounts,
    SandboxError,
    SandboxFs,
    SandboxObserver,
)

from .capture import Capture, Effort
from .constants import (
    AGENT_ENV_NAME,
    AGENT_EXIT_CODE_NAME,
    AGENT_HOME,
    AGENT_SCRIPT_NAME,
    AGENT_STDERR_NAME,
    BINARY_AT,
    CONTAINER_PROXY_HOST,
    DEFAULT_MODEL,
    EVENT_STREAM_NAME,
    INFO_ARTIFACT,
    MAX_PROMPT_BYTES,
    PROMPT_FILENAME,
    PROXY_LOG_NAME,
    UNATTENDED_DENIED_TOOLS,
)
from .convert import (
    event_stream_outcome,
    event_stream_to_conversation,
    proxy_log_outcome,
    proxy_log_to_conversation,
)
from .proxy import DEFAULT_BASE_PORT
from .recorder import ProxyRecorder

_logger = logging.getLogger(__name__)

# Generous: `--help` on a cold 275 MB binary is mostly process start-up.
_INFO_TIMEOUT_S = 60.0


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
    bare: Run the agent with ``--bare`` — minimal mode: no hooks, plugins, MCP
      config, auto-memory or CLAUDE.md discovery, so the repo under test cannot
      inject instructions into the harness. **It also disables keychain and
      OAuth reads**, so a bare run authenticates by ``ANTHROPIC_API_KEY``
      only; verified on 2.1.220, where a bare run with a valid
      ``CLAUDE_CODE_OAUTH_TOKEN`` still fails "Not logged in".

      **On by default**: an unattended run should be reproducible across
      machines, and without it the repo under test can inject instructions via
      CLAUDE.md, hooks or MCP config. A composition that authenticates by OAuth
      sets it back to ``False`` explicitly — the shipped ``rollout`` definition
      does exactly that. See the script's guard.
    effort: Reasoning effort for the run, passed as ``--effort``. Defaults to
      ``HIGH``: an unattended solve is the case worth spending on, and the
      agent's own default is not stated in ``--help``, so pinning it makes a
      sweep reproducible rather than dependent on whatever the build prefers.
      Typed, because the agent treats an unknown value as a *warning* and
      quietly runs at its default — a typo would otherwise mis-run a whole
      batch silently.
    max_turns: Agent-loop runaway guard, passed as ``--max-turns``. The flag is
      undocumented in ``--help`` on 2.1.220 but accepted (a bogus flag is
      rejected with "unknown option" in the same position, so this is
      acceptance, not a parser that ignores everything).
    max_budget_usd: Optional spend ceiling (``--max-budget-usd``, print mode
      only). Omitted entirely when ``None``.
    subagent_wait_ceiling_ms: Optional ceiling on waiting for background
      subagents (``CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS``). Omitted when
      ``None``, which leaves the agent's own ten-minute default in place.
      Letting the run bound itself yields a clean exit and a complete trace
      where an external kill would truncate mid-write.
  """

  model: str = DEFAULT_MODEL
  capture: Capture = "stream"
  proxy_port: int = DEFAULT_BASE_PORT
  proxy_base_url: str | None = None
  bare: bool = True
  effort: Effort = "high"
  max_turns: int = 500
  max_budget_usd: float | None = None
  subagent_wait_ceiling_ms: int | None = None

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
    ``to_conversation`` / ``outcome`` / ``native_outputs``, which is where
    everything Claude-Code-specific lives.

    In ``PROXY`` capture the trace *is* a recording this harness has to make,
    so it composes the recorder itself, **first**: its ``before_destroy``
    closes the proxy and lands the log, and only then does the converter read
    it. Nothing above this class has to know a proxy exists.
    """
    recorder = (
        (ProxyRecorder(port=self.proxy_port),)
        if self.capture == "proxy"
        else ()
    )
    return (
        # First: record which build the sandbox actually got, before anything
        # can go wrong with the run it describes.
        AgentInfoObserver(binary=BINARY_AT, artifact=INFO_ARTIFACT),
        *recorder,
        ConversationObserver(producer=self),
        HarnessOutcomeObserver(harness=self),
    )

  @override
  def assets(self) -> Sequence[AgentAsset]:
    """Declare the pinned binary at ``BINARY_AT``.

    One file. ``ensure_claude_binary`` already has the materializer contract
    (``dest=None`` caches, a path installs), so the seam needed no new
    fetching code.
    """
    from .binary import ensure_claude_binary

    return (
        AgentAsset(
            path=BINARY_AT,
            materialize=lambda dest: ensure_claude_binary(dest=dest),
        ),
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
      The agent script's outcome. The script always exits 0, so this carries
      only whether *we* killed it on timeout; the agent's own status is written
      to ``claude.exit_code`` in the workspace.

    Raises:
      SandboxError: If the prompt exceeds Claude Code's 10 MB stdin cap, or an
        env name is not a shell identifier.
    """
    encoded = prompt.encode()
    if len(encoded) > MAX_PROMPT_BYTES:
      # Claude Code caps piped stdin at 10 MB and exits non-zero past it. Fail
      # here, where the size is known and the message can say so, rather than
      # staging it and reading the cap back as an opaque agent failure.
      raise SandboxError(
          f"prompt is {len(encoded)} bytes; Claude Code caps piped stdin at"
          f" {MAX_PROMPT_BYTES}"
      )
    sb.write(PROMPT_FILENAME, encoded)
    if env:
      sb.write(AGENT_ENV_NAME, env_exports(env).encode())
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
        if self.capture == "proxy"
        else {"event_stream.jsonl": EVENT_STREAM_NAME}
    )
    return trace | {
        "stderr.log": AGENT_STDERR_NAME,
        "exit_code.txt": AGENT_EXIT_CODE_NAME,
    }

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    """Convert the run's captured trace into a ``Conversation``.

    Both strategies land on the same typed model — ``STREAM`` from the
    ``event_stream``, ``PROXY`` from the proxy log.
    """
    if self.capture == "proxy":
      return proxy_log_to_conversation(read_text(sb, PROXY_LOG_NAME))
    return event_stream_to_conversation(read_text(sb, EVENT_STREAM_NAME))

  @override
  def outcome(self, sb: SandboxFs) -> AgentOutcome:
    """Classify the ending from whichever trace the run captured.

    ``STREAM`` reads the agent's own terminal ``result`` event, so it can name
    every ending; ``PROXY`` sees API traffic only and reports the coarse pair
    it can actually evidence (see :func:`proxy_log_outcome`). An absent trace
    reads as ``NO_OUTPUT`` (``_read_text`` is absence-tolerant), so a crashed
    run reports an outcome rather than raising.
    """
    if self.capture == "proxy":
      return proxy_log_outcome(read_text(sb, PROXY_LOG_NAME))
    return event_stream_outcome(read_text(sb, EVENT_STREAM_NAME))

  def _invocation_script(self, workdir: str) -> str:
    """Build the run script for an *unattended* run.

    The invariants it enforces, each of which an unattended run needs and none
    of which ``--dangerously-skip-permissions`` provides:

    - **Interactive and plan-mode tools are always denied.** ``EnterPlanMode``
      needs no permission at all, so nothing ever asks; ``ExitPlanMode`` and
      ``AskUserQuestion`` ask for a *reply*, which never comes. Denying only
      ``ExitPlanMode`` is worse than denying neither — the agent enters plan
      mode, cannot leave, and burns the budget read-only.
    - **Turns are bounded** (``--max-turns``), so an agent loop cannot run away.
    - **Reasoning effort is pinned** (``--effort``) rather than left to the
      build's default, which ``--help`` does not state.
    - **The exit status is reported out-of-band.** The script itself always
      exits 0 so teardown is unchanged; the real code lands in
      ``claude.exit_code`` (143 = SIGTERM, i.e. someone killed the turn).
    - **Wall-clock is the caller's**, deliberately not here.

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
    if self.capture == "proxy":
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
    # Always denied: not covered by --dangerously-skip-permissions, and each
    # hangs an unattended run in its own way (see the constant).
    denied = ",".join(UNATTENDED_DENIED_TOOLS)
    flags = [
        "-p",
        f"--model {shlex.quote(self.model)}",
        f"--output-format {output_format}",
        "--dangerously-skip-permissions",
        f"--disallowedTools {shlex.quote(denied)}",
        f"--effort {self.effort}",
        # Undocumented in --help on 2.1.220, but accepted — verified against a
        # bogus flag in the same position, which is rejected outright.
        f"--max-turns {int(self.max_turns)}",
    ]
    if self.bare:
      flags.insert(1, "--bare")
    if self.max_budget_usd is not None:
      flags.append(f"--max-budget-usd {self.max_budget_usd}")

    if self.subagent_wait_ceiling_ms is not None:
      # After the caller env block on purpose: this bound is the harness's, and
      # a prompt-supplied env must not raise it.
      lines.append(
          "export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="
          f"{int(self.subagent_wait_ceiling_ms)}"
      )

    if self.bare:
      # Bare mode reads neither the keychain nor OAuth, so a missing API key
      # would otherwise surface as a plain-text "Not logged in" result on
      # stdout — a *successful-looking* run that did nothing.
      lines += [
          'if [ -z "${ANTHROPIC_API_KEY:-}" ]; then',
          '  echo "FATAL: --bare needs ANTHROPIC_API_KEY (it does not read'
          ' OAuth or the keychain)" >&2',
          "  exit 78",
          "fi",
      ]
      if self.capture == "proxy":
        lines += [
            'if [ -z "${ANTHROPIC_BASE_URL:-}" ]; then',
            '  echo "FATAL: PROXY capture needs ANTHROPIC_BASE_URL" >&2',
            "  exit 78",
            "fi",
        ]

    exit_file = f'"$SANDBOX_WORKSPACE"/{AGENT_EXIT_CODE_NAME}'
    lines += [
        f"cd {shlex.quote(workdir)}",
        # Feed the prompt on stdin (``-p`` with no argument reads it) rather
        # than inlining it into the argv — no shell-quoting hazard for a large,
        # arbitrary prompt.
        (
            f"{binary} {' '.join(flags)}"
            f" < {prompt} {capture_redirect} 2> {stderr}"
        ),
        *status_tail(exit_file),
    ]
    return "\n".join(lines) + "\n"
