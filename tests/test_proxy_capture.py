"""Proxy-capture conversion: proxy log → Conversation, equivalent to stream.

The heart is the equivalence test: a stream capture and a proxy capture of the
*same* session convert to the same user/assistant messages (the proxy carries an
extra SYSTEM turn — a richer capture). Fixtures are inline literals, mirroring
``test_exchange_record.py`` and ``test_claude_code_harness.py``.
"""

import json
from pathlib import Path

from swe_lab.conversation import (
    Conversation,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from swe_lab.harnesses.claude_code import (
    event_stream_to_conversation,
    proxy_log_complete,
    proxy_log_to_conversation,
)

# ─── one session, two capture shapes ────────────────────────────────────────

# The shared turns: an assistant turn (reasoning + text + tool_use), the user's
# tool_result, and the final assistant turn.
_STREAM_EVENTS: list[dict[str, object]] = [
    {"type": "system", "subtype": "init"},
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "look", "signature": "sig"},
                {"type": "text", "text": "editing"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "a.py\nb.py",
                    "is_error": False,
                },
            ],
        },
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        },
    },
    {"type": "result", "subtype": "success", "is_error": False},
]

# The proxy log for the same session. Anthropic is stateless, so the LAST
# request's body.messages holds the prior turns and response.message is the
# final assistant turn. An earlier stub record is present to prove only the last
# record is read.
_PROXY_RECORDS: list[dict[str, object]] = [
    {"request": {"body": {}}, "response": {"message": {}}, "complete": False},
    {
        "request": {
            "headers": {"x-api-key": "secret"},
            "body": {
                "model": "claude-sonnet-5",
                "system": "You are helpful.",
                "tools": [{"name": "Bash"}],
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "look",
                                "signature": "sig",
                            },
                            {"type": "text", "text": "editing"},
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": "a.py\nb.py",
                                "is_error": False,
                            },
                        ],
                    },
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
            },
        },
        "complete": True,
    },
]


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
  path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _non_system(conv: Conversation) -> Conversation:
  return Conversation(
      messages=[m for m in conv.messages if m.role is not Role.SYSTEM]
  )


def test_proxy_conversion_maps_the_expected_messages(tmp_path: Path):
  raw = tmp_path / "claude.proxy.jsonl"
  _write_jsonl(raw, _PROXY_RECORDS)
  conv = proxy_log_to_conversation(raw)
  assert conv == Conversation(
      messages=[
          Message(
              role=Role.SYSTEM,
              content=[TextBlock(text="You are helpful.")],
          ),
          Message(
              role=Role.ASSISTANT,
              content=[
                  ReasoningBlock(text="look", signature="sig"),
                  TextBlock(text="editing"),
                  ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
              ],
          ),
          Message(
              role=Role.USER,
              content=[
                  ToolResultBlock(
                      tool_use_id="t1", content="a.py\nb.py", is_error=False
                  )
              ],
          ),
          Message(role=Role.ASSISTANT, content=[TextBlock(text="done")]),
      ]
  )


def test_proxy_capture_is_equivalent_to_stream(tmp_path: Path):
  stream = tmp_path / "claude.event_stream.jsonl"
  stream.write_text("\n".join(json.dumps(e) for e in _STREAM_EVENTS) + "\n")
  proxy = tmp_path / "claude.proxy.jsonl"
  _write_jsonl(proxy, _PROXY_RECORDS)
  # the proxy additionally carries the SYSTEM turn; the shared user/assistant
  # surface is identical — proven by construction (same block mappers).
  assert _non_system(proxy_log_to_conversation(proxy)) == (
      event_stream_to_conversation(stream)
  )


def test_proxy_conversion_absent_file_is_empty(tmp_path: Path):
  assert proxy_log_to_conversation(tmp_path / "nope.jsonl") == Conversation(
      messages=[]
  )


def test_proxy_complete_reads_last_record(tmp_path: Path):
  raw = tmp_path / "claude.proxy.jsonl"
  _write_jsonl(raw, _PROXY_RECORDS)
  assert proxy_log_complete(raw) is True
  # last record's flag wins over the earlier stub's False
  _write_jsonl(raw, [_PROXY_RECORDS[1], _PROXY_RECORDS[0]])
  assert proxy_log_complete(raw) is False
  assert proxy_log_complete(tmp_path / "nope.jsonl") is False
