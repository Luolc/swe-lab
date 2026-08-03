"""In-container paths and workspace file names for the ``claude_code`` harness.

Single source of truth for every literal the invocation script and the trace
converter share. The native output names are harness-owned; the prompt name is
the shared solve-input convention the harness *reads* while the dataset and
composition *write* it (the prompt is dataset-derived).
"""

from __future__ import annotations

# The pinned native Claude Code binary — a read-only asset at a fixed path,
# invoked by absolute path (not via PATH).
BINARY_AT = "/opt/claude-code/claude"

# A writable HOME for the agent inside the container (instance images run as
# root with no guaranteed-writable home; the binary wants a config dir). Set in
# the invocation script — ephemeral, not a workspace file.
AGENT_HOME = "/agent-home"

# The invocation script the harness stages and runs by its workspace path; it
# drives Claude Code in headless (``-p``) mode.
AGENT_SCRIPT_NAME = "run_claude_code.sh"

# Caller-injected environment, sourced by the invocation script. Staged empty
# and rewritten by ``run(env=...)``, so the exports land inside the script's env
# setup (and stay visible in the workspace) without re-staging the script.
AGENT_ENV_NAME = "agent_env.sh"

# Where this harness lands the prompt it receives as a string (ADR-0007 §8):
# ``run(prompt=...)`` writes it here itself, then the invocation script feeds
# it to the agent on stdin. This agent's own choice of filename — no
# composition-level convention exists anymore.
PROMPT_FILENAME = "prompt.txt"

# Native outputs the run writes into the workspace (registered as artifacts).
EVENT_STREAM_NAME = "claude.event_stream.jsonl"  # stream-json trace (primary)
AGENT_STDERR_NAME = "claude.stderr.log"  # the run's stderr log

# The proxy-capture trace: the cc-reverse-proxy appends one request/response
# record per API call here. The proxy is a host process, but the composition
# points it at the (shared) workspace so the log is a normal workspace artifact.
PROXY_LOG_NAME = "claude.proxy.jsonl"

# The host, as seen from inside the container, that the Docker host-gateway
# resolves to — how a containerized agent reaches a host-side proxy. The host
# backend maps it via ``--add-host`` on every container.
CONTAINER_PROXY_HOST = "host.docker.internal"

DEFAULT_MODEL = "claude-sonnet-5"

# The subscription OAuth token the agent reads from its env; the rollout backend
# passes it by reference (never in the docker argv).
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# The API-key env var Claude Code authenticates with when an API key (not the
# subscription OAuth token) is used — required by ``--bare``, which disables
# OAuth. Set by the harness for the agent exec when an ``api_key`` is given.
API_KEY_ENV = "ANTHROPIC_API_KEY"
