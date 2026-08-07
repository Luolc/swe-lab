"""The output-capture strategies a ``claude_code`` run can use.

Claude Code can surface its agent trace two ways, and the harness supports both
as first-class strategies (neither is legacy). ``STREAM`` (the default) reads
the agent's own ``stream-json`` stdout;
``PROXY`` records every request/response pair at a ``cc-reverse-proxy`` in front
of the API. Both convert into the same canonical ``Conversation``.
"""

from __future__ import annotations

from enum import StrEnum


class Capture(StrEnum):
  """How a ``claude_code`` run captures its agent trace."""

  STREAM = "stream"  # agent stdout → claude.event_stream.jsonl (default)
  PROXY = "proxy"  # cc-reverse-proxy records request/response host-side


class Effort(StrEnum):
  """How much reasoning effort a ``claude_code`` run is told to spend.

  The exact set the pinned agent accepts, read off the binary itself rather
  than a doc — 2.1.220 answers an unknown value with::

      Warning: Unknown --effort value 'bogus' — ignoring it and using the
      default effort. Valid values: low, medium, high, xhigh, max.

  That warning is the reason this is an enum. An unrecognized value is **not**
  an error to the agent: it shrugs and silently runs at its default, so a typo
  in a sweep config would produce a whole batch at the wrong effort with
  nothing in the logs to say so. Python refuses it here instead, before a
  container is paid for.
  """

  LOW = "low"
  MEDIUM = "medium"
  HIGH = "high"
  XHIGH = "xhigh"
  MAX = "max"
