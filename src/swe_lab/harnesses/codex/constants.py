"""In-container paths and workspace file names for the ``codex`` harness.

Single source of truth for every literal the invocation script and the trace
converter share, mirroring the ``claude_code`` harness's module of the same
name. The native output names are harness-owned; the prompt name is this
harness's own choice of where a caller-supplied prompt lands (ADR-0007 §8).
"""

from __future__ import annotations

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

# A writable home for the agent inside the container. Codex reads and writes
# its own state under `CODEX_HOME` (auth, sessions, history); instance images
# run as root with no guaranteed-writable home, so the invocation script points
# it somewhere it may actually write. Ephemeral, not a workspace file.
AGENT_HOME = "/codex-home"

# Codex's own name for the variable above. Relocating the whole config dir with
# one variable is what lets a caller supply credentials as a *directory*
# (`auth.json` lives in it) rather than as an env var.
CODEX_HOME_ENV = "CODEX_HOME"

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

# **No default model.** The set of models a Codex install may use depends on
# the account behind it — measured 2026-08-08, a ChatGPT login rejects an
# API-tier model outright ("The '<model>' model is not supported when using
# Codex with a ChatGPT account", HTTP 400) and the whole run fails. So the
# harness omits `--model` unless a caller pins one, letting Codex pick what the
# account actually allows; a sweep that needs reproducibility pins it
# explicitly and takes responsibility for it being valid there.
DEFAULT_MODEL: str | None = None

# The API-key env var Codex authenticates with when an API key (rather than a
# ChatGPT login) is used. A ChatGPT login instead lives in `auth.json` under
# CODEX_HOME, which is a *file*, so it is supplied by mount rather than by env.
API_KEY_ENV = "OPENAI_API_KEY"

# The credential file Codex reads from `CODEX_HOME`. Named here because the
# composition that supplies a login has to stage it under exactly this name.
AUTH_FILENAME = "auth.json"
