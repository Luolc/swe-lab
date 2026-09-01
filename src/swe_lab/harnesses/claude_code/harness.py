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

``PROXY`` capture works the same way, and runs the recording proxy **inside**
the sandbox: it is declared as a second asset, started by the invocation script
on the sandbox's own loopback, and it writes its log straight into the
workspace. See :mod:`~swe_lab.harnesses.claude_code.proxy` for why it stopped
being a host process.
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
    home_fallback_lines,
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

from .binary import PINNED_CLAUDE_CODE_VERSION
from .capture import Capture, Effort
from .constants import (
    AGENT_ENV_NAME,
    AGENT_EXIT_CODE_NAME,
    AGENT_HOME,
    AGENT_SCRIPT_NAME,
    AGENT_STDERR_NAME,
    ANTHROPIC_API,
    BINARY_AT,
    DEFAULT_MODEL,
    EVENT_STREAM_NAME,
    INFO_ARTIFACT,
    MAX_PROMPT_BYTES,
    PROMPT_FILENAME,
    PROXY_BASE_URL,
    PROXY_BINARY_AT,
    PROXY_LOG_NAME,
    PROXY_PORT,
    PROXY_STDERR_NAME,
    UNATTENDED_DENIED_TOOLS,
)
from .convert import (
    event_stream_outcome,
    event_stream_to_conversation,
    proxy_log_outcome,
    proxy_log_to_conversation,
)

_logger = logging.getLogger(__name__)

# Generous: `--help` on a cold 275 MB binary is mostly process start-up.
_INFO_TIMEOUT_S = 60.0

# Readiness polling for the in-sandbox proxy: how many attempts, how long
# between them. A pure-Go listener binds in milliseconds, so 30s is a ceiling
# for "something is wrong", not a budget to spend — and it is polled rather
# than slept through, because a fixed sleep is either a wasted second on every
# run or a race on a loaded machine.
_PROXY_READY_ATTEMPTS = 300
_PROXY_READY_INTERVAL_S = "0.1"
# The script exit code reserved for "the run could not even start" (EX_CONFIG),
# already used for the missing-credential guard below.
_MISCONFIGURED_EXIT = 78


def _proxy_start_lines(target: str) -> list[str]:
  """Return the script lines that start the in-sandbox recording proxy.

  Three things have to be true before the agent may run, and each is a line
  here rather than an assumption:

  - **the proxy is running** — backgrounded, with its own output on a workspace
    file (see ``PROXY_STDERR_NAME``), because an in-sandbox process that fails
    silently leaves no other trace;
  - **it is accepting connections** — polled on the loopback port, not slept
    through. A fixed sleep is a race, and the failure it produces (the agent's
    very first API call refused) reads as an auth or network problem;
  - **it is reaped when the script ends** — an ``EXIT`` trap, so the proxy dies
    on every path out, including the guards that ``exit 78`` above it.

  The trap is also why no observer is needed for ordering anymore: by the time
  ``run`` returns, the proxy is gone and its log is closed.

  **If the script is killed instead of exiting** (the caller's timeout fires
  and the container is torn down), the trap never runs and the proxy is killed
  with the container. That truncates the log at a line boundary and loses at
  most the exchange in flight: cc-reverse-proxy appends one JSON record per
  completed exchange and closes the file each time, so every line already
  written is a complete record. A killed run yields *partial* capture, never a
  corrupt file.

  Args:
    target: The upstream API base URL to forward to.

  Returns:
    The lines, in order.
  """
  binary = shlex.quote(PROXY_BINARY_AT)
  log = f'"$SANDBOX_WORKSPACE"/{PROXY_LOG_NAME}'
  own_log = f'"$SANDBOX_WORKSPACE"/{PROXY_STDERR_NAME}'
  # Bash's /dev/tcp is the only TCP probe that needs nothing installed in the
  # image; `curl` and `nc` are not present in every instance image. A shell
  # without it fails every attempt and hits the loud timeout below rather than
  # running the agent against a proxy nobody confirmed.
  probe = f"(exec 3<>/dev/tcp/127.0.0.1/{PROXY_PORT}) 2>/dev/null"
  return [
      (
          f"{binary} --port {PROXY_PORT} --target {shlex.quote(target)}"
          f" --output {log} > {own_log} 2>&1 &"
      ),
      "proxy_pid=$!",
      'trap \'kill "$proxy_pid" 2>/dev/null; wait "$proxy_pid" 2>/dev/null\''
      " EXIT",
      "proxy_wait=0",
      f"until {probe}; do",
      '  if ! kill -0 "$proxy_pid" 2>/dev/null; then',
      f'    echo "FATAL: the capture proxy exited; see {PROXY_STDERR_NAME}"'
      " >&2",
      f"    exit {_MISCONFIGURED_EXIT}",
      "  fi",
      "  proxy_wait=$((proxy_wait+1))",
      f'  if [ "$proxy_wait" -ge {_PROXY_READY_ATTEMPTS} ]; then',
      f'    echo "FATAL: the capture proxy never listened on port'
      f' {PROXY_PORT}; see {PROXY_STDERR_NAME}" >&2',
      f"    exit {_MISCONFIGURED_EXIT}",
      "  fi",
      f"  sleep {_PROXY_READY_INTERVAL_S}",
      "done",
  ]


