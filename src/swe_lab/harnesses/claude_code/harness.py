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
import json
import logging
import shlex
from typing import Any, override

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
from swe_lab.trace_synthesis.native_supervision import (
    API_KEY_ENV as SUPERVISOR_API_KEY_ENV,
)
from swe_lab.trace_synthesis.native_supervision import (
    BASE_URL_ENV as SUPERVISOR_BASE_URL_ENV,
)
from swe_lab.trace_synthesis.native_supervision import (
    NativeSupervision,
    NativeSupervisionObserver,
    SUPERVISOR_BINARY_AT,
    SUPERVISOR_CONFIG_NAME,
    SUPERVISOR_SUMMARY_NAME,
)
from swe_lab.trace_synthesis.segmented_loop import (
    SegmentedRun,
    SegmentedSupervision,
    SegmentRequest,
)
from swe_lab.trace_synthesis.vocabulary import SUPERVISOR_LOG_NAME

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
    CORRECTION_DONE_NAME,
    CORRECTION_DROP_NAME,
    CORRECTION_FIFO_NAME,
    CORRECTION_RELAY_LOG_NAME,
    CORRECTION_UNCLEAN_NAME,
    DEFAULT_MODEL,
    EVENT_STREAM_NAME,
    INFO_ARTIFACT,
    MAX_PROMPT_BYTES,
    OPENROUTER_API,
    PROMPT_FILENAME,
    PROXY_BASE_URL,
    PROXY_BINARY_AT,
    PROXY_LOG_NAME,
    PROXY_PORT,
    PROXY_STDERR_NAME,
    STREAM_JSON_PROMPT_NAME,
    SUPERVISOR_INFO_NAME,
    SUPERVISOR_PROXY_BASE_URL,
    SUPERVISOR_PROXY_LOG_NAME,
    SUPERVISOR_PROXY_PORT,
    SUPERVISOR_PROXY_STDERR_NAME,
    SUPERVISOR_STDERR_NAME,
    UNATTENDED_DENIED_TOOLS,
)
from .convert import (
    event_stream_outcome,
    event_stream_to_conversation,
    event_stream_usage,
    proxy_log_outcome,
    proxy_log_to_conversation,
    user_event_line,
)
from .native_transcript import NativeTranscriptObserver

# Aliased because this module already has an `API_KEY_ENV` of the actor's, and
# two credentials' variables under one name is the confusion a credential
# boundary can least afford.


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


_REAPED_PIDS_VAR = "reaped_pids"


def _reaper_lines() -> list[str]:
  """Return the lines that install the script's single ``EXIT`` trap.

  **Bash keeps only the last ``EXIT`` trap installed.** Two helpers each
  trapping their own background process therefore silently leaves one of them
  unreaped — the second `trap` replaces the first rather than adding to it. So
  there is one trap, installed once here, over a list every starter appends to.

  Installed **before** anything is backgrounded, so a process started and then
  failing a readiness guard is still reaped on the way out.

  Reaping runs in reverse order of starting: the capture proxy goes last,
  because it is the thing recording whatever the others do on their way down.

  Returns:
    The lines, in order.
  """
  return [
      f"{_REAPED_PIDS_VAR}=",
      # TERM, a bounded grace, then KILL whatever is left. `wait` is not
      # used: `kill "$p"; wait "$p"` per pid deadlocks with more than
      # one background job, and a bare `wait` proved non-terminating in
      # some environments. The escalation is what makes the guarantee
      # true rather than likely — a child that ignores or delays TERM
      # would otherwise outlive the grace period silently, and `run`
      # would return while the capture log was still being written.
      # A KILLed proxy truncates its log at a line boundary (each record
      # is written and closed), so the cost of escalating is partial
      # capture, never a corrupt file.
      "trap '"
      f'for _pid in ${_REAPED_PIDS_VAR}; do kill "$_pid" 2>/dev/null;'
      " done; _left=50;"
      ' while [ "$_left" -gt 0 ]; do _any=;'
      f" for _pid in ${_REAPED_PIDS_VAR}; do"
      ' kill -0 "$_pid" 2>/dev/null && _any=1; done;'
      ' [ -n "$_any" ] || break; _left=$((_left-1)); sleep 0.1; done;'
      f" for _pid in ${_REAPED_PIDS_VAR}; do"
      ' kill -0 "$_pid" 2>/dev/null && kill -9 "$_pid" 2>/dev/null;'
      " done"
      "' EXIT",
  ]


def _reap(pid_var: str) -> str:
  """Return the line registering ``pid_var`` with the single cleanup trap.

  Args:
    pid_var: The shell variable holding the process id.

  Returns:
    One line, prepending the pid so cleanup runs in reverse start order.
  """
  return f'{_REAPED_PIDS_VAR}="${pid_var} ${_REAPED_PIDS_VAR}"'


