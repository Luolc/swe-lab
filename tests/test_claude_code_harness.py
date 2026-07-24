"""Tests for the claude_code harness: mounts, assets, invocation, conversion."""

import json
from pathlib import Path

from swe_lab.conversation import (
    Conversation,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from swe_lab.harnesses.claude_code import (
    ClaudeCodeHarness,
    event_stream_complete,
    event_stream_to_conversation,
)
from swe_lab.harnesses.claude_code.constants import (
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    EVENT_STREAM_NAME,
)
from swe_lab.sandbox import Inline, LocalFile, Sandbox, SandboxSpec
from swe_lab.sandbox.testing import FakeBackend

_EVENTS: list[dict[str, object]] = [
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


def _write_stream(path: Path, events: list[dict[str, object]]) -> None:
  path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _script(workdir: str) -> str:
  mount = ClaudeCodeHarness().mounts(workdir)[AGENT_SCRIPT_NAME]
  assert isinstance(mount.resource, Inline)
  return mount.resource.content.decode()


def test_mounts_only_agent_script_no_prompt():
  mounts = ClaudeCodeHarness().mounts("/app")
  assert set(mounts) == {AGENT_SCRIPT_NAME}  # NOT the prompt (dataset's)
  assert mounts[AGENT_SCRIPT_NAME].executable is True


def test_invocation_script_shape_and_quoting():
  script = _script("/weird dir")
  assert "export HOME=/tmp/agent-home" in script
  assert "export IS_SANDBOX=1" in script
  assert "cd '/weird dir'" in script  # shlex.quote'd workdir with a space
  assert f'{BINARY_AT} -p "$(cat "$SANDBOX_WORKSPACE"/prompt.txt)"' in script
  assert "--output-format stream-json --verbose" in script
  assert "--dangerously-skip-permissions" in script
  assert '> "$SANDBOX_WORKSPACE"/event_stream.jsonl' in script
  assert script.rstrip().endswith("|| true")


def test_assets_binary_at_fixed_path(tmp_path: Path):
  binary = tmp_path / "claude"
  _ = binary.write_bytes(b"BIN")
  assets = ClaudeCodeHarness(binary_path=binary).assets()
  assert assets == {BINARY_AT: LocalFile(binary)}


def test_native_outputs():
  assert ClaudeCodeHarness().native_outputs() == {
      "event_stream": "event_stream.jsonl",
      "agent_stderr": "agent.stderr",
  }


def test_run_executes_agent_script(tmp_path: Path):
  backend = FakeBackend()
  sb = Sandbox(
      label="x",
      spec=SandboxSpec("x", "img:tag", "/app", "abc"),
      workspace=tmp_path,
      backend=backend,
      handle="fake",
  )
  ClaudeCodeHarness().run(sb, timeout=30.0)
  assert backend.scripts == [AGENT_SCRIPT_NAME]


def test_to_conversation_maps_roles_and_blocks(tmp_path: Path):
  raw = tmp_path / EVENT_STREAM_NAME
  _write_stream(raw, _EVENTS)
  conv = event_stream_to_conversation(raw)

  assert [m.role for m in conv.messages] == [
      Role.ASSISTANT,
      Role.USER,
      Role.ASSISTANT,
  ]
  first = conv.messages[0].content
  assert first[0] == ReasoningBlock(text="look", signature="sig")
  assert first[1] == TextBlock(text="editing")
  assert first[2] == ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})
  result = conv.messages[1].content[0]
  assert result == ToolResultBlock(
      tool_use_id="t1", content="a.py\nb.py", is_error=False
  )


def test_to_conversation_absent_file_is_empty(tmp_path: Path):
  assert event_stream_to_conversation(tmp_path / "nope.jsonl") == Conversation(
      messages=[]
  )


def test_event_stream_complete(tmp_path: Path):
  ok = tmp_path / "ok.jsonl"
  _write_stream(ok, _EVENTS)
  assert event_stream_complete(ok) is True

  errored = tmp_path / "err.jsonl"
  _write_stream(
      errored, [{"type": "result", "subtype": "error", "is_error": True}]
  )
  assert event_stream_complete(errored) is False

  assert event_stream_complete(tmp_path / "absent.jsonl") is False