@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  """The Claude Code agent as a sandbox-engine harness plug.

  Attributes:
    model: The ``--model`` alias to run.
    version: The pinned agent release. Defaulted to the version this harness
      was developed and verified against — a sweep whose agent build floats is
      not reproducible — but overridable, since pinning is a run-level
      decision. The release manifest supplies the checksum, so any published
      version works.

    capture: The output-capture strategy — ``STREAM`` (default) or ``PROXY``.
      ``PROXY`` needs no port and no URL: the proxy runs in the sandbox, on
      the fixed loopback port every run uses (see ``constants.PROXY_PORT``).
    proxy_target: The upstream ``PROXY`` capture forwards to. The default is
      the Anthropic API; an OpenRouter run points it at
      ``https://openrouter.ai/api``, which is not cosmetic — the proxy mirrors
      ``Anthropic-Beta`` into ``X-Anthropic-Beta`` (without which interleaved
      thinking silently does nothing) and injects OpenRouter provider
      preferences, and it does both only when the target says OpenRouter.
      Unused for ``STREAM``.
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
  version: str = PINNED_CLAUDE_CODE_VERSION
  capture: Capture = "stream"
  proxy_target: str = ANTHROPIC_API
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

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Return the generic pair, preceded by the agent-build probe.

    This harness's own choice (ADR-0007 §3), not an inherited default — the
    pair are generic building blocks that delegate back to
    ``to_conversation`` / ``outcome`` / ``native_outputs``, which is where
    everything Claude-Code-specific lives.

    ``PROXY`` capture adds nothing here. It used to add an observer whose whole
    job was ordering — start a host process before ``up``, stop it before the
    converter read its log — and moving the proxy into the sandbox deleted the
    problem rather than solving it: the proxy is started and reaped by the
    invocation script, so it is already gone, and its log already complete,
    before ``run`` returns.
    """
    return (
        # First: record which build the sandbox actually got, before anything
        # can go wrong with the run it describes.
        AgentInfoObserver(binary=BINARY_AT, artifact=INFO_ARTIFACT),
        ConversationObserver(producer=self),
        HarnessOutcomeObserver(harness=self),
    )

  @override
  def assets(self) -> Sequence[AgentAsset]:
    """Declare the pinned agent binary, and the proxy when recording one.

    Both already have the materializer contract (``dest=None`` caches, a path
    installs), so the seam needed no new fetching code. The proxy is declared
    **only** for ``PROXY`` capture: a stream run has no use for it, and an
    asset declared is an asset transferred.

    Returns:
      One asset, or two under ``PROXY`` capture.
    """
    from .binary import ensure_claude_binary
    from .proxy import ensure_proxy_binary, proxy_source_version

    version = self.version
    agent = AgentAsset(
        path=BINARY_AT,
        version=version,
        fetch=lambda dest: ensure_claude_binary(version=version, dest=dest),
    )
    if self.capture != "proxy":
      return (agent,)
    return (
        agent,
        AgentAsset(
            path=PROXY_BINARY_AT,
            # cc-reverse-proxy is a single unversioned Go file in a sibling
            # checkout, so its content hash *is* its release (see the module).
            version=proxy_source_version(),
            fetch=lambda dest: ensure_proxy_binary(dest=dest),
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
        {
            "proxy_log.jsonl": PROXY_LOG_NAME,
            # The proxy's own log, not the trace: what it said about itself,
            # which is the only evidence available when capture came up empty.
            "proxy_stderr.log": PROXY_STDERR_NAME,
        }
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
    (redirected to the event-stream file). In ``PROXY`` capture the script also
    owns the recorder: it starts the proxy on the sandbox's own loopback, waits
    for it, points the agent at it via ``ANTHROPIC_BASE_URL``, discards the
    agent's stdout, and reaps the proxy on exit.

    Args:
      workdir: The repo path (``$WORKDIR``) the agent ``cd``s into.

    Returns:
      The bash script text staged as the invocation mount.

    """
    config_dir = shlex.quote(f"{AGENT_HOME}/.claude")
    binary = shlex.quote(BINARY_AT)
    prompt = f'"$SANDBOX_WORKSPACE"/{PROMPT_FILENAME}'
    stderr = f'"$SANDBOX_WORKSPACE"/{AGENT_STDERR_NAME}'
    lines = [
        "set -u",
        # The image's HOME wins (warm toolchain caches live under it, #240);
        # the CONFIG stays ours — pinned below, so an image cannot inject
        # agent instructions through ~/.claude.json / $HOME/.claude (the
        # ADR-0010 door, which deferring config discovery would reopen).
        *home_fallback_lines(),
        f"export CLAUDE_CONFIG_DIR={config_dir}",
        f"mkdir -p {config_dir}",
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
      # Start the recording proxy, then route the agent's API calls through it;
      # the agent's own stdout (a plain JSON result) is not the trace, so it is
      # discarded.
      lines += _proxy_start_lines(self.proxy_target)
      lines.append(f"export ANTHROPIC_BASE_URL={PROXY_BASE_URL}")
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
          f"  exit {_MISCONFIGURED_EXIT}",
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