def _proxy_start_lines(
    *,
    target: str,
    port: int,
    log_name: str,
    own_log_name: str,
    name: str,
    label: str,
) -> list[str]:
  """Return the script lines that start one in-sandbox recording proxy.

  Three things have to be true before the agent may run, and each is a line
  here rather than an assumption:

  - **the proxy is running** — backgrounded, with its own output on a workspace
    file (see ``PROXY_STDERR_NAME``), because an in-sandbox process that fails
    silently leaves no other trace;
  - **it is accepting connections** — polled on the loopback port, not slept
    through. A fixed sleep is a race, and the failure it produces (the agent's
    very first API call refused) reads as an auth or network problem;
  - **it is reaped when the script ends** — it registers with the script's
    single ``EXIT`` trap (see :func:`_reaper_lines`), so the proxy dies on every
    path out, including the guards that ``exit 78`` above it. It does **not**
    install a trap of its own: Bash keeps only the last one installed, and this
    script may also start a correction relay.

  The trap is also why no observer is needed for ordering anymore: by the time
  ``run`` returns, the proxy is gone and its log is closed.

  **If the script is killed instead of exiting** (the caller's timeout fires
  and the container is torn down), the trap never runs and the proxy is killed
  with the container. That truncates the log at a line boundary and loses at
  most the exchange in flight: cc-reverse-proxy appends one JSON record per
  completed exchange and closes the file each time, so every line already
  written is a complete record. A killed run yields *partial* capture, never a
  corrupt file.

  **A run may start more than one.** The actor's calls and the in-sandbox
  supervisor's go to different upstreams, so each gets its own instance, its
  own port and its own log — one function rather than two, because everything
  that makes an instance safe (the readiness poll, the liveness check, the
  registration with the single trap) has to be true of both, and a second copy
  is a second place for one of them to be forgotten.

  Args:
    target: The upstream API base URL to forward to.
    port: The loopback port this instance listens on. Private to the sandbox's
      own network namespace, so two instances need only differ from each other.
    log_name: The workspace file this instance records exchanges into.
    own_log_name: The workspace file this instance's own output goes to.
    name: What this instance is called in the script. It prefixes the shell
      variables holding its pid and its wait counter, which have to be distinct
      per instance: a second start reusing the first's pid variable would leave
      the trap reaping one process twice and the other never.
    label: What this instance records, in the failure messages — the only thing
      a reader of a dead proxy has to tell the two apart. Separate from
      ``name`` because one of them is shell syntax and the other is English.

  Returns:
    The lines, in order.
  """
  binary = shlex.quote(PROXY_BINARY_AT)
  log = f'"$SANDBOX_WORKSPACE"/{log_name}'
  own_log = f'"$SANDBOX_WORKSPACE"/{own_log_name}'
  pid_var = f"{name}_pid"
  wait_var = f"{name}_wait"
  # Bash's /dev/tcp is the only TCP probe that needs nothing installed in the
  # image; `curl` and `nc` are not present in every instance image. A shell
  # without it fails every attempt and hits the loud timeout below rather than
  # running the agent against a proxy nobody confirmed.
  probe = f"(exec 3<>/dev/tcp/127.0.0.1/{port}) 2>/dev/null"
  return [
      (
          f"{binary} --port {port} --target {shlex.quote(target)}"
          f" --output {log} > {own_log} 2>&1 &"
      ),
      f"{pid_var}=$!",
      _reap(pid_var),
      f"{wait_var}=0",
      f"until {probe}; do",
      f'  if ! kill -0 "${pid_var}" 2>/dev/null; then',
      f'    echo "FATAL: the {label} proxy exited; see {own_log_name}" >&2',
      f"    exit {_MISCONFIGURED_EXIT}",
      "  fi",
      f"  {wait_var}=$(({wait_var}+1))",
      f'  if [ "${wait_var}" -ge {_PROXY_READY_ATTEMPTS} ]; then',
      f'    echo "FATAL: the {label} proxy never listened on port'
      f' {port}; see {own_log_name}" >&2',
      f"    exit {_MISCONFIGURED_EXIT}",
      "  fi",
      f"  sleep {_PROXY_READY_INTERVAL_S}",
      "done",
  ]


_RELAY_POLL_INTERVAL_S = 0.1


