"""Claude Code ``event_stream`` (stream-json) → the canonical ``Conversation``.

Written fresh (stdlib ``json`` over the stream-json lines → the typed model),
*not* wrapping the soon-deprecated ``core/agent/trace.py`` dict parser. Each
line is one JSON event; ``user`` / ``assistant`` events carry a ``message`` with
Anthropic-shaped content blocks, which map onto the canonical
:class:`~swe_lab.conversation.Conversation`.

No PII redaction is needed here: the rollout agent runs **inside** the instance
container (``HOME`` = ``/tmp/agent-home``, git config = the instance's), so the
operator's identity is never injected into the trace — unlike a host-subprocess
run (W1), which redacts separately.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swe_lab.conversation import (
    ContentBlock,
    Conversation,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def event_stream_to_conversation(raw: Path) -> Conversation:
  """Convert a Claude Code ``event_stream`` file into a typed ``Conversation``.

  Args:
    raw: Path to the ``event_stream.jsonl`` file (may be absent — an agent that
      never started leaves no file).

  Returns:
    The conversation; an empty ``Conversation(messages=[])`` when the file is
    absent or carries no user/assistant messages.
  """
  messages: list[Message] = []
  for event in _parse_events(raw):
    if event.get("type") not in ("user", "assistant"):
      continue
    message = event.get("message")
    if not isinstance(message, dict):
      continue
    role = _role(message.get("role"))
    blocks = _content_blocks(message.get("content"))
    if role is not None and blocks:
      messages.append(Message(role=role, content=blocks))
  return Conversation(messages=messages)


def event_stream_complete(raw: Path) -> bool:
  """Return whether the run finished cleanly.

  The reliable signal is the terminal ``result`` event (``subtype == "success"``
  and not ``is_error``); assistant messages may carry a null ``stop_reason``.

  Args:
    raw: Path to the ``event_stream.jsonl`` file.

  Returns:
    ``True`` iff a terminal success ``result`` event is present.
  """
  for event in reversed(_parse_events(raw)):
    if event.get("type") == "result":
      return event.get("subtype") == "success" and not event.get(
          "is_error", False
      )
  return False


def _parse_events(raw: Path) -> list[dict[str, object]]:
  """Parse the stream-json file (one JSON object per line), skipping junk."""
  if not raw.is_file():
    return []
  events: list[dict[str, object]] = []
  for line in raw.read_text().splitlines():
    stripped = line.strip()
    if not stripped:
      continue
    try:
      obj = json.loads(stripped)
    except json.JSONDecodeError:
      continue
    if isinstance(obj, dict):
      events.append(obj)
  return events


def _role(value: object) -> Role | None:
  """Map a message ``role`` string onto a canonical ``Role`` (else ``None``)."""
  if value == "user":
    return Role.USER
  if value == "assistant":
    return Role.ASSISTANT
  return None


def _content_blocks(content: object) -> list[ContentBlock]:
  """Map a message ``content`` (a string or a list of blocks) onto blocks."""
  if isinstance(content, str):
    return [TextBlock(text=content)] if content else []
  if not isinstance(content, list):
    return []
  blocks: list[ContentBlock] = []
  for item in content:
    block = _one_block(item)
    if block is not None:
      blocks.append(block)
  return blocks


def _one_block(item: object) -> ContentBlock | None:
  """Map one Anthropic content block; drop kinds v0 does not model."""
  if not isinstance(item, dict):
    return None
  kind = item.get("type")
  if kind == "text":
    return TextBlock(text=str(item.get("text", "")))
  if kind == "thinking":
    return ReasoningBlock(
        text=str(item.get("thinking", "")),
        signature=_opt_str(item.get("signature")),
    )
  if kind == "tool_use":
    return ToolUseBlock(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        input=_as_dict(item.get("input")),
    )
  if kind == "tool_result":
    return ToolResultBlock(
        tool_use_id=str(item.get("tool_use_id", "")),
        content=_flatten_result(item.get("content")),
        is_error=bool(item.get("is_error", False)),
    )
  return None  # redacted_thinking / image / … — not modeled in v0


def _flatten_result(content: object) -> str:
  """Flatten a tool-result ``content`` (string or text blocks) to text (v0)."""
  if isinstance(content, str):
    return content
  if not isinstance(content, list):
    return ""
  parts: list[str] = []
  for item in content:
    if isinstance(item, dict) and item.get("type") == "text":
      parts.append(str(item.get("text", "")))
  return "\n".join(parts)


def _as_dict(value: object) -> dict[str, Any]:
  """Return ``value`` when it is a dict, else an empty dict."""
  return value if isinstance(value, dict) else {}


def _opt_str(value: object) -> str | None:
  """Return ``value`` when it is a string, else ``None``."""
  return value if isinstance(value, str) else None
