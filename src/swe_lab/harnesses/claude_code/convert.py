"""Claude Code agent trace → the canonical ``Conversation``.

Written fresh: stdlib ``json`` → the typed ``Conversation`` model directly (no
intermediate dict parser). Both capture strategies land here:

- **stream** — the agent's own ``stream-json`` stdout: each line is one event;
  ``user`` / ``assistant`` events carry a ``message`` with Anthropic-shaped
  content blocks.
- **proxy** — the ``cc-reverse-proxy`` request/response log: one record per API
  call. Anthropic is stateless, so the *last* record's ``request.body.messages``
  already holds the entire prior conversation and ``response.message`` is the
  final assistant turn — reading that one record reconstructs the whole trace.

Both map their Anthropic content blocks through the **same** ``_content_blocks``
/ ``_one_block`` helpers, so a block means the same thing whichever capture it
arrived in. **That is a claim about blocks and not about conversations**: the
message sequence and the roles can differ by capture, because the two record
different things. One mid-turn correction is a ``user`` message in a stream
capture and a ``system`` message wrapping ``<system-reminder>`` in a proxy one —
each faithful to its own source, neither a defect (ADR-0017). A consumer pooling
traces from both must key on which capture produced each.

**Which capture this is about matters, and an earlier version of this note did
not say.** Nothing is redacted *here*, and for stream capture nothing needs to
be: the rollout agent runs **inside** the instance container (its config dir
pinned per run by ``CLAUDE_CONFIG_DIR``, git config = the instance's), so the
operator's identity is never injected into what the agent emits — unlike a
host-subprocess run (W1), which redacts separately.

**Proxy capture is a different question and is answered elsewhere.** A proxy
record is a whole HTTP exchange, so it has an envelope a stream event does not:
credentials on the request, account identity on the response. Those are masked
at write time by ``cc-reverse-proxy`` (see
:mod:`~swe_lab.harnesses.claude_code.redaction`, which checks that the build we
actually ran did so). Conversion neither adds nor removes any of it.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from swe_lab.harnesses.base import AgentOutcome


def event_stream_to_conversation(raw: str) -> Conversation:
  """Convert a Claude Code ``event_stream`` text into a typed ``Conversation``.

  Args:
    raw: The ``event_stream.jsonl`` contents (may be ``""`` — an agent that
      never started leaves no file).

  Returns:
    The conversation; an empty ``Conversation(messages=[])`` when the text is
    empty or carries no user/assistant messages.
  """
  messages = [m for e in _parse_events(raw) if (m := event_to_message(e))]
  return Conversation(messages=messages)


def event_to_message(event: Mapping[str, Any]) -> Message | None:
  """Convert one ``stream-json`` event into a typed message.

  Extracted from :func:`event_stream_to_conversation` so that a live consumer
  reading the stream event by event maps content blocks through the *same*
  helpers as the after-the-fact conversion, rather than growing a second
  parser beside it.

  Args:
    event: One decoded ``stream-json`` event.

  Returns:
    The message it carries, or ``None`` for an event that carries none — a
    non-``user``/``assistant`` event, a malformed ``message``, an unknown role,
    or content with no block we represent.
  """
  if event.get("type") not in ("user", "assistant"):
    return None
  message = event.get("message")
  if not isinstance(message, dict):
    return None
  role = _role(message.get("role"))
  blocks = _content_blocks(message.get("content"))
  if role is None or not blocks:
    return None
  return Message(role=role, content=blocks)


# The terminal ``result`` event's error subtypes, mapped onto the taxonomy.
# Exhaustive as of the vendored source: ``SDKResultErrorSchema`` in
# ``entrypoints/sdk/coreSchemas.ts`` enumerates exactly these four, and
# ``SDKResultSuccessSchema`` the one ``success``.
_ERROR_SUBTYPES: dict[str, AgentOutcome] = {
    "error_max_turns": AgentOutcome.MAX_TURNS,
    "error_max_budget_usd": AgentOutcome.MAX_BUDGET,
    "error_max_structured_output_retries": AgentOutcome.MAX_OUTPUT_RETRIES,
    "error_during_execution": AgentOutcome.EXECUTION_ERROR,
}


def event_stream_usage(raw: str) -> dict[str, float | int | None]:
  """Read what the run cost from its ``stream-json`` trace.

  Both numbers sit in the ``result`` events we already parse for the outcome,
  and they are aggregated differently, which is the whole reason this is a
  function rather than a field read:

  - ``total_cost_usd`` is **cumulative over the session**, so the total is the
    value on the **final** result and summing would count earlier segments
    twice.
  - ``num_turns`` is **per result**, so a segmented run reports its turns in
    pieces and the total is their sum.

  **A metric whose inputs are not all present is ``None``, never a partial.**
  The final result is the only one that carries the cumulative cost, so a trace
  whose last result omits it has no cost — reporting the previous segment's
  figure would be a stale number wearing a measured one's clothes. Likewise a
  sum over some of the segments is not the run's turn count. Both would enter a
  later average as though they had been measured, which is the failure this
  function exists to avoid.

  Args:
    raw: The event-stream file contents (``""`` when the agent wrote none).

  Returns:
    ``cost_usd`` and ``num_turns``, each ``None`` unless every result the metric
    needs carried a usable value.
  """
  results = [e for e in _parse_events(raw) if e.get("type") == "result"]
  if not results:
    return {"cost_usd": None, "num_turns": None}

  final_cost = results[-1].get("total_cost_usd")
  cost = float(final_cost) if isinstance(final_cost, (int, float)) else None

  counts = [event.get("num_turns") for event in results]
  present = [count for count in counts if isinstance(count, int)]
  turns = sum(present) if len(present) == len(counts) else None

  return {"cost_usd": cost, "num_turns": turns}


def event_stream_outcome(raw: str) -> AgentOutcome:
  """Classify how the run ended from its ``stream-json`` trace.

  The terminal ``result`` event is the reliable signal, and it says which of
  the endings happened: ``subtype == "success"`` marks a clean finish, and
  every bounded-exit path emits a *distinct* error subtype instead (see
  :data:`_ERROR_SUBTYPES`). Load-bearing details, all read off the Claude Code
  source (``QueryEngine.ts``, ``entrypoints/sdk/coreSchemas.ts``):

  - **``is_error`` is independent of the subtype.** A ``success`` result can
    carry ``is_error: true`` — the loop ended but its final turn was an API
    error — which is a *different* outcome from a clean finish and, unlike it,
    worth retrying.
  - **A result is not guaranteed.** ``print.ts`` synthesizes an
    ``error_during_execution`` from its top-level ``catch``, but inside its own
    try/catch that gives up on shutdown; a hard kill emits nothing at all. So
    "a trace with no result event" is its own outcome (``TRUNCATED``), and
    "no trace" another (``NO_OUTPUT``).
  - An unrecognized error subtype maps to ``EXECUTION_ERROR``, the catch-all it
    would be a flavour of; a *new* budget ending would arrive here as
    retryable, which is the direction that needs watching (ADR-0011).
  - Assistant messages may carry a null ``stop_reason``, so we never depend on
    it.

  Args:
    raw: The event-stream file contents (``""`` when the agent wrote none).

  Returns:
    How the agent's loop ended.
  """
  events = _parse_events(raw)
  if not events:
    return AgentOutcome.NO_OUTPUT
  for event in reversed(events):
    if event.get("type") != "result":
      continue
    subtype = event.get("subtype")
    if subtype == "success":
      return (
          AgentOutcome.FINISHED_WITH_API_ERROR
          if event.get("is_error", False)
          else AgentOutcome.FINISHED
      )
    return _ERROR_SUBTYPES.get(str(subtype), AgentOutcome.EXECUTION_ERROR)
  return AgentOutcome.TRUNCATED


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


def proxy_log_outcome(raw: str) -> AgentOutcome:
  """Classify a proxied session's ending — coarsely, and deliberately so.

  A proxy log is a record of **API traffic**, not of the agent loop: the flag
  it stamps on each record says the last *HTTP response* was fully received (a
  ``message_delta`` carrying a ``stop_reason`` for a stream, a fully-read body
  otherwise), which is a strictly weaker claim than "the agent finished". A run
  that hit ``--max-turns``, and a run that crashed *after* its last response,
  both end on a perfectly complete response.

  So this maps onto only the two outcomes the evidence supports, and resolves
  the ambiguity **towards not retrying**: a complete last response reads
  ``FINISHED`` even though it may hide a budget ending or a late crash, because
  re-running an ending the agent chose is what inflates a score, while a
  truncated one reads ``TRUNCATED`` — a response cut mid-flight is ours. A
  composition that needs the agent's own budget endings distinguished should
  capture ``STREAM``, where the agent reports them itself.

  Args:
    raw: The proxy log contents (``""`` when the run never reached the API).

  Returns:
    ``NO_OUTPUT`` with no records, else ``FINISHED`` / ``TRUNCATED``.
  """
  record = _last_proxy_record(raw)
  if not record:
    return AgentOutcome.NO_OUTPUT
  if record.get("complete", False):
    return AgentOutcome.FINISHED
  return AgentOutcome.TRUNCATED


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


def user_event_line(text: str) -> str:
  """Return one stream-json user event, newline-terminated.

  The wire shape is not ours to choose: it is what the CLI accepts under
  ``--input-format stream-json``, and it is the shape the compliance experiment
  measured, so it is reproduced rather than re-derived.

  It belongs here, not in ``harness``: ``trace_synthesis.channel`` needs it and
  ``harness`` imports ``trace_synthesis``, so defining it there makes the
  import graph cyclic.

  Args:
    text: The message body.

  Returns:
    A single JSON line, ending in a newline.
  """
  event = {
      "type": "user",
      "message": {"role": "user", "content": [{"type": "text", "text": text}]},
  }
  return json.dumps(event) + "\n"