def _relay_start_lines() -> list[str]:
  """Return the script lines that open the correction channel and hold it.

  The relay is the only thing holding the FIFO's write end, which makes it the
  thing that decides when the run ends. Three properties follow, and each is a
  line here rather than an assumption:

  - **It starts before the agent.** A shell redirect from a FIFO blocks until a
    writer opens the other end, so an agent started first would hang forever.
    The proxy's readiness wait is the precedent.
  - **It closes the write end only on the sentinel.** Closing is the intended
    termination mechanism (the CLI exits on stdin EOF), so it must be produced
    deliberately by whoever decides the task is over.
  - **It is failure-closed.** The unclean marker is written at start and removed
    only on that deliberate close, so anything else that ends the relay — a
    crash, a kill, the container going away — leaves the marker behind. A relay
    that is killed cannot write a marker; it also cannot remove one.

  Returns:
    The lines, in order.
  """
  fifo = f'"$SANDBOX_WORKSPACE"/{CORRECTION_FIFO_NAME}'
  drop = f'"$SANDBOX_WORKSPACE"/{CORRECTION_DROP_NAME}'
  unclean = f'"$SANDBOX_WORKSPACE"/{CORRECTION_UNCLEAN_NAME}'
  log = f'"$SANDBOX_WORKSPACE"/{CORRECTION_RELAY_LOG_NAME}'
  return [
      f"mkdir -p {drop}",
      f"rm -f {fifo}",
      f"mkfifo {fifo}",
      # Present from before the relay exists until it closes on purpose.
      f"touch {unclean}",
      "(",
      # Blocks until the agent opens the read end, which is why this whole
      # subshell is backgrounded and the agent is started after it.
      f"  exec 3> {fifo}",
      # The prompt is just the first message on this channel.
      f'  cat "$SANDBOX_WORKSPACE"/{STREAM_JSON_PROMPT_NAME} >&3',
      f"  while [ ! -e {drop}/{CORRECTION_DONE_NAME} ]; do",
      f"    for message in {drop}/*.json; do",
      '      [ -e "$message" ] || continue',
      '      cat "$message" >&3',
      '      mv "$message" "$message.sent"',
      "    done",
      f"    sleep {_RELAY_POLL_INTERVAL_S}",
      "  done",
      # Drain whatever arrived in the same tick as the sentinel, so a
      # correction and the end of the run cannot race each other away.
      f"  for message in {drop}/*.json; do",
      '    [ -e "$message" ] || continue',
      '    cat "$message" >&3',
      '    mv "$message" "$message.sent"',
      "  done",
      # Cleared **before** the close, not after: closing makes the reader see
      # EOF, the script then exits, and its EXIT trap kills this relay — which
      # would race the removal and leave the marker behind on an ordinary,
      # deliberate ending. The marker means "the relay never saw a deliberate
      # end", so seeing the sentinel is the moment it stops being true.
      f"  rm -f {unclean}",
      "  exec 3>&-",
      f") > {log} 2>&1 &",
      "relay_pid=$!",
      _reap("relay_pid"),
      '  if ! kill -0 "$relay_pid" 2>/dev/null; then',
      f'    echo "FATAL: the correction relay exited before the agent started;'
      f' see {CORRECTION_RELAY_LOG_NAME}" >&2',
      f"    exit {_MISCONFIGURED_EXIT}",
      "  fi",
  ]


def _supervisor_probe_lines() -> list[str]:
  """Return the lines that prove a supervised run can be supervised, or stop.

  Two preconditions, both checked before the actor exists, because the failure
  they prevent is the same one and it is the expensive one: a run that goes
  ahead without a working supervisor ends as an *ordinary* result. An
  unsupervised success is worse than a failure — a failure is discarded, and a
  success is kept as data.

  The binary is asked to answer ``--version`` rather than tested with ``[ -x
  ]``. **A positive premise, not a list of the ways it could be missing:**
  ``-x`` rules out "absent" and lets "present, executable, and not a working
  binary for this image" straight through, so the arm nobody enumerated is
  exactly as green as the arms they did. Answering constrains it to be the
  kind of thing it claims to be, and everything that is not passes no part of
  it.

  The credential is checked for presence only, and only its **name** is ever
  written: it arrives by reference (the sandbox's ``pass_env``), and a check
  that rendered it would put it in the script, the script in the workspace,
  and the workspace in the run's artifacts.

  Returns:
    The lines, in order. The version answer is also the run's record of which
    build supervised it, which the container does not outlive.
  """
  binary = shlex.quote(SUPERVISOR_BINARY_AT)
  info = f'"$SANDBOX_WORKSPACE"/{SUPERVISOR_INFO_NAME}'
  return [
      f'if [ -z "${{{SUPERVISOR_API_KEY_ENV}:-}}" ]; then',
      f'  echo "FATAL: {SUPERVISOR_API_KEY_ENV} is unset or empty; the'
      f" supervisor cannot reach its model. Pass it to the sandbox by"
      f' reference (pass_env)." >&2',
      f"  exit {_MISCONFIGURED_EXIT}",
      "fi",
      f"if ! {binary} --version > {info} 2>&1; then",
      f'  echo "FATAL: the supervisor wrapper at {SUPERVISOR_BINARY_AT} did'
      f' not answer --version; see {SUPERVISOR_INFO_NAME}" >&2',
      f"  exit {_MISCONFIGURED_EXIT}",
      "fi",
  ]


