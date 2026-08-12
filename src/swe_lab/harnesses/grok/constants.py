"""In-container paths and workspace file names for the ``grok`` harness.

Single source of truth for every literal the invocation script and the trace
converter share, mirroring the ``codex`` harness's module of the same name.
"""

from __future__ import annotations

from typing import Literal

# The pinned Grok Build binary — a read-only asset at a fixed path, invoked by
# absolute path (not via PATH). Like Codex's, the Linux build is statically
# linked musl, so it needs no loader indirection and no bundled libraries
# (task-29 §1); unlike Codex, there is exactly ONE binary — no companion like
# `codex-code-mode-host` exists, verified by a live tool-using run (§9.3).
BINARY_AT = "/opt/grok/grok"

# A writable HOME for the agent inside the container — the same path the other
# harnesses use, for the same reason: instance images run as root with no
# guaranteed-writable home. Ephemeral, not a workspace file.
AGENT_HOME = "/agent-home"

# Grok derives its config dir from `$HOME/.grok` (source: `grok_home()`); it
# has no CODEX_HOME-style relocation variable, so the dir is fixed by HOME and
# this constant only *names* the derivation for the auth observer.
GROK_DIR_NAME = ".grok"


def grok_config_dir(agent_home: str = AGENT_HOME) -> str:
  """Return grok's config dir for a given agent home — always ``$HOME/.grok``.

  Derived rather than configured so the harness and the auth observer cannot
  disagree about where grok will look; a staged ``auth.json`` landing anywhere
  else surfaces as an authentication failure minutes into a run.

  Args:
    agent_home: The in-container ``HOME``.

  Returns:
    The config directory path.
  """
  return f"{agent_home.rstrip('/')}/{GROK_DIR_NAME}"


# The invocation script the harness stages and runs by its workspace path; it
# drives grok in headless (``-p`` / ``--prompt-file``) mode.
AGENT_SCRIPT_NAME = "run_grok.sh"

# Caller-injected environment, sourced by the invocation script. Staged empty
# and rewritten by ``run(env=...)``, so the exports land inside the script's
# env setup without re-staging the script.
AGENT_ENV_NAME = "agent_env.sh"

# Where this harness lands the prompt it receives as a string (ADR-0007 §8).
# Grok reads it natively via `--prompt-file`, so there is no stdin plumbing.
PROMPT_FILENAME = "prompt.txt"

# The agent's real exit status, reported out-of-band. The invocation script
# always exits 0 (container teardown must not change), so this file is the only
# way to tell success from failure from a caller-initiated kill. 143 = SIGTERM.
AGENT_EXIT_CODE_NAME = "grok.exit_code"

# Native outputs the run writes into the workspace (registered as artifacts).
# The trace is `--output-format streaming-messages-json` on stdout — measured
# to be Claude Code's stream-json schema (task-29 §4), one event per line.
EVENT_STREAM_NAME = "grok.event_stream.jsonl"
AGENT_STDERR_NAME = "grok.stderr.log"

# The agent's own account of itself (`--version` + `--help`), captured once the
# sandbox is up. Which build actually ran is the first question anyone asks,
# and the sandbox is gone by the time they ask it.
AGENT_INFO_NAME = "grok.info"
INFO_ARTIFACT = "grok.info"

# The model a run pins unless told otherwise — the measured default of the
# pinned 1.0.0 build (`grok models`: "Default model: grok-4.5"). Pinning
# matters because a sweep whose model floats is not reproducible; `None` omits
# `--model` and defers to the build.
DEFAULT_MODEL: str | None = "grok-4.5"

type Effort = Literal["low", "medium", "high"]
"""Reasoning effort, as ``--reasoning-effort`` accepts it.

Typed rather than a bare ``str`` for the house reason: a typo silently running
a sweep at the wrong effort is worse than a loud refusal. The three values are
the ones grok's own TUI model picker exposes; the flag itself is undocumented
about its domain, so an unknown value fails at grok's argv parsing — loudly,
which is what we want. (Unverified beyond these three; widen with evidence.)
"""

# High by default, the house rule: an unattended solve is the case worth
# spending on, and a floating default makes two sweeps incomparable.
DEFAULT_EFFORT: Effort | None = "high"

# Agent-loop runaway guard, passed as `--max-turns`. Unlike Codex, grok has
# this flag — which makes AgentOutcome.MAX_TURNS reachable for this harness.
DEFAULT_MAX_TURNS = 500

# The API-key env var grok authenticates with when an API key (rather than an
# OAuth login) is used. An OAuth login instead lives in `auth.json` under
# `$HOME/.grok`, which is a *file*, so it is supplied by mount rather than env.
API_KEY_ENV = "XAI_API_KEY"

# The credential file grok reads from `$HOME/.grok`. Named here because the
# composition that supplies a login has to stage it under exactly this name.
AUTH_FILENAME = "auth.json"
