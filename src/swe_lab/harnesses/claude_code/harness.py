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
from dataclasses import dataclass, field
import logging
import re
import shlex
from typing import override

from swe_lab.conversation import Conversation, ConversationObserver
from swe_lab.harnesses.base import Harness
from swe_lab.harnesses.observer import HarnessOutcomeObserver
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
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
    AGENT_EXIT_CODE_NAME,
    AGENT_HOME,
    AGENT_INFO_NAME,
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
    event_stream_complete,
    event_stream_to_conversation,
    proxy_log_complete,
    proxy_log_to_conversation,
)
from .proxy import DEFAULT_BASE_PORT
from .recorder import ProxyRecorder

_logger = logging.getLogger(__name__)

# Generous: `--help` on a cold 275 MB binary is mostly process start-up.
_INFO_TIMEOUT_S = 60.0


@dataclass
class AgentInfoObserver(SandboxObserver):
  """Record the agent's own account of itself, for post-hoc debugging.

  Runs ``--version`` and ``--help`` against the provisioned binary once the
  sandbox is up, lands the combined output in the workspace, and registers it
  as an artifact. *Which build actually ran* is the first question anyone asks
  when a run behaves oddly, and once the sandbox is gone the answer is
  otherwise unrecoverable — the pin says what we asked for, not what the
  sandbox had.

  **Never fails a run.** Every step is caught: a diagnostic that can abort the
  thing it documents is worse than no diagnostic. A failed capture is logged
  and simply contributes nothing.

  Single-run, like every stateful observer: ``after_create`` captures (that
  hook cannot return a contribution) and ``before_destroy`` hands it over.

  Attributes:
    binary: The in-sandbox path to interrogate.
    filename: The workspace file the output lands in.
    artifact: The name it is registered under.
  """

  binary: str = BINARY_AT
  filename: str = AGENT_INFO_NAME
  artifact: str = INFO_ARTIFACT
  _captured: bool = field(default=False, init=False, repr=False)

  @override
  def output_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the info file — advisory, since a run is valid without it."""
    return (
        ArtifactSchema(
            self.artifact,
            required=False,
            description="the agent's own --version and --help output",
        ),
    )

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Interrogate the binary and land the output in the workspace.

    Args:
      sb: The live sandbox, with the binary already provisioned (the backend's
        own observer runs first).
    """
    binary = shlex.quote(self.binary)
    sections: list[str] = []
    for flag in ("--version", "--help"):
      try:
        # 2>&1 because a binary that cannot run at all says so on stderr, and
        # that is exactly the case this file exists to explain.
        result = sb.run_command(
            f"{binary} {flag} 2>&1", timeout=_INFO_TIMEOUT_S
        )
        body = (result.stdout + result.stderr).strip()
        sections.append(f"$ claude {flag}\n[exit {result.exit_code}]\n{body}")
      except Exception:  # noqa: BLE001 — a diagnostic must never fail the run
        _logger.exception("claude %s failed; recording that instead", flag)
        sections.append(f"$ claude {flag}\n[did not run]")
    try:
      sb.write(self.filename, ("\n\n".join(sections) + "\n").encode())
      self._captured = True
    except Exception:  # noqa: BLE001 — as above
      _logger.exception("could not write %s; skipping it", self.filename)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Register the captured file, if there is one.

    Args:
      sb: Unused — the file is already in the workspace.

    Returns:
      The artifact registration, or ``None`` when nothing was captured.
    """
    del sb
    if not self._captured:
      return None
    return Contribution(artifacts={self.artifact: self.filename})


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
  capture: Capture = Capture.STREAM
  proxy_port: int = DEFAULT_BASE_PORT
  proxy_base_url: str | None = None
  bare: bool = True
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
        # First: record which build the sandbox actually got, before anything
        # can go wrong with the run it describes.
        AgentInfoObserver(),
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
    """Build the run script for an *unattended* run.

    The invariants it enforces, each of which an unattended run needs and none
    of which ``--dangerously-skip-permissions`` provides:

    - **Interactive and plan-mode tools are always denied.** ``EnterPlanMode``
      needs no permission at all, so nothing ever asks; ``ExitPlanMode`` and
      ``AskUserQuestion`` ask for a *reply*, which never comes. Denying only
      ``ExitPlanMode`` is worse than denying neither — the agent enters plan
      mode, cannot leave, and burns the budget read-only.
    - **Turns are bounded** (``--max-turns``), so an agent loop cannot run away.
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
    # Always denied: not covered by --dangerously-skip-permissions, and each
    # hangs an unattended run in its own way (see the constant).
    denied = ",".join(UNATTENDED_DENIED_TOOLS)
    flags = [
        "-p",
        f"--model {shlex.quote(self.model)}",
        f"--output-format {output_format}",
        "--dangerously-skip-permissions",
        f"--disallowedTools {shlex.quote(denied)}",
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
      if self.capture is Capture.PROXY:
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
        # The agent's real status, reported out-of-band. `set -u` is on but
        # `set -e` is not, so execution continues here; capturing $? on the very
        # next line and then exiting 0 keeps container teardown unchanged while
        # still telling a caller success from failure from a kill (143=SIGTERM).
        f"printf '%s\\n' \"$?\" > {exit_file}",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"