def _supervisor_command(actor_argv: Sequence[str]) -> str:
  """Return the one command line that runs the actor under the wrapper.

  A string rather than an argv, because the two halves need opposite
  treatments and the difference matters:

  - the wrapper's **own** flags name workspace files, and the workspace path is
    a shell variable the sandbox exports. Those have to reach the shell
    unquoted enough to expand, exactly as every other path in this script does;
  - the **actor's** tokens must reach the actor unchanged, so they are
    shell-quoted as a unit. The wrapper executes them as given — it joins
    nothing into a shell command and adds no flags of its own — so what this
    hands over is a handover, not a second construction of them.

  Quoting them the same way would be wrong in one direction or the other: a
  `$SANDBOX_WORKSPACE` inside single quotes arrives at the wrapper literally,
  and an unquoted actor token with a space in it arrives as two.

  Args:
    actor_argv: The actor's command, from
      :meth:`ClaudeCodeHarness.actor_argv`.

  Returns:
    The command line, without redirects.
  """
  flags = {
      "--config": SUPERVISOR_CONFIG_NAME,
      "--actor-event-log": EVENT_STREAM_NAME,
      "--supervisor-log": SUPERVISOR_LOG_NAME,
      "--summary": SUPERVISOR_SUMMARY_NAME,
      "--actor-stderr": AGENT_STDERR_NAME,
      # The prompt travels by path, not on stdin. The wrapper writes these
      # bytes unparsed as the first thing the actor reads and then holds that
      # stdin open, because when it closes is the wrapper's policy to decide —
      # a quiet result closes it, a correction at a result boundary keeps it
      # open — and a plain file's EOF must not decide instead.
      "--actor-prompt": STREAM_JSON_PROMPT_NAME,
  }
  named = " ".join(
      f'{flag} "$SANDBOX_WORKSPACE"/{name}' for flag, name in flags.items()
  )
  return (
      f"{shlex.quote(SUPERVISOR_BINARY_AT)} run {named}"
      f" -- {shlex.join(actor_argv)}"
  )


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
      ``STREAM`` runs with ``--replay-user-messages``, so its trace carries the
      user messages the agent received as well as what it produced; without it
      the CLI echoes no stdin, and the run's own opening prompt is absent from
      the trace it writes (ADR-0017).
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

      **Under** :attr:`segmented` **this field is not used, and the flag means
      something else.** ``--max-turns`` then carries one *segment's* length
      rather than the whole run's ceiling, so the runaway guard moves to
      ``max_segments * turns_per_segment`` — which is why
      :class:`~swe_lab.trace_synthesis.segmented_loop.SegmentedSupervision`
      gives ``max_segments`` no default. A parameter that silently changes
      meaning is the same failure as a comment that has gone stale, so it is
      said here rather than only in the plan.
    max_budget_usd: Optional spend ceiling (``--max-budget-usd``, print mode
      only). Omitted entirely when ``None``.
    subagent_wait_ceiling_ms: Optional ceiling on waiting for background
      subagents (``CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS``). Omitted when
      ``None``, which leaves the agent's own ten-minute default in place.
      Letting the run bound itself yields a clean exit and a complete trace
      where an external kill would truncate mid-write.
    correction_channel: Run the agent with a **live** stdin channel — a FIFO
      fed by an in-sandbox relay from a bind-mounted drop directory — so a
      host-side supervisor can write a correction while the agent is still
      working (ADR-0013). Off by default: it removes the ordinary termination
      mechanism (stdin reaching EOF) and replaces it with a deliberate close,
      which only a caller that owns a supervisor can produce.

      A **field rather than a subclass** on purpose: a supervised rollout must
      differ from an unsupervised one *only* by the corrections, and a forked
      harness is a standing invitation for the two to drift in flags, denied
      tools or capture wiring — drift that would be invisible in the traces it
      produces. That reasoning expires the moment the supervised path needs a
      genuinely different invocation rather than an extended one.
    native_supervision: Run the actor as the **child of the in-sandbox
      supervision wrapper** rather than as the script's own command, so the
      supervisor lives beside the actor instead of on the host (#375). Adds a
      second capture-proxy instance for the supervisor's own model calls, hands
      the wrapper the actor's argv and the prompt's path, and leaves the
      actor's stdout, stderr and stdin to it.

      ``None`` runs the actor directly, which is still the default and still
      what every shipped definition takes: the wrapper is added *beside* the
      host runtime rather than in place of it, until it has run end to end.
      Mutually exclusive with ``correction_channel`` — see
      :meth:`__post_init__`.
    segmented: Cut the run into segments of
      :attr:`~swe_lab.trace_synthesis.segmented_loop.SegmentedSupervision.turns_per_segment`
      turns, consult a policy at each cut, and resume — the second supervision
      carrier (task 22). ``None`` runs the actor once, which is what every
      shipped definition but the segmented arm takes.

      **A field, for the reason stated at** ``correction_channel``: a supervised
      run must differ from an unsupervised one only by the supervision, and a
      forked harness lets them drift in flags, denied tools or capture wiring
      invisibly. Mutually exclusive with both other mechanisms — see
      :meth:`__post_init__`.
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
  correction_channel: bool = False
  native_supervision: NativeSupervision | None = None
  segmented: SegmentedSupervision | None = None

  def __post_init__(self) -> None:
    """Refuse the one configuration in which two components own the actor.

    The native wrapper owns the actor's stdin — that ownership is what lets it
    decide when the run ends, and it is how the prompt reaches the actor. The
    correction channel owns the same stdin from the other side: a FIFO the
    in-sandbox relay holds open. Both at once is two writers to one stdin and
    two answers to when it closes, so it is refused where the two are named
    rather than discovered as a run that ended at a moment neither chose.

    The segmented loop is refused alongside either for a related but distinct
    reason: it decides **when the actor stops and starts**, running the CLI once
    per segment, while both others assume one long-lived process they are
    attached to. Two components deciding when the run ends is the same class of
    configuration as two writing its stdin, so it is refused where the pair is
    named rather than discovered as a run that ended at a moment neither chose.

    Raises:
      ValueError: More than one of the three supervision mechanisms is on.
    """
    if self.native_supervision is not None and self.correction_channel:
      raise ValueError(
          "native_supervision and correction_channel both own the actor's"
          " stdin; the wrapper replaces the FIFO and the relay rather than"
          " running beside them"
      )
    if self.segmented is not None and (
        self.correction_channel or self.native_supervision is not None
    ):
      raise ValueError(
          "segmented supervision runs the actor once per segment and decides"
          " when it stops; correction_channel and native_supervision each"
          " attach to one long-lived actor process, so neither composes with it"
      )

  @property
  def _stdin_is_stream_json(self) -> bool:
    """Whether this run feeds the agent JSON lines rather than a plain file.

    Two independent reasons land on the same wire format. The channel needs it
    because a correction is a message and messages on that stdin are JSON
    lines. ``STREAM`` capture needs it because ``--replay-user-messages`` is
    only accepted alongside stream-json on **both** sides — the pinned 2.1.212
    binary exits 1 with *"--replay-user-messages requires both
    --input-format=stream-json and --output-format=stream-json"*.

    Read by the invocation script and by :meth:`run`, which must agree: the
    script names the stdin and ``run`` is what writes it, so a disagreement is
    an agent reading a file nobody produced.

    Returns:
      Whether the run's stdin carries stream-json.
    """
    return (
        self.correction_channel
        or self.native_supervision is not None
        or self.capture != "proxy"
    )

  @property
  def _narrates_event_stream(self) -> bool:
    """Whether the agent writes its own ``stream-json`` trace to a file.

    Two orthogonal decisions meet here: recording traffic through the proxy
    says nothing about whether the actor should also narrate itself, and a
    supervised run needs both — the proxy log is the trace, and the event
    stream is the only *live* view of the actor there is.

    Read by :meth:`actor_argv`, which picks the output format, and by the
    invocation script, which picks where that output goes. One property rather
    than one condition in each, because the two must agree: an agent narrating
    into ``/dev/null`` is a supervisor with nothing to read.

    Returns:
      Whether the run wants the agent's own event stream.
    """
    return (
        self.capture != "proxy"
        or self.correction_channel
        or self.native_supervision is not None
        # The segmented loop reads each segment's terminal ``result`` event to
        # learn whether the cut was the turn budget or the actor finishing, and
        # that event exists only in the agent's own narration. Under ``PROXY``
        # capture the wire is still the trace; this keeps the loop's instrument
        # alive beside it, exactly as the two mechanisms above do.
        or self.segmented is not None
    )

  @property
  @override
  def accepts_corrections(self) -> bool:
    """Whether this run has the live stdin channel.

    Returns:
      Whether :attr:`correction_channel` is on — the FIFO and the in-sandbox
      relay are the whole of what makes the actor reachable mid-run.
    """
    return self.correction_channel

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

    The native transcript comes **last**, and unconditionally. It is the only
    thing here written by the agent itself rather than by us, so it is what an
    account of ours can be checked against — and it exists only in the
    container's writable layer, so a hook that runs after this one is a hook
    that runs after the record is gone.
    """
    supervision = (
        (NativeSupervisionObserver(),)
        if self.native_supervision is not None
        else ()
    )
    return (
        # First: record which build the sandbox actually got, before anything
        # can go wrong with the run it describes.
        AgentInfoObserver(binary=BINARY_AT, artifact=INFO_ARTIFACT),
        ConversationObserver(producer=self),
        HarnessOutcomeObserver(harness=self),
        # Before the transcript, so a run whose wrapper lost the actor is
        # already marked as such by the time anything reads the trace it left.
        *supervision,
        NativeTranscriptObserver(),
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
    from swe_lab.trace_synthesis.supervisor_binary import (
        ensure_supervisor_binary,
        local_build,
        supervisor_version,
    )

    from .binary import ensure_claude_binary
    from .proxy import ensure_proxy_binary, proxy_source_version

    version = self.version
    agent = AgentAsset(
        path=BINARY_AT,
        version=version,
        fetch=lambda dest: ensure_claude_binary(version=version, dest=dest),
    )
    assets = [agent]
    if self.capture == "proxy":
      assets.append(
          AgentAsset(
              path=PROXY_BINARY_AT,
              # cc-reverse-proxy is a single unversioned Go file in a sibling
              # checkout, so its content hash *is* its release (see the
              # module).
              version=proxy_source_version(),
              fetch=lambda dest: ensure_proxy_binary(dest=dest),
          )
      )
    # Independent of capture: the wrapper runs the actor, which every capture
    # mode needs. Nesting this under the proxy branch left a supervised stream
    # run declaring no wrapper at all — the script would exec a path nothing
    # had placed, and the run would stop at the `--version` probe.
    if self.native_supervision is not None:
      # The version is read off the binary rather than pinned here: there is
      # no release to pin against yet, and asserting a guess would refuse a
      # real artifact. `ensure_supervisor_binary` raises when there is nothing
      # to verify, so this declaration cannot name a version for a wrapper
      # that is not there.
      source = local_build()
      assets.append(
          AgentAsset(
              path=SUPERVISOR_BINARY_AT,
              version=supervisor_version(source) if source else "unreleased",
              fetch=lambda dest: ensure_supervisor_binary(dest=dest),
          )
      )
    return tuple(assets)

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
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
      # Claude Code caps piped stdin at 10 MB and exits non-zero past it. Fail
      # here, where the size is known and the message can say so, rather than
      # staging it and reading the cap back as an opaque agent failure.
      raise SandboxError(
          f"prompt is {len(prompt.encode())} bytes; Claude Code caps piped"
          f" stdin at {MAX_PROMPT_BYTES}"
      )
    self._land_prompt(sb, prompt)
    if self.native_supervision is not None:
      # The supervisor's `task` is the actor's prompt today, and they are two
      # parameters rather than one so that they can stop being: the config
      # states what the judge measures against, and `--actor-prompt` states
      # what the actor was told. Binding them would make changing one change
      # the other.
      sb.write(
          SUPERVISOR_CONFIG_NAME,
          self.native_supervision.config_bytes(task=prompt),
      )
    if env:
      sb.write(AGENT_ENV_NAME, env_exports(env).encode())
    if self.segmented is not None:
      return self._run_segmented(sb, task=prompt, timeout=timeout)
    return sb.run_script(AGENT_SCRIPT_NAME, timeout=timeout)

  def _land_prompt(self, sb: SandboxFs, prompt: str) -> None:
    """Write what the actor is told, in whichever form its stdin takes.

    **No size check here, and that is not an omission.** ``run`` checks the one
    prompt that can reach the 10 MB cap — the opening one, which comes from
    outside. Every later segment prompt is either a rendered
    :class:`~swe_lab.trace_synthesis.supervisor.Intervention`, whose text is
    capped at
    :data:`~swe_lab.trace_synthesis.supervisor.MAX_INTERVENTION_CHARS`, or the
    neutral continue, which is a short constant.

    Args:
      sb: The live sandbox.
      prompt: The text to land.
    """
    sb.write(PROMPT_FILENAME, prompt.encode())
    if self._stdin_is_stream_json:
      # The same prompt, as the run's first stream-json message. Written in
      # addition to the plain file, which stays the human-readable record of
      # what was asked on every path.
      sb.write(STREAM_JSON_PROMPT_NAME, user_event_line(prompt).encode())

  def _run_segmented(
      self, sb: SandboxFs, *, task: str, timeout: float
  ) -> ExecResult:
    """Run the actor in segments, consulting the policy at each cut.

    The loop itself is
    :class:`~swe_lab.trace_synthesis.segmented_loop.SegmentedRun`, which knows
    nothing about sandboxes; everything sandbox-shaped is the closure below.
    Each segment re-stages the invocation script so its argv carries that
    segment's ``--resume``, which keeps :meth:`actor_argv` the one place the
    run's flags are assembled.

    Args:
      sb: The live sandbox.
      task: The opening prompt, which is also what the supervisor is told the
        actor was asked to do.
      timeout: Seconds the whole run may take.

    Returns:
      The last segment's outcome, which is the run's.
    """
    assert self.segmented is not None

    def launch(request: SegmentRequest) -> ExecResult:
      if request.index == 0:
        # One truncation, before the first of the appending redirects. A stale
        # file here would be read as segment 0's own output.
        sb.write(EVENT_STREAM_NAME, b"")
      sb.write(
          AGENT_SCRIPT_NAME,
          self._invocation_script(
              sb.spec.workdir, resume_session_id=request.resume_session_id
          ).encode(),
          executable=True,
      )
      self._land_prompt(sb, request.prompt)
      return sb.run_script(AGENT_SCRIPT_NAME, timeout=request.timeout)

    rows: list[Mapping[str, Any]] = []
    loop = SegmentedRun(
        supervision=self.segmented,
        task=task,
        launch=launch,
        read_stream=lambda: read_text(sb, EVENT_STREAM_NAME),
        log=rows.append,
    )
    try:
      return loop.run(timeout=timeout)
    finally:
      # In a `finally` because the account of a run that raised is the account
      # most worth having: without it a crashed loop leaves no record of which
      # segments it had already cut.
      sb.write(
          SUPERVISOR_LOG_NAME,
          "".join(json.dumps(row) + "\n" for row in rows).encode(),
      )

  @override
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct the run writes into the workspace.

    The trace file depends on the capture strategy: ``STREAM`` writes the
    agent's ``event_stream``; ``PROXY`` records into the proxy log instead.
    Roles carry the payload's format (``.jsonl`` — both traces are
    newline-delimited, one record per line — and ``.log``), so a consumer reads
    the artifact name and knows how to parse it.

    A supervised **proxy** run writes both: the proxy log is its trace, and the
    event stream is what the supervisor read while it ran, which is the only
    record of what the supervisor could see at each moment it decided. On
    ``STREAM`` the one file is both.
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
    if self.capture == "proxy" and self._narrates_event_stream:
      trace |= {"event_stream.jsonl": EVENT_STREAM_NAME}
    if self.native_supervision is not None:
      # The wrapper's own artifacts. The summary is the one a consumer
      # classifies the run from; the rest are what a reader needs when that
      # classification says the run was not accounted for.
      trace |= {
          # What the wrapper was told, kept beside what it did: a reader who
          # has the summary but not the policy it was produced under cannot
          # say what "off track" meant for this run.
          "supervisor_config.json": SUPERVISOR_CONFIG_NAME,
          "supervisor_log.jsonl": SUPERVISOR_LOG_NAME,
          "supervisor_summary.json": SUPERVISOR_SUMMARY_NAME,
          "supervisor_stderr.log": SUPERVISOR_STDERR_NAME,
          "supervisor.info": SUPERVISOR_INFO_NAME,
          "supervisor_proxy_log.jsonl": SUPERVISOR_PROXY_LOG_NAME,
          "supervisor_proxy_stderr.log": SUPERVISOR_PROXY_STDERR_NAME,
      }
    if self.segmented is not None:
      # The loop's own account: one row per segment ending and one per seam
      # decision. It is the only record of where the seams were cut, and a
      # consumer needs those coordinates to locate what a seam fabricated in a
      # corpus that carries no marker (task 22 §6.5).
      trace |= {"supervisor.jsonl": SUPERVISOR_LOG_NAME}
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

  @override
  def usage(self, sb: SandboxFs) -> dict[str, float | int | None]:
    """Report cost and turns, which only the agent's own trace carries.

    ``PROXY`` capture sees API traffic and not the agent's ``result`` events,
    so it reports nothing rather than a number it would have to reconstruct.
    """
    if self.capture == "proxy":
      return {}
    return event_stream_usage(read_text(sb, EVENT_STREAM_NAME))

  def actor_argv(
      self, *, resume_session_id: str | None = None
  ) -> tuple[str, ...]:
    """Return the agent's command as the tokens a process would exec.

    **The one construction of this run's flags.** The invocation script is a
    consumer of these tokens rather than a second place they are assembled,
    which is what lets a process wrapper launch the same actor the script
    would: the native supervision runtime takes an argv after ``--`` and
    executes it as given, joining nothing into a shell command and adding no
    flags of its own (#375). A second construction beside this one would be a
    supervised run differing from an unsupervised one by more than the
    supervision — the drift ``correction_channel`` is a field rather than a
    subclass to avoid.

    Tokens, so nothing here needs a shell to be meaningful: no redirect, no
    variable, no quoting. The run's redirects and its stdin belong to whoever
    runs the tokens — the script, or the wrapper.

    Args:
      resume_session_id: The session a segment resumes, or ``None`` for a run
        that starts one. Only the segmented loop passes it, and it passes it
        **here** rather than assembling a second command beside this one — the
        drift this method's first paragraph exists to prevent.

    Returns:
      The binary's absolute path followed by its flags, in the order the run
      passes them.
    """
    # Always denied: not covered by --dangerously-skip-permissions, and each
    # hangs an unattended run in its own way (see the constant).
    denied = ",".join(UNATTENDED_DENIED_TOOLS)
    argv = [
        BINARY_AT,
        "-p",
        "--model",
        self.model,
        "--output-format",
        *(
            ("stream-json", "--verbose")
            if self._narrates_event_stream
            else ("json",)
        ),
        "--dangerously-skip-permissions",
        "--disallowedTools",
        denied,
        "--effort",
        self.effort,
        # Undocumented in --help on 2.1.220, but accepted — verified against a
        # bogus flag in the same position, which is rejected outright. Under
        # segmentation this bounds one segment rather than the run; see
        # `max_turns`.
        "--max-turns",
        str(
            int(
                self.max_turns
                if self.segmented is None
                else self.segmented.turns_per_segment
            )
        ),
    ]
    if resume_session_id is not None:
      argv += ["--resume", resume_session_id]
    if self.capture != "proxy":
      # Without this the CLI echoes no stdin, so the trace it writes contains
      # what the agent said and not what it was asked — the opening prompt and
      # any mid-run injection are simply absent (ADR-0017). The proxy path
      # needs nothing here: its trace is the wire, which carries both already.
      argv.append("--replay-user-messages")
    if self.bare:
      argv.insert(2, "--bare")
    if self.max_budget_usd is not None:
      argv += ["--max-budget-usd", str(self.max_budget_usd)]
    if self._stdin_is_stream_json:
      argv += ["--input-format", "stream-json"]
    return tuple(argv)

  def _stdin_path(self) -> str:
    """Return the workspace file the agent reads its input from.

    Three sources, one reason each: the FIFO when a supervisor may write to it
    mid-run, the stream-json prompt when the run's stdin is that format but
    nothing will write to it again, and the plain prompt otherwise.

    Returns:
      The shell-quoted path, relative to ``$SANDBOX_WORKSPACE``.
    """
    if self.correction_channel:
      return f'"$SANDBOX_WORKSPACE"/{CORRECTION_FIFO_NAME}'
    if self._stdin_is_stream_json:
      return f'"$SANDBOX_WORKSPACE"/{STREAM_JSON_PROMPT_NAME}'
    return f'"$SANDBOX_WORKSPACE"/{PROMPT_FILENAME}'

  def _invocation_script(
      self, workdir: str, *, resume_session_id: str | None = None
  ) -> str:
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
    (redirected to the event-stream file), and ``--replay-user-messages`` is
    what makes it a whole one — the messages the agent received are echoed into
    it alongside the ones it produced. In ``PROXY`` capture the script also
    owns the recorder: it starts the proxy on the sandbox's own loopback, waits
    for it, points the agent at it via ``ANTHROPIC_BASE_URL``, discards the
    agent's stdout, and reaps the proxy on exit.

    Under :attr:`segmented` the script is re-staged once per segment, so the
    agent's own narration is **appended** rather than truncated: one event
    stream holds the whole run, which is the shape ``event_stream_outcome`` (it
    scans backwards for the last ``result``) and ``event_stream_usage`` (whose
    docstring already states the per-segment aggregation) were written for. The
    loop truncates the file once before the first segment. The in-sandbox
    capture proxy needs nothing here: it opens its output with
    ``O_APPEND|O_CREATE``, so a per-segment restart appends too.

    Args:
      workdir: The repo path (``$WORKDIR``) the agent ``cd``s into.
      resume_session_id: Passed through to :meth:`actor_argv` for a segment
        that resumes; ``None`` on every unsegmented run and on segment 0.

    Returns:
      The bash script text staged as the invocation mount.

    """
    config_dir = shlex.quote(f"{AGENT_HOME}/.claude")
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
    # One cleanup owner, before anything is backgrounded: two helpers each
    # installing their own `EXIT` trap would leave only the later one, and the
    # proxy-plus-channel configuration installs both.
    lines += _reaper_lines()
    # Two orthogonal decisions, deliberately not one branch: **where the API
    # calls go** and **what the CLI prints**. Recording traffic through the
    # proxy says nothing about whether the actor should also narrate itself,
    # and a supervised run needs both — the proxy log is the trace, and the
    # event stream is the only *live* view of the actor there is.
    if self.capture == "proxy":
      lines += _proxy_start_lines(
          target=self.proxy_target,
          port=PROXY_PORT,
          log_name=PROXY_LOG_NAME,
          own_log_name=PROXY_STDERR_NAME,
          name="proxy",
          label="capture",
      )
      lines.append(f"export ANTHROPIC_BASE_URL={PROXY_BASE_URL}")
    if self.native_supervision is not None:
      # The supervisor's own upstream, terminated inside the sandbox: the
      # wrapper carries no TLS and refuses an https:// base URL, so it speaks
      # plain HTTP to this instance and this instance speaks TLS to OpenRouter.
      lines += _proxy_start_lines(
          target=OPENROUTER_API,
          port=SUPERVISOR_PROXY_PORT,
          log_name=SUPERVISOR_PROXY_LOG_NAME,
          own_log_name=SUPERVISOR_PROXY_STDERR_NAME,
          name="supervisor_proxy",
          label="supervisor",
      )
      # The endpoint is ours to state; the credential is passed by reference
      # into the sandbox and is never named here with a value.
      lines.append(
          f"export {SUPERVISOR_BASE_URL_ENV}={SUPERVISOR_PROXY_BASE_URL}"
      )
      lines += _supervisor_probe_lines()
    if self._narrates_event_stream:
      # Streamed, and to a file: a supervisor reads this while the actor is
      # still running, so it has to exist during the run rather than be
      # reconstructed after it.
      redirect = ">>" if self.segmented is not None else ">"
      capture_redirect = f'{redirect} "$SANDBOX_WORKSPACE"/{EVENT_STREAM_NAME}'
    else:
      # An unsupervised proxy run has no reader for the agent's own stdout —
      # the trace comes from the proxy log — so it is discarded.
      capture_redirect = "> /dev/null"
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

    if self.correction_channel:
      # Before the agent: a redirect from a FIFO blocks until a writer opens
      # the other end, so an agent started first would wait forever.
      lines += _relay_start_lines()
    stdin_source = self._stdin_path()

    exit_file = f'"$SANDBOX_WORKSPACE"/{AGENT_EXIT_CODE_NAME}'
    if self.native_supervision is not None:
      # No stdin redirect at all: the wrapper owns the actor's stdin and is
      # handed the prompt by path. Its own output is kept apart from the
      # actor's, which it writes itself.
      command = (
          f"{_supervisor_command(self.actor_argv())}"
          f' > "$SANDBOX_WORKSPACE"/{SUPERVISOR_STDERR_NAME} 2>&1'
      )
    else:
      # Feed the prompt on stdin (``-p`` with no argument reads it) rather
      # than inlining it into the argv — no shell-quoting hazard for a large,
      # arbitrary prompt.
      command = (
          f"{shlex.join(self.actor_argv(resume_session_id=resume_session_id))}"
          f" < {stdin_source} {capture_redirect} 2> {stderr}"
      )
    lines += [
        f"cd {shlex.quote(workdir)}",
        command,
        *status_tail(exit_file),
    ]
    return "\n".join(lines) + "\n"
