"""Unified exchange record: schema shape + PII/secret redaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from swe_lab.core.agent.trace import (
    build_exchange_from_proxy,
    CAPTURE_PROXY,
)

_MESSAGE_KEYS = {"role", "content", "id", "model", "stop_reason", "usage"}
_RECORD_KEYS = {
    "source",
    "complete",
    "model",
    "messages",
    "system",
    "tools",
    "extra_info",
}


def _raw_proxy_record(home: str) -> dict[str, object]:
  user_text = (
      "The user's email address is jane@roe.example.\n"
      f"working dir: {home}/dev/x/file.py"
  )
  body = {
      "model": "claude-sonnet-4-6",
      "max_tokens": 32000,
      "system": [{"type": "text", "text": "Git user: Jane Roe\nrest"}],
      "tools": [{"name": "Read"}],
      "messages": [
          {"role": "user", "content": [{"type": "text", "text": user_text}]}
      ],
      "metadata": {"user_id": "device+account json"},
  }
  answer = {
      "role": "assistant",
      "content": [{"type": "text", "text": "done"}],
      "id": "msg_1",
      "model": "claude-sonnet-4-6",
      "stop_reason": "end_turn",
      "usage": {"output_tokens": 3},
  }
  return {
      "complete": True,
      "request": {
          "body": body,
          "headers": {
              "Authorization": "Bearer sk-ant-oat01-SECRET",
              "X-App": "cli",
          },
      },
      "response": {
          "message": answer,
          "headers": {"Anthropic-Organization-Id": "org-uuid-123"},
          "status": 200,
      },
  }


def test_proxy_exchange_schema() -> None:
  rec = build_exchange_from_proxy(_raw_proxy_record(str(Path.home())))
  assert set(rec) == _RECORD_KEYS
  assert rec["source"] == CAPTURE_PROXY
  assert rec["complete"] is True
  assert rec["model"] == "claude-sonnet-4-6"
  # full history through the final answer; canonical keys, null-filled
  messages = cast(list[dict[str, object]], rec["messages"])
  assert messages[-1]["role"] == "assistant"
  assert all(set(m) == _MESSAGE_KEYS for m in messages)
  assert messages[0]["id"] is None


def test_proxy_exchange_redacts_pii_and_secrets() -> None:
  home = str(Path.home())
  rec = build_exchange_from_proxy(_raw_proxy_record(home))
  blob = json.dumps(rec)

  # operator PII swapped for the placeholder identity in message / system text
  assert "jane@roe.example" not in blob
  assert "Jane Roe" not in blob
  assert home not in blob
  assert "/Users/aturing" in blob
  assert "Alan Turing" in blob
  assert "alan.turing@example.com" in blob

  # header-level secrets scrubbed (request + response)
  assert "sk-ant-oat01" not in blob
  extra = cast(dict[str, object], rec["extra_info"])
  req_headers = cast(dict[str, object], extra["request_headers"])
  assert req_headers["Authorization"] == "<redacted>"
  assert req_headers["X-App"] == "cli"  # non-sensitive header preserved
  resp_headers = cast(dict[str, object], extra["response_headers"])
  assert resp_headers["Anthropic-Organization-Id"] == "<redacted>"

  # user_id (device/account ids) dropped from metadata
  assert extra["metadata"] == {}
