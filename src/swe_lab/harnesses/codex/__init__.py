"""The ``codex`` harness: OpenAI Codex headless, plugged into the engine.

``CodexHarness`` + the ``exec --json`` trace → ``Conversation`` converter and
the outcome classifier.
"""

from swe_lab.harnesses import register_harness

from .auth import CodexAuthObserver
from .binary import ensure_codex_binaries
from .convert import event_stream_outcome, event_stream_to_conversation
from .harness import CodexHarness

# Importing this package registers the agent it plugs in, exactly as importing
# a backend module registers a backend.
register_harness("codex", CodexHarness)

__all__ = [
    "CodexAuthObserver",
    "CodexHarness",
    "ensure_codex_binaries",
    "event_stream_outcome",
    "event_stream_to_conversation",
]
