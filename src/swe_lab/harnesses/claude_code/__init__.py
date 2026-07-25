"""The ``claude_code`` harness: Claude Code headless, plugged into the engine.

``ClaudeCodeHarness`` + the fresh agent-trace → ``Conversation`` converters for
both capture strategies (``Capture.STREAM`` / ``Capture.PROXY``).
"""

from .capture import Capture
from .convert import (
    event_stream_complete,
    event_stream_to_conversation,
    proxy_log_complete,
    proxy_log_to_conversation,
)
from .harness import ClaudeCodeHarness

__all__ = [
    "Capture",
    "ClaudeCodeHarness",
    "event_stream_complete",
    "event_stream_to_conversation",
    "proxy_log_complete",
    "proxy_log_to_conversation",
]
