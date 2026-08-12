"""The ``grok`` harness: xAI's Grok Build headless, plugged into the engine.

``GrokHarness`` + the ``streaming-messages-json`` trace converter (delegating
to the ``claude_code`` converter — the wire format is the same, measured) and
the outcome classifier.
"""

from swe_lab.harnesses import register_harness

from .auth import GrokAuthObserver
from .binary import ensure_grok_binary
from .convert import event_stream_outcome, event_stream_to_conversation
from .harness import GrokHarness

# Importing this package registers the agent it plugs in, exactly as importing
# a backend module registers a backend.
register_harness("grok", GrokHarness)

__all__ = [
    "GrokAuthObserver",
    "GrokHarness",
    "ensure_grok_binary",
    "event_stream_outcome",
    "event_stream_to_conversation",
]
