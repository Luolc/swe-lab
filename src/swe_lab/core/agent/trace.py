"""Headless-agent trace capture → one source-agnostic exchange record.

A headless Claude Code run can be captured two ways (see ``CAPTURE_MODES``):
``proxy`` (a ``cc-reverse-proxy`` logs the raw wire request/response) or
``stream`` (``claude --output-format stream-json`` writes every event to
stdout). Both normalize here to a single **unified exchange record** so
downstream audit tooling never branches on the source:

    {source, complete, model, messages[], system|null, tools|null, extra_info}

``messages`` is the full conversation through the final answer; each message
keeps the canonical per-turn fields below, null-filled when a source lacks them.
``system`` / ``tools`` are the API request's values (proxy) or null (stream).
Source-specific data (wire headers, run summary) goes in ``extra_info``, with
secrets (the auth header, ``metadata.user_id``) stripped as it is built, and
operator PII (home path, git name/email) swapped for a stable placeholder.

This module is dataset- and task-agnostic: both the related-files annotator
(host subprocess) and ``rollout`` (agent runs inside the instance container,
writing its ``stream-json`` trajectory to a mounted file) reuse it.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

# Where the run's trace (the audit record + the ``complete`` signal) comes from.
# ``proxy``  — a ``cc-reverse-proxy`` in front of the API logs the raw wire
#              request/response (exact system prompt + payload); most faithful.
# ``stream`` — ``claude --output-format stream-json`` writes every event to the
#              CLI's own stdout; no proxy (no submodule build, no port), but the
#              record is Claude Code's message view, without the raw system
#              prompt. See :func:`last_stream_record`.
CAPTURE_PROXY = "proxy"
CAPTURE_STREAM = "stream"
CAPTURE_MODES = (CAPTURE_PROXY, CAPTURE_STREAM)
DEFAULT_CAPTURE = CAPTURE_STREAM


# --- stream-json event parsing -----------------------------------------------


def parse_stream_events(stdout: str) -> list[dict[str, object]]:
  """Parse ``stream-json`` stdout (one JSON event per line) into a list."""
  events: list[dict[str, object]] = []
  for line in stdout.splitlines():
    line = line.strip()
    if not line:
      continue
    try:
      obj = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(obj, dict):
      events.append(obj)
  return events


def final_result_event(
    events: list[dict[str, object]],
) -> dict[str, object] | None:
  for event in reversed(events):
    if event.get("type") == "result":
      return event
  return None


def _last_assistant_message(
    events: list[dict[str, object]],
) -> dict[str, object] | None:
  for event in reversed(events):
    message = event.get("message")
    if event.get("type") == "assistant" and isinstance(message, dict):
      return message
  return None


def last_assistant_stop_reason(events: list[dict[str, object]]) -> object:
  message = _last_assistant_message(events)
  return message.get("stop_reason") if message else None


def _stream_complete(result_event: dict[str, object]) -> bool:
  """Whether a ``stream-json`` run finished cleanly.

  The reliable signal is the terminal ``result`` event (``subtype == "success"``
  and not ``is_error``) — assistant messages in the stream may carry a null
  ``stop_reason``, so we do not depend on it.
  """
  return result_event.get("subtype") == "success" and not result_event.get(
      "is_error", False
  )


def last_stream_record(stream_log: Path) -> dict[str, object]:
  """Read a ``stream-json`` trace file, normalized to the exchange schema."""
  if not stream_log.is_file():
    return {}
  events = parse_stream_events(stream_log.read_text())
  return build_exchange_from_stream(events)


def last_proxy_record(proxy_log: Path) -> dict[str, object]:
  """Read the last proxy log record, normalized to the exchange schema."""
  raw = _last_proxy_raw(proxy_log)
  return build_exchange_from_proxy(raw) if raw else {}


def _last_proxy_raw(proxy_log: Path) -> dict[str, object]:
  if not proxy_log.is_file():
    return {}
  last_line = ""
  with proxy_log.open() as handle:
    for line in handle:
      if line.strip():
        last_line = line
  if not last_line:
    return {}
  record = json.loads(last_line)
  return record if isinstance(record, dict) else {}


# --- Unified exchange record -------------------------------------------------

_MESSAGE_FIELDS = ("role", "content", "id", "model", "stop_reason", "usage")
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "x-api-key", "cookie", "anthropic-organization-id"}
)
# PII Claude Code injects into the system prompt / tool-call paths. It is
# swapped for a stable placeholder identity (matching the migrated historical
# records) so future runs never re-leak the operator's real identity while the
# traces still read naturally. Actual secrets (auth token, org id) are NOT given
# a fake identity — they are redacted by ``_scrub_headers``.
_FAKE_NAME = "Alan Turing"
_FAKE_EMAIL = "alan.turing@example.com"
_FAKE_HOME = "/Users/aturing"
_EMAIL_SENTENCE_RE = re.compile(r"(The user's email address is )\S+")
_GIT_USER_LINE_RE = re.compile(r"(Git user: )[^\n]+")
_PROXY_PARAM_FIELDS = (
    "max_tokens",
    "stream",
    "thinking",
    "output_config",
    "context_management",
)


def _normalize_message(message: object) -> dict[str, object]:
  """Project one message onto the canonical per-message field set."""
  source = message if isinstance(message, dict) else {}
  out: dict[str, object] = {}
  for field_name in _MESSAGE_FIELDS:
    out[field_name] = source.get(field_name)
  return out


def _scrub_headers(headers: object) -> object:
  if not isinstance(headers, dict):
    return headers
  return {
      key: ("<redacted>" if key.lower() in _SENSITIVE_HEADERS else value)
      for key, value in headers.items()
  }


def _scrub_metadata(metadata: object) -> object:
  if not isinstance(metadata, dict):
    return metadata
  return {key: value for key, value in metadata.items() if key != "user_id"}


def _git_config(key: str) -> str:
  """Best-effort ``git config`` lookup (empty string on any failure)."""
  try:
    result = subprocess.run(
        ["git", "config", "--get", key],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return ""
  return result.stdout.strip()


def _redact_text(text: str, home: str, name: str, email: str) -> str:
  if home:
    text = text.replace(home, _FAKE_HOME)
  if name:
    text = text.replace(name, _FAKE_NAME)
  if email:
    text = text.replace(email, _FAKE_EMAIL)
  text = _EMAIL_SENTENCE_RE.sub(lambda m: m.group(1) + _FAKE_EMAIL, text)
  text = _GIT_USER_LINE_RE.sub(lambda m: m.group(1) + _FAKE_NAME, text)
  return text


def _redact_value(value: object, home: str, name: str, email: str) -> object:
  if isinstance(value, str):
    return _redact_text(value, home, name, email)
  if isinstance(value, dict):
    return {k: _redact_value(v, home, name, email) for k, v in value.items()}
  if isinstance(value, list):
    return [_redact_value(v, home, name, email) for v in value]
  return value


def _redact_pii(record: dict[str, object]) -> dict[str, object]:
  """Swap operator PII (home path, name, email) for the placeholder identity.

  Claude Code injects the operator's home directory, git user, and email into
  the agent's system prompt and tool-call paths; this replaces them with the
  ``_FAKE_*`` identity so a freshly written record never re-leaks the real one.
  Header-level secrets (auth token, org id) are handled separately by
  :func:`_scrub_headers`.
  """
  home = str(Path.home())
  name = _git_config("user.name")
  email = _git_config("user.email")
  return {
      key: _redact_value(value, home, name, email)
      for key, value in record.items()
  }


def build_exchange_from_proxy(raw: dict[str, object]) -> dict[str, object]:
  """Map a raw ``cc-reverse-proxy`` record to the unified exchange schema."""
  request = raw.get("request")
  request = request if isinstance(request, dict) else {}
  response = raw.get("response")
  response = response if isinstance(response, dict) else {}
  body = request.get("body")
  body = body if isinstance(body, dict) else {}

  messages_src = list(body.get("messages") or [])
  response_message = response.get("message")
  if isinstance(response_message, dict):
    messages_src.append(response_message)

  return _redact_pii(
      {
          "source": CAPTURE_PROXY,
          "complete": bool(raw.get("complete", False)),
          "model": body.get("model"),
          "messages": [_normalize_message(m) for m in messages_src],
          "system": body.get("system"),
          "tools": body.get("tools"),
          "extra_info": {
              "request_headers": _scrub_headers(request.get("headers")),
              "response_headers": _scrub_headers(response.get("headers")),
              "status": response.get("status"),
              "request_params": {
                  field: body.get(field) for field in _PROXY_PARAM_FIELDS
              },
              "metadata": _scrub_metadata(body.get("metadata")),
          },
      }
  )


def build_exchange_from_stream(
    events: list[dict[str, object]],
) -> dict[str, object]:
  """Map parsed ``stream-json`` events to the unified exchange schema."""
  result_event = final_result_event(events) or {}
  messages_src: list[dict[str, object]] = []
  for event in events:
    message = event.get("message")
    if event.get("type") in ("user", "assistant") and isinstance(message, dict):
      messages_src.append(message)
  final_message = _last_assistant_message(events)
  model = final_message.get("model") if final_message else None
  return _redact_pii(
      {
          "source": CAPTURE_STREAM,
          "complete": _stream_complete(result_event),
          "model": model,
          "messages": [_normalize_message(m) for m in messages_src],
          "system": None,
          "tools": None,
          "extra_info": {"result": result_event},
      }
  )
