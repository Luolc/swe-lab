"""In-container paths and workspace file names for the ``claude_code`` harness.

Single source of truth for every literal the invocation script and the trace
converter share. Output names (``event_stream``/``agent.stderr``) are
harness-owned; ``PROMPT_NAME`` is the shared solve-input convention the harness
*reads* and the dataset/composition *writes* (the prompt is dataset-derived).
"""

from __future__ import annotations

# The pinned native Claude Code binary — a read-only asset at a fixed path,
# invoked by absolute path (not via PATH). See harness §5.3.
BINARY_AT = "/opt/claude-code/claude"

# A writable HOME for the agent inside the container (instance images run as
# root with no guaranteed-writable home; the binary wants a config dir). Set in
# the invocation script, in /tmp — ephemeral, not a workspace file.
AGENT_HOME = "/tmp/agent-home"

# The invocation script the harness stages and runs by its workspace path.
AGENT_SCRIPT_NAME = "agent.sh"

# The solve prompt the harness reads; staged by the composition (it is
# dataset-derived, not the harness's).
PROMPT_NAME = "prompt.txt"

# Native outputs the run writes into the workspace (registered as artifacts).
EVENT_STREAM_NAME = "event_stream.jsonl"  # stream-json event trace (primary)
AGENT_STDERR_NAME = "agent.stderr"  # the run's stderr log

DEFAULT_MODEL = "sonnet"
