"""Codex ``exec --json`` trace → a ``Conversation``, and the run's outcome.

Codex emits one JSON object per line (`ThreadEvent` in the upstream
``exec_events.rs`): a `thread.started`, then `turn.started` / `turn.completed`
or `turn.failed` around `item.started` / `item.updated` / `item.completed`
events, plus a top-level `error`. Only **completed** items are converted — an
item is emitted twice (started, then completed), and the completed one is the
only shape that carries the result.

The item model is flatter than Anthropic's: a `command_execution` item holds
the command *and* its output in one record. It is expanded back into the
canonical call/result pair — an assistant ``ToolUseBlock`` followed by a user
``ToolResultBlock`` sharing the item's id — so a Codex trace and a Claude Code
trace of the same work read the same way downstream.

Schema and mapping verified against live 0.147.0 runs (2026-08-08), not only
the source.
"""

from __future__ import annotations

import json
from typing import Any

from swe_lab.conversation import (
    Conversation,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from swe_lab.harnesses.base import AgentOutcome

# Item types that are a *tool call*: the item's own payload is the call's
# input, and what it produced is the result. Everything else is plain content.
_TOOL_ITEMS = frozenset(
    {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
        "todo_list",
    }
)


def event_stream_to_conversation(raw: str) -> Conversation:
  """Convert a Codex ``exec --json`` trace into a typed ``Conversation``.

  Args:
    raw: The event-stream contents (may be ``""`` — a run that never started
      leaves no file).

  Returns:
    The conversation; empty when the text carries no completed items.
  """
  messages: list[Message] = []
  for event in _parse_events(raw):
    if event.get("type") != "item.completed":
      continue
    item = event.get("item")
    if not isinstance(item, dict):
      continue
    messages.extend(_item_messages(item))
  return Conversation(messages=messages)


def _item_messages(item: dict[str, Any]) -> list[Message]:
  """Map one completed item onto canonical messages.

  Args:
    item: The item payload.

  Returns:
    One assistant message, plus a user message carrying the tool result when
    the item is a tool call. Empty when the item carries nothing to record.
  """
  kind = str(item.get("type", ""))
  if kind == "agent_message":
    return _text_message(Role.ASSISTANT, str(item.get("text", "")))
  if kind == "reasoning":
    text = str(item.get("text", ""))
    if not text:
      return []
    return [Message(role=Role.ASSISTANT, content=[ReasoningBlock(text=text)])]
  if kind == "error":
    # A non-fatal notice surfaced as an item (a degraded feature, a recoverable
    # failure). Recorded as assistant text so it is visible in the trace, and
    # deliberately NOT treated as the run's outcome — that is a turn-level
    # fact, and a live run showed this item present on an otherwise clean turn.
    return _text_message(Role.ASSISTANT, str(item.get("message", "")))
  if kind in _TOOL_ITEMS:
    return _tool_messages(item, kind)
  return []  # a kind this version does not model


def _text_message(role: Role, text: str) -> list[Message]:
  """Wrap non-empty text as a single-block message."""
  if not text:
    return []
  return [Message(role=role, content=[TextBlock(text=text)])]


def _tool_messages(item: dict[str, Any], kind: str) -> list[Message]:
  """Expand a tool item into the canonical call + result pair.

  Args:
    item: The completed tool item.
    kind: Its ``type``, used as the tool name.

  Returns:
    The assistant's call and the user's result, sharing the item's id.
  """
  item_id = str(item.get("id", ""))
  call = ToolUseBlock(id=item_id, name=kind, input=_tool_input(item, kind))
  result = ToolResultBlock(
      tool_use_id=item_id,
      content=_tool_output(item, kind),
      is_error=_tool_failed(item),
  )
  return [
      Message(role=Role.ASSISTANT, content=[call]),
      Message(role=Role.USER, content=[result]),
  ]


def _tool_input(item: dict[str, Any], kind: str) -> dict[str, Any]:
  """Return the call's arguments — everything but the result fields."""
  omit = {"id", "type", "aggregated_output", "exit_code", "status"}
  del kind
  return {k: v for k, v in item.items() if k not in omit}


def _tool_output(item: dict[str, Any], kind: str) -> str:
  """Flatten a tool item's result to text (v0 models results as text)."""
  if kind == "command_execution":
    return str(item.get("aggregated_output", ""))
  status = item.get("status")
  return "" if status is None else f"status: {status}"


def _tool_failed(item: dict[str, Any]) -> bool:
  """Whether the tool call reported a failure.

  A non-zero exit code, or a terminal status that is not a success. Read
  defensively: a status this version does not know reads as *not* failed
  rather than poisoning the trace with false errors.
  """
  exit_code = item.get("exit_code")
  if isinstance(exit_code, int) and exit_code != 0:
    return True
  return item.get("status") in ("failed", "error")


def event_stream_outcome(raw: str) -> AgentOutcome:
  """Classify how a Codex run ended from its own trace (ADR-0011).

  The turn-level events are the signal — `turn.completed` for a clean end,
  `turn.failed` and the top-level `error` for a broken one. Item-level errors
  are deliberately *not* consulted: a live 0.147.0 run emits an
  ``item.completed`` of type ``error`` (a degraded optional feature) on a turn
  that then completes perfectly, so reading items here would report a healthy
  run as failed and, worse, retry it.

  Codex reports no budget endings — it has no `--max-turns` equivalent in this
  surface — so ``MAX_TURNS`` / ``MAX_BUDGET`` are simply unreachable here
  rather than guessed at.

  Args:
    raw: The event-stream contents.

  Returns:
    ``NO_OUTPUT`` for an absent or empty trace, ``TRUNCATED`` when the stream
    stops before any turn-level verdict, else the verdict itself.
  """
  events = _parse_events(raw)
  if not events:
    return AgentOutcome.NO_OUTPUT
  for event in reversed(events):
    kind = event.get("type")
    if kind == "turn.completed":
      return AgentOutcome.FINISHED
    if kind in ("turn.failed", "error"):
      return AgentOutcome.EXECUTION_ERROR
  return AgentOutcome.TRUNCATED


def _parse_events(raw: str) -> list[dict[str, Any]]:
  """Parse the JSONL text (one object per line), skipping junk."""
  events: list[dict[str, Any]] = []
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
