"""Claude Code agent trace → the canonical ``Conversation``.

Written fresh (stdlib ``json`` → the typed model), *not* wrapping the
soon-deprecated ``core/agent/trace.py`` dict parser. Both capture strategies
land here:

- **stream** — the agent's own ``stream-json`` stdout: each line is one event;
  ``user`` / ``assistant`` events carry a ``message`` with Anthropic-shaped
  content blocks.
- **proxy** — the ``cc-reverse-proxy`` request/response log: one record per API
  call. Anthropic is stateless, so the *last* record's ``request.body.messages``
  already holds the entire prior conversation and ``response.message`` is the
  final assistant turn — reading that one record reconstructs the whole trace.

Both map their Anthropic content blocks through the **same** ``_content_blocks``
/ ``_one_block`` helpers, so a stream and a proxy capture of one session convert
to the same :class:`~swe_lab.conversation.Conversation` by construction.

No PII redaction is needed here: the rollout agent runs **inside** the instance
container (``HOME`` = ``/agent-home``, git config = the instance's), so the
operator's identity is never injected into the trace — unlike a host-subprocess
run (W1), which redacts separately.
"""

from __future__ import annotations

import json
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


def event_stream_to_conversation(raw: str) -> Conversation:
  """Convert a Claude Code ``event_stream`` text into a typed ``Conversation``.

  Args:
    raw: The ``event_stream.jsonl`` contents (may be ``""`` — an agent that
      never started leaves no file).

  Returns:
    The conversation; an empty ``Conversation(messages=[])`` when the text is
    empty or carries no user/assistant messages.
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


def event_stream_complete(raw: str) -> bool:
  """Return whether the run finished cleanly.

  The terminal ``result`` event is the reliable signal: ``subtype == "success"``
  marks a clean finish, and every bounded-exit path emits a *distinct* error
  subtype instead — ``error_max_turns`` (max turns reached),
  ``error_max_budget_usd``, ``error_max_structured_output_retries``,
  ``error_during_execution`` — each with ``is_error`` set. A ``success`` subtype
  can still carry ``is_error`` (the final assistant turn was an API error), so
  we require both. Assistant messages may carry a null ``stop_reason``, so we
  never depend on it. (Verified against the Claude Code source
  ``QueryEngine.ts``.)

  Args:
    raw: The event-stream file contents.

  Returns:
    ``True`` iff a terminal success ``result`` event (not an error) is present.
  """
  for event in reversed(_parse_events(raw)):
    if event.get("type") == "result":
      return event.get("subtype") == "success" and not event.get(
          "is_error", False
      )
  return False


def proxy_log_to_conversation(raw: str) -> Conversation:
  """Convert a ``cc-reverse-proxy`` log into a typed ``Conversation``.

  The last record reconstructs the whole session (Anthropic is stateless): its
  ``request.body`` carries the ``system`` prompt and every prior ``user`` /
  ``assistant`` turn, and ``response.message`` is the final assistant turn.

  Args:
    raw: The proxy log contents (may be "" — a run that never reached the API
      leaves no file).

  Returns:
    The conversation; an empty ``Conversation(messages=[])`` when the file is
    absent or carries no messages.
  """
  record = _last_proxy_record(raw)
  body = _as_dict(record.get("request")).get("body")
  body = body if isinstance(body, dict) else {}
  messages: list[Message] = []
  system_blocks = _content_blocks(body.get("system"))
  if system_blocks:
    messages.append(Message(role=Role.SYSTEM, content=system_blocks))
  input_messages = body.get("messages")
  if isinstance(input_messages, list):
    for message in input_messages:
      if not isinstance(message, dict):
        continue
      role = _role(message.get("role"))
      blocks = _content_blocks(message.get("content"))
      if role is not None and blocks:
        messages.append(Message(role=role, content=blocks))
  final = _as_dict(record.get("response")).get("message")
  final_blocks = _content_blocks(_as_dict(final).get("content"))
  if final_blocks:
    messages.append(Message(role=Role.ASSISTANT, content=final_blocks))
  return Conversation(messages=messages)


def proxy_log_complete(raw: str) -> bool:
  """Return whether the proxied session finished cleanly.

  The proxy stamps each record with its own ``complete`` flag (a
  ``message_delta`` carrying a ``stop_reason`` for a stream, or a fully-read
  buffered response); the last record's flag is the session's completion signal.

  Args:
    raw: The proxy log contents.

  Returns:
    ``True`` iff the last record is marked ``complete``.
  """
  return bool(_last_proxy_record(raw).get("complete", False))


def _last_proxy_record(raw: str) -> dict[str, object]:
  """Return the last JSON record in the proxy log (``{}`` when none)."""
  records = _parse_events(raw)
  return records[-1] if records else {}


def _parse_events(raw: str) -> list[dict[str, object]]:
  """Parse the stream-json text (one JSON object per line), skipping junk."""
  events: list[dict[str, object]] = []
  for line in raw.splitlines():
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
  if value == "system":
    return Role.SYSTEM
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
  return "\n\n".join(parts)


def _as_dict(value: object) -> dict[str, Any]:
  """Return ``value`` when it is a dict, else an empty dict."""
  return value if isinstance(value, dict) else {}


def _opt_str(value: object) -> str | None:
  """Return ``value`` when it is a string, else ``None``."""
  return value if isinstance(value, str) else None
