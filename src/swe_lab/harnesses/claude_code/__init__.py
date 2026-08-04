"""The ``claude_code`` harness: Claude Code headless, plugged into the engine.

``ClaudeCodeHarness`` + the fresh agent-trace → ``Conversation`` converters for
both capture strategies (``Capture.STREAM`` / ``Capture.PROXY``).
"""

from swe_lab.harnesses import register_harness

from .capture import Capture
from .convert import (
    event_stream_complete,
    event_stream_to_conversation,
    proxy_log_complete,
    proxy_log_to_conversation,
)
from .harness import ClaudeCodeHarness

# Importing this package registers the agent it plugs in, exactly as importing
# a backend module registers a backend.
register_harness("claude_code", ClaudeCodeHarness)

__all__ = [
    "Capture",
    "ClaudeCodeHarness",
    "event_stream_complete",
    "event_stream_to_conversation",
    "proxy_log_complete",
    "proxy_log_to_conversation",
]
