"""In-container paths and workspace file names for the ``codex`` harness.

Single source of truth for every literal the invocation script and the trace
converter share, mirroring the ``claude_code`` harness's module of the same
name. The native output names are harness-owned; the prompt name is this
harness's own choice of where a caller-supplied prompt lands (ADR-0007 §8).
"""

from __future__ import annotations

from typing import Literal

# The pinned Codex binary — a read-only asset at a fixed path, invoked by
# absolute path (not via PATH). Unlike Claude Code's, this is the upstream
# binary itself and not a launcher: the Linux build is statically linked musl,
# so it needs no loader indirection and no bundled libraries (task-28 §1).
BINARY_AT = "/opt/codex/codex"

# The code-mode host, which Codex spawns to execute commands and apply patches.
# Its path is **derived by Codex as a sibling of the binary above**, so this is
# not a free choice — the two must land in one directory. Without it a run
# still starts, authenticates and answers, but can neither run a command nor
# edit a file, and it exits 0 anyway (measured on 0.147.0, 2026-08-08).
CODE_MODE_HOST_AT = "/opt/codex/codex-code-mode-host"

# A writable HOME for the agent inside the container — deliberately the same
# path the ``claude_code`` harness uses, for the same reason: instance images
# run as root with no guaranteed-writable home. Ephemeral, not a workspace file.
AGENT_HOME = "/agent-home"

# Codex's config dir lives *under* the home, at its own default `$HOME/.codex`
# — it is not the home itself. Pointing `CODEX_HOME` at a bare home directory
# would scatter Codex's state (auth, sessions, history, caches) directly over
# `$HOME` and diverge from the layout of every ordinary install, which is
# exactly the kind of difference that makes a sandboxed run stop reproducing
# what a developer sees locally.
CODEX_DIR_NAME = ".codex"

# Codex's own name for the variable that relocates the dir above. Relocating a
# whole *directory* with one variable is what lets a caller supply credentials
# as a file (`auth.json` lives in it) rather than as an env var.
CODEX_HOME_ENV = "CODEX_HOME"


def codex_config_dir(agent_home: str = AGENT_HOME) -> str:
  """Return the ``CODEX_HOME`` for a given agent home — always ``$HOME/.codex``.

  Derived rather than configured separately so the two cannot drift: a home and
  a config dir that disagree would put the staged ``auth.json`` somewhere Codex
  does not read, which surfaces as an authentication failure minutes into a
  run rather than as a misconfiguration.

  Args:
    agent_home: The in-container ``HOME``.

  Returns:
    The config directory path.
  """
  return f"{agent_home.rstrip('/')}/{CODEX_DIR_NAME}"


# The invocation script the harness stages and runs by its workspace path; it
# drives Codex in headless (``exec``) mode.
AGENT_SCRIPT_NAME = "run_codex.sh"

# Caller-injected environment, sourced by the invocation script. Staged empty
# and rewritten by ``run(env=...)``, so the exports land inside the script's env
# setup without re-staging the script.
AGENT_ENV_NAME = "agent_env.sh"

# Where this harness lands the prompt it receives as a string (ADR-0007 §8).
# `codex exec` reads the prompt from stdin when given `-`.
PROMPT_FILENAME = "prompt.txt"

# The agent's real exit status, reported out-of-band. The invocation script
# always exits 0 (container teardown must not change), so this file is the only
# way to tell success from failure from a caller-initiated kill. 143 = SIGTERM.
AGENT_EXIT_CODE_NAME = "codex.exit_code"

# Native outputs the run writes into the workspace (registered as artifacts).
EVENT_STREAM_NAME = "codex.event_stream.jsonl"  # `exec --json` trace (primary)
AGENT_STDERR_NAME = "codex.stderr.log"  # the run's stderr log

# `codex exec -o` writes the final assistant message here. Cheap, and it is the
# one thing the JSONL stream states twice over (as an `agent_message` item), so
# it doubles as a corruption check on the trace.
LAST_MESSAGE_NAME = "codex.last_message.txt"

# The agent's own account of itself (`--version` + `exec --help`), captured once
# the sandbox is up. See the claude_code harness's observer for why: which build
# actually ran is the first question anyone asks, and the sandbox is gone by the
# time they ask it.
AGENT_INFO_NAME = "codex.info"
INFO_ARTIFACT = "codex.info"

# The model a run pins unless told otherwise. Pinning matters — a sweep whose
# model floats is not reproducible — but it is **account-sensitive**: measured
# 2026-08-08, a ChatGPT login rejects an API-tier model outright ("The
# '<model>' model is not supported when using Codex with a ChatGPT account",
# HTTP 400) and the whole run fails before its first turn. So a caller whose
# account offers a different set must override this, and `None` is accepted to
# mean "omit `--model` and let Codex choose what the account allows".
DEFAULT_MODEL: str | None = "gpt-5.6-sol"

type Effort = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"
]
"""Reasoning effort, as Codex's own ``ReasoningEffort`` enumerates it.

Typed rather than a bare ``str`` for the reason the ``claude_code`` harness
types its own: this arrives as a *config override*, and Codex treats an
unparseable one as a literal string rather than refusing it — so a typo would
quietly run a whole sweep at the wrong effort instead of failing loudly.
"""

# Codex exposes reasoning effort as a config key, not a flag (`codex exec
# --help` has no `--effort`), so the harness passes it through `-c`.
EFFORT_CONFIG_KEY = "model_reasoning_effort"

# Codex's own default is `medium`. High is pinned here for the same reason the
# claude_code harness pins its own: an unattended solve is the case worth
# spending on, and a floating default makes two sweeps incomparable.
DEFAULT_EFFORT: Effort | None = "high"

# The API-key env var Codex authenticates with when an API key (rather than a
# ChatGPT login) is used. A ChatGPT login instead lives in `auth.json` under
# CODEX_HOME, which is a *file*, so it is supplied by mount rather than by env.
API_KEY_ENV = "OPENAI_API_KEY"

# The credential file Codex reads from `CODEX_HOME`. Named here because the
# composition that supplies a login has to stage it under exactly this name.
AUTH_FILENAME = "auth.json"
