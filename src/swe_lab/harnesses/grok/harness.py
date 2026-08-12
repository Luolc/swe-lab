"""The ``grok`` harness: run Grok Build headless in the sandbox.

Stages its invocation script, runs the agent, and converts the
``streaming-messages-json`` trace — which is Claude Code's stream-json schema,
measured — into a canonical ``Conversation``. Dataset-agnostic:
``run(prompt=...)`` receives the dataset-derived prompt as text and lands it in
a file of this harness's own choosing; grok reads that file natively via
``--prompt-file``, so there is no stdin plumbing at all.

The **binary is not this harness's to place**: it invokes grok at the agreed
absolute path (:data:`~swe_lab.harnesses.grok.constants.BINARY_AT`) and each
backend's own observer puts it there the way that backend can (ADR-0003).
Like Codex there is no bundle and no launcher — the Linux build is statically
linked musl (task-29 §1) — and unlike Codex there is exactly **one** binary.
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
)
from swe_lab.harnesses.observer import HarnessOutcomeObserver
from swe_lab.sandbox import (
    AgentAsset,
    ExecResult,
    Inline,
    Mount,
    Mounts,
    SandboxFs,
    SandboxObserver,
)

from .constants import (
    AGENT_ENV_NAME,
    AGENT_EXIT_CODE_NAME,
    AGENT_HOME,
    AGENT_SCRIPT_NAME,
    AGENT_STDERR_NAME,
    BINARY_AT,
    DEFAULT_EFFORT,
    DEFAULT_MAX_TURNS,
    DEFAULT_MODEL,
    Effort,
    EVENT_STREAM_NAME,
    grok_config_dir,
    INFO_ARTIFACT,
    PROMPT_FILENAME,
)
from .convert import event_stream_outcome, event_stream_to_conversation

_logger = logging.getLogger(__name__)

# Generous: `--version` on a cold 164 MB binary is mostly process start-up.
_INFO_TIMEOUT_S = 60.0

# The doors a repo under test could use to steer the agent, each with a real
# switch (task-29 §6). The AGENTS.md door has NO switch — it is handled by
# detection, not prevention (see GrokHarness.bare).
_BARE_FLAGS = (
    "--no-plan",
    "--no-subagents",
    "--no-memory",
    "--disable-web-search",
)


@dataclass(frozen=True)
class GrokHarness(Harness):
  """The Grok Build agent as a sandbox-engine harness plug.

  Attributes:
    model: The ``--model`` id to run, or ``None`` to omit the flag and defer
      to the build. Pinned by default (the measured default of the pinned
      build) so a sweep is reproducible.
    effort: Reasoning effort, passed as ``--reasoning-effort`` — a real flag
      here, unlike Codex. ``high`` by default for the house reason; ``None``
      omits it.
    max_turns: Agent-loop runaway guard (``--max-turns``). Grok has this flag
      — which is what makes ``AgentOutcome.MAX_TURNS`` reachable for this
      harness, unlike Codex.
    agent_home: In-container ``HOME``. Grok derives its config dir from it as
      ``$HOME/.grok`` (there is no relocation variable), which is where a
      staged OAuth login lands and where grok writes its own state.
    bare: Close every door the *repo under test* could use to steer the agent
      **that has a switch**: plan mode, subagents, cross-session memory, and
      web search/fetch (an egress door ADR-0010 wants shut anyway). **On by
      default**, like the other harnesses.

      **What it cannot close, stated loudly**: grok injects a repo's
      ``AGENTS.md`` as a prepended user message, and no flag or config key
      gates it (task-29 §6 — ``--system-prompt-override`` cannot remove it,
      and the compat cells gate only the ``.claude``/``.cursor``/``.codex``
      vendor surfaces). The mitigation is **detection**: unlike Claude Code's
      reminders, the injected message is visible in the trace this harness
      captures, so a steered run is auditable from the conversation record.
    base_url: Override the xAI API base URL (``--xai-api-base-url``) — a plain
      flag, so Codex's provider machinery has no counterpart here. The API key
      still travels only by ``pass_env`` (the flag carries the URL, never the
      key). ``None`` keeps the default endpoint.
    extra_flags: Appended verbatim after everything the fields render — the
      escape hatch for a flag that has no field, mirroring codex's
      ``extra_config``.
  """

  model: str | None = DEFAULT_MODEL
  effort: Effort | None = DEFAULT_EFFORT
  max_turns: int = DEFAULT_MAX_TURNS
  agent_home: str = AGENT_HOME
  bare: bool = True
  base_url: str | None = None
  extra_flags: tuple[str, ...] = ()

  @property
  @override
  def name(self) -> str:
    """This harness's identifier; namespaces its artifacts."""
    return "grok"

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Return the info recorder plus the generic conversation/outcome pair.

    This harness's own choice (ADR-0007 §3): the pair are generic building
    blocks that delegate back to ``to_conversation`` / ``outcome`` /
    ``native_outputs``, which is where everything grok-specific lives.
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
    """Declare the pinned binary at ``BINARY_AT``.

    One file, no companion — the Codex trap has no analogue here (task-29 §3).
    """
    from .binary import ensure_grok_binary

    return (
        AgentAsset(
            path=BINARY_AT,
            materialize=lambda dest: ensure_grok_binary(dest=dest),
        ),
    )

  @override
  def mounts(self, workdir: str) -> Mounts:
    """Stage the invocation script and its env file — and nothing else.

    The binary is deliberately absent: it is machinery, not this run's
    material, and the backend provisions it at ``BINARY_AT``.

    The env file is staged **empty**: the script always sources it, and
    ``run(env=...)`` fills it in, so injected variables need no second version
    of the script.

    Args:
      workdir: The repo path the invocation script points the agent at.

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
        (ADR-0007 §8), which grok reads natively via ``--prompt-file`` — no
        stdin, no argv-quoting hazard.
      timeout: Seconds before the agent run is killed.
      env: Extra ``KEY=VALUE`` exports for the agent, written into the sourced
        env file so they apply after the script's own defaults. A name that is
        not a shell identifier raises ``SandboxError`` (from
        :func:`_env_exports`) rather than corrupting the file and skipping the
        run.

    Returns:
      The agent script's outcome. The script always exits 0, so this carries
      only whether *we* killed it on timeout; the agent's own status is
      written to ``grok.exit_code`` in the workspace.
    """
    sb.write(PROMPT_FILENAME, prompt.encode())
    if env:
      sb.write(AGENT_ENV_NAME, env_exports(env).encode())
    return sb.run_script(AGENT_SCRIPT_NAME, timeout=timeout)

  @override
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct the run writes into the workspace.

    Roles carry the payload's format (``.jsonl`` — one event per line), so a
    consumer reads the artifact name and knows how to parse it.
    """
    return {
        "event_stream.jsonl": EVENT_STREAM_NAME,
        "stderr.log": AGENT_STDERR_NAME,
        "exit_code.txt": AGENT_EXIT_CODE_NAME,
    }

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    """Convert the run's captured event stream into a ``Conversation``."""
    return event_stream_to_conversation(read_text(sb, EVENT_STREAM_NAME))

  @override
  def outcome(self, sb: SandboxFs) -> AgentOutcome:
    """Classify the ending from the run's own event stream.

    An absent trace reads as ``NO_OUTPUT`` (``_read_text`` is
    absence-tolerant), so a crashed run reports an outcome rather than
    raising.
    """
    return event_stream_outcome(read_text(sb, EVENT_STREAM_NAME))

  def _invocation_script(self, workdir: str) -> str:
    """Build the run script for an *unattended* run.

    The invariants it enforces:

    - **Approvals are bypassed** (``--permission-mode bypassPermissions``) —
      the container is the sandbox, the same argument as the other harnesses.
    - **The prompt arrives by file** (``--prompt-file``), grok's native
      mechanism; no stdin, no argv quoting.
    - **The trace is the agent's own JSONL stdout**
      (``streaming-messages-json``), redirected to the event-stream file.
    - **No leader process** (``--no-leader``): the leader is a shared backend
      for multiple interactive clients; a one-shot container run must not
      leave a socket-holding daemon behind the exec.
    - **Turns are bounded** (``--max-turns``), so an agent loop cannot run
      away — and its exhaustion is a *distinct, non-retryable* outcome
      (ADR-0011).
    - **The exit status is reported out-of-band**; the script itself always
      exits 0 so teardown is unchanged (143 = SIGTERM).
    - **Wall-clock is the caller's**, deliberately not here.

    Args:
      workdir: The repo path (``--cwd``) the agent works in.

    Returns:
      The bash script text staged as the invocation mount.
    """
    home = shlex.quote(self.agent_home)
    grok_dir = shlex.quote(grok_config_dir(self.agent_home))
    binary = shlex.quote(BINARY_AT)
    prompt = f'"$SANDBOX_WORKSPACE"/{PROMPT_FILENAME}'
    stderr = f'"$SANDBOX_WORKSPACE"/{AGENT_STDERR_NAME}'
    event_stream = f'"$SANDBOX_WORKSPACE"/{EVENT_STREAM_NAME}'
    lines = [
        "set -u",
        # Instance images run as root with no guaranteed-writable home, so the
        # agent gets one; grok derives its config dir from it ($HOME/.grok),
        # which is also where a staged OAuth login lands. `mkdir -p` on the
        # nested path creates both.
        f"export HOME={home}",
        f"mkdir -p {grok_dir}",
        # Caller-injected env (empty unless ``run(env=...)`` filled it in).
        # Sourced after the defaults above so a caller can override them.
        f'. "$SANDBOX_WORKSPACE"/{AGENT_ENV_NAME}',
    ]
    flags = [
        f"--prompt-file {prompt}",
        "--output-format streaming-messages-json",
        "--permission-mode bypassPermissions",
        "--no-leader",
        f"--cwd {shlex.quote(workdir)}",
        f"--max-turns {int(self.max_turns)}",
    ]
    if self.model is not None:
      flags.append(f"--model {shlex.quote(self.model)}")
    if self.effort is not None:
      flags.append(f"--reasoning-effort {self.effort}")
    if self.bare:
      flags.extend(_BARE_FLAGS)
    if self.base_url is not None:
      flags.append(f"--xai-api-base-url {shlex.quote(self.base_url)}")
    flags.extend(self.extra_flags)

    exit_file = f'"$SANDBOX_WORKSPACE"/{AGENT_EXIT_CODE_NAME}'
    lines += [
        (f"{binary} {' '.join(flags)} > {event_stream} 2> {stderr}"),
        # The agent's real status, reported out-of-band. `set -u` is on but
        # `set -e` is not, so execution continues here; capturing $? on the
        # very next line and then exiting 0 keeps container teardown unchanged
        # while still telling a caller success from failure from a kill.
        f"printf '%s\\n' \"$?\" > {exit_file}",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"
