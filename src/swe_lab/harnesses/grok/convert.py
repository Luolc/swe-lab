"""Grok's headless trace → ``Conversation`` and outcome, by delegation.

Grok's ``--output-format streaming-messages-json`` emits **Claude Code's
stream-json schema** — measured on live 1.0.0 output, not inferred: the
``{"type":"system","subtype":"init",…}`` opener and the terminal
``{"type":"result","subtype":…,"is_error":…,"errors":[…]}`` are
byte-compatible with what the ``claude_code`` converter consumes, and the
message events are Anthropic Messages wire format, which is exactly the shape
``_content_blocks`` was written for.

So this module **delegates instead of reimplementing** (task-29 §4, decision
2). It exists as the seam where grok-specific deltas land *when a capture
shows one* — none has yet — so a future divergence is a one-file change with
evidence behind it rather than a fork of the parser.
"""

from __future__ import annotations

from swe_lab.conversation import Conversation
from swe_lab.harnesses.base import AgentOutcome
from swe_lab.harnesses.claude_code.convert import (
    event_stream_outcome as _claude_outcome,
)
from swe_lab.harnesses.claude_code.convert import (
    event_stream_to_conversation as _claude_conversation,
)


def event_stream_to_conversation(raw: str) -> Conversation:
  """Convert a grok ``streaming-messages-json`` trace into a ``Conversation``.

  Args:
    raw: The event-stream contents (may be ``""`` — a run that never started
      leaves no file).

  Returns:
    The conversation; empty when the text carries no user/assistant messages.
  """
  return _claude_conversation(raw)


def event_stream_outcome(raw: str) -> AgentOutcome:
  """Classify how a grok run ended from its own trace (ADR-0011).

  The terminal ``result`` event carries the same subtypes as Claude Code's —
  a live auth failure produced ``error_during_execution`` with ``is_error``
  and ``errors[]`` exactly as the claude_code classifier expects, and grok has
  a real ``--max-turns``, so the ``error_max_turns`` → ``MAX_TURNS`` mapping
  is *reachable* here (it is not for Codex).

  Args:
    raw: The event-stream contents.

  Returns:
    ``NO_OUTPUT`` / ``TRUNCATED`` / the terminal verdict, per the shared
    mapping.
  """
  return _claude_outcome(raw)
