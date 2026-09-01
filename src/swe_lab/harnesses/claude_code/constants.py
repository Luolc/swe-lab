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

# The agent's CONFIG root inside the container — no longer exported as HOME
# (#240): HOME defers to the image (warm toolchain caches live under it), and
# the config dir is pinned here via CLAUDE_CONFIG_DIR instead, so an image
# cannot inject instructions through $HOME/.claude.json or $HOME/.claude
# (measured: a planted "begin every reply with BANANA" CLAUDE.md under the
# image's HOME is not read once the config dir is pinned). Fresh per run,
# since containers are.
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

# The agent's real exit status, reported out-of-band. The invocation script
# always exits 0 (container teardown must not change), so this file is the only
# way to tell success from failure from a caller-initiated kill. 143 = SIGTERM.
AGENT_EXIT_CODE_NAME = "claude.exit_code"

# Interactive / plan-mode tools, always denied for an unattended run. They are
# not covered by --dangerously-skip-permissions: EnterPlanMode needs no
# permission at all, and ExitPlanMode / AskUserQuestion ask for a *reply*, which
# never arrives and never times out. Bare names, no parenthesized specifier.
#
# Probed against the bundled 2.1.220 (init event's `tools` array, both with and
# without the rule, under ENABLE_TOOL_SEARCH=false):
#
#   - **none of these three is offered** — absent under
#     --dangerously-skip-permissions, --permission-mode plan, and
#     --permission-mode acceptEdits alike. So on this version the rule is inert.
#   - the rule itself works: adding `WebSearch` (which *is* offered) to the list
#     removes it from the array. So an empty diff here means "nothing to strip",
#     not "the flag is ignored" — do not read it as the flag being broken.
#
# Kept anyway: it costs one argument, it was load-bearing on 2.1.185 where
# EnterPlanMode was observed self-invoked, and the pinned version moves.
UNATTENDED_DENIED_TOOLS = ("EnterPlanMode", "ExitPlanMode", "AskUserQuestion")

# Claude Code caps piped stdin at 10 MB (v2.1.128+) and exits non-zero past it.
MAX_PROMPT_BYTES = 10 * 1024 * 1024

# Native outputs the run writes into the workspace (registered as artifacts).
EVENT_STREAM_NAME = "claude.event_stream.jsonl"  # stream-json trace (primary)
AGENT_STDERR_NAME = "claude.stderr.log"  # the run's stderr log

# The agent's own account of itself (`--version` + `--help`), captured once the
# sandbox is up. `AGENT_INFO_NAME` is the workspace file; `INFO_ARTIFACT` is the
# name it is registered under, which is what a reader of a persisted manifest
# sees. They coincide here — unlike the trace and stderr, whose in-sandbox names
# are namespaced `claude.*` while their artifact names are not — but they stay
# separate constants because they answer different questions.
AGENT_INFO_NAME = "claude.info"
INFO_ARTIFACT = "claude.info"

# The proxy-capture trace: the cc-reverse-proxy appends one request/response
# record per API call here. The proxy runs *inside* the sandbox and writes
# straight to the workspace, so the log is a normal workspace artifact.
#
# One record per line, appended and flushed as each exchange completes, which
# is what makes an interrupted run *partially readable* rather than corrupt: a
# proxy killed mid-stream truncates the file at a line boundary and every line
# already written stays a complete record.
PROXY_LOG_NAME = "claude.proxy.jsonl"

# The proxy's own stdout/stderr. Its banner, per-request lines and any upstream
# error land here — the only account of *why* capture failed when it does, and
# unrecoverable once the sandbox is gone (a missing CA bundle in the instance
# image, say, is invisible from the agent's side: it just cannot connect).
PROXY_STDERR_NAME = "claude.proxy.log"

# The pinned cc-reverse-proxy build, placed like the agent binary: a read-only
# executable asset at a fixed absolute path, outside the workspace because it
# is machinery rather than the run's material.
PROXY_BINARY_AT = "/opt/cc-reverse-proxy/cc-reverse-proxy"

# The port the in-sandbox proxy listens on, and the URL the agent dials to
# reach it. **A constant, deliberately.** The sandbox has its own network
# namespace, so this port is private to one run and cannot collide with
# another run, with the host, or with anything else on the machine — which is
# the whole reason the proxy moved inside. Its predecessor derived a host port
# from a dataset index, needed a firewall rule to be reachable from the
# container, and was exposed to every node on the host's tailnet.
# (cc-reverse-proxy's own default port, so a manual run matches.)
PROXY_PORT = 9527
PROXY_BASE_URL = f"http://127.0.0.1:{PROXY_PORT}"

# The upstream the proxy forwards to unless a run says otherwise.
ANTHROPIC_API = "https://api.anthropic.com"

DEFAULT_MODEL = "claude-sonnet-5"

# The subscription OAuth token the agent reads from its env; the rollout backend
# passes it by reference (never in the docker argv).
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# The API-key env var Claude Code authenticates with when an API key (not the
# subscription OAuth token) is used — required by ``--bare``, which disables
# OAuth. Set by the harness for the agent exec when an ``api_key`` is given.
API_KEY_ENV = "ANTHROPIC_API_KEY"
