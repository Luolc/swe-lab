"""The output-capture strategies a ``claude_code`` run can use.

Claude Code can surface its agent trace two ways, and the harness supports both
as first-class strategies (spec §"capture = stream | proxy"; "proxy is not
legacy"). ``STREAM`` (the default) reads the agent's own ``stream-json`` stdout;
``PROXY`` records every request/response pair at a ``cc-reverse-proxy`` in front
of the API. Both convert into the same canonical ``Conversation``.
"""

from __future__ import annotations

from enum import StrEnum


class Capture(StrEnum):
  """How a ``claude_code`` run captures its agent trace."""

  STREAM = "stream"  # agent stdout → claude.event_stream.jsonl (default)
  PROXY = "proxy"  # cc-reverse-proxy records request/response host-side
