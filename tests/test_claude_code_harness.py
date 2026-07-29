"""Tests for the claude_code harness: mounts, binary, invocation, conversion."""

import json
from pathlib import Path

from etils import epath
import pytest

from swe_lab.conversation import (
    Conversation,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from swe_lab.harnesses.claude_code import (
    Capture,
    ClaudeCodeHarness,
    event_stream_complete,
    event_stream_to_conversation,
)
from swe_lab.harnesses.claude_code.constants import (
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    PROXY_LOG_NAME,
)
from swe_lab.sandbox import (
    Inline,
    LocalFile,
    Mount,
    SandboxError,
    SandboxSpec,
)
from swe_lab.sandbox.testing import FakeSandbox

_SPEC = SandboxSpec("x", "img:tag", "/app", "abc")

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


@pytest.fixture(autouse=True)
def _fake_binary(  # pyright: ignore[reportUnusedFunction]  # autouse fixture
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
  """Stand in for the pinned binary so ``mounts`` never provisions (network)."""
  binary = tmp_path / "claude"
  _ = binary.write_bytes(b"BIN")
  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.harness.ensure_claude_binary",
      lambda: binary,
  )
  return binary


def _stream_text(events: list[dict[str, object]]) -> str:
  return "\n".join(json.dumps(e) for e in events) + "\n"


def _script(workdir: str, harness: ClaudeCodeHarness | None = None) -> str:
  mount = (harness or ClaudeCodeHarness()).mounts(workdir)[AGENT_SCRIPT_NAME]
  assert isinstance(mount.resource, Inline)
  return mount.resource.content.decode()


def test_mounts_stage_agent_script_and_binary_not_prompt():
  mounts = ClaudeCodeHarness().mounts("/app")
  # the agent script and the pinned binary — NOT the prompt (dataset's)
  assert set(mounts) == {AGENT_SCRIPT_NAME, BINARY_AT}
  assert mounts[AGENT_SCRIPT_NAME].executable is True


def test_invocation_script_shape_and_quoting():
  script = _script("/weird dir")
  assert "export HOME=/agent-home" in script
  assert "export IS_SANDBOX=1" in script
  assert "cd '/weird dir'" in script  # shlex.quote'd workdir with a space
  assert f"{BINARY_AT} -p " in script  # no inline prompt in the argv
  assert "--bare" not in script  # bare defaults off
  assert "--output-format stream-json --verbose" in script
  assert "--dangerously-skip-permissions" in script
  # the prompt is piped in on stdin (no shell-quoting hazard)
  assert '< "$SANDBOX_WORKSPACE"/prompt.txt' in script
  assert '> "$SANDBOX_WORKSPACE"/claude.event_stream.jsonl' in script
  assert script.rstrip().endswith("|| true")


def test_bare_flag_added_when_set():
  script = _script("/app", ClaudeCodeHarness(bare=True))
  assert f"{BINARY_AT} -p --bare " in script


def test_binary_is_a_read_only_executable_mount_at_fixed_path(
    _fake_binary: Path,
):
  # the pinned binary is now a read-only executable mount (ADR-0003: an asset is
  # just a read-only mount), not a separate assets() seam
  binary_mount = ClaudeCodeHarness().mounts("/app")[BINARY_AT]
  assert binary_mount == Mount(
      LocalFile(epath.Path(_fake_binary)), executable=True, read_only=True
  )


def test_native_outputs():
  assert ClaudeCodeHarness().native_outputs() == {
      "event_stream": "claude.event_stream.jsonl",
      "stderr": "claude.stderr",
  }


# ─── proxy capture ──────────────────────────────────────────────────────────


def _proxy_harness() -> ClaudeCodeHarness:
  return ClaudeCodeHarness(
      capture=Capture.PROXY, proxy_base_url="http://host.docker.internal:20001"
  )


def test_proxy_invocation_routes_through_base_url_and_discards_stdout():
  script = _script("/app", _proxy_harness())
  assert "export ANTHROPIC_BASE_URL=http://host.docker.internal:20001" in script
  assert "--output-format json" in script  # not stream-json in proxy mode
  assert "> /dev/null" in script  # agent stdout is not the trace
  assert "claude.event_stream.jsonl" not in script  # no stream redirect
  assert '< "$SANDBOX_WORKSPACE"/prompt.txt' in script  # prompt still on stdin


def test_proxy_capture_without_base_url_raises():
  with pytest.raises(SandboxError, match="proxy_base_url"):
    _ = ClaudeCodeHarness(capture=Capture.PROXY).mounts("/app")


def test_proxy_native_outputs_registers_proxy_log():
  assert _proxy_harness().native_outputs() == {
      "proxy_log": PROXY_LOG_NAME,
      "stderr": "claude.stderr",
  }


def test_proxy_to_conversation_reads_proxy_log(tmp_path: Path):
  # the proxy branch reads the proxy log, not the (absent) event stream
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  (tmp_path / PROXY_LOG_NAME).write_text(
      json.dumps(
          {
              "request": {
                  "body": {
                      "messages": [
                          {
                              "role": "user",
                              "content": [{"type": "text", "text": "hi"}],
                          }
                      ]
                  }
              },
              "response": {
                  "message": {
                      "role": "assistant",
                      "content": [{"type": "text", "text": "yo"}],
                  }
              },
              "complete": True,
          }
      )
      + "\n"
  )
  conv = _proxy_harness().to_conversation(sb)
  assert [m.role.value for m in conv.messages] == ["user", "assistant"]


def test_run_executes_agent_script(tmp_path: Path):
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  ClaudeCodeHarness().run(sb, timeout=30.0)
  assert sb.scripts == [AGENT_SCRIPT_NAME]


def test_to_conversation_maps_roles_and_blocks():
  conv = event_stream_to_conversation(_stream_text(_EVENTS))

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


def test_to_conversation_empty_text_is_empty():
  # an absent event stream reaches the converter as "" (the harness reads the
  # file and passes its text, or "" when it never landed)
  assert event_stream_to_conversation("") == Conversation(messages=[])


def test_event_stream_complete():
  assert event_stream_complete(_stream_text(_EVENTS)) is True

  errored = _stream_text(
      [{"type": "result", "subtype": "error", "is_error": True}]
  )
  assert event_stream_complete(errored) is False

  assert event_stream_complete("") is False
