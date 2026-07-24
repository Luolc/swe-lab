"""The ``claude_code`` harness: Claude Code headless, plugged into the engine.

``ClaudeCodeHarness`` + the fresh ``event_stream`` → ``Conversation`` converter.
"""

from .convert import event_stream_complete, event_stream_to_conversation
from .harness import ClaudeCodeHarness

__all__ = [
    "ClaudeCodeHarness",
    "event_stream_complete",
    "event_stream_to_conversation",
]
