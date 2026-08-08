"""Tests for the ``codex`` harness: invocation script, trace, outcome, assets.

The event fixtures are **real** — captured from live ``codex exec --json`` runs
of the pinned 0.147.0 build (2026-08-08), trimmed but not reshaped — so the
converter is pinned against what the agent actually emits rather than against
what its source suggests it should.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from etils import epath
import pytest

from swe_lab.conversation import TextBlock, ToolResultBlock, ToolUseBlock
from swe_lab.harnesses import AgentOutcome, registered_harnesses
from swe_lab.harnesses.codex import (
    CodexAuthObserver,
    CodexHarness,
    event_stream_outcome,
    event_stream_to_conversation,
)
from swe_lab.harnesses.codex.binary import (
    archive_checksum,
    archive_url,
    BINARY_STEMS,
    PINNED_CODEX_VERSION,
    release_tag,
)
from swe_lab.harnesses.codex.constants import (
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    CODE_MODE_HOST_AT,
    EVENT_STREAM_NAME,
)
from swe_lab.sandbox import Inline, SandboxError, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

# One captured turn: a command that read a file, a patch that landed, and the
# agent's closing message.
_EVENTS: list[dict[str, Any]] = [
    {"type": "thread.started", "thread_id": "019fe364-b50e-7be2"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "agent_message",
            "text": "I'll inspect the module, then make the edit.",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "/bin/bash -lc \"sed -n '1,200p' calc.py\"",
            "aggregated_output": "def add(a, b):\n    return a + b\n",
            "exit_code": 0,
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "file_change",
            "changes": [{"path": "/work/calc.py", "kind": "update"}],
            "status": "completed",
        },
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 13741, "output_tokens": 6},
    },
]


def _stream(events: Sequence[Mapping[str, Any]]) -> str:
  return "".join(json.dumps(e) + "\n" for e in events)


def _sandbox(workspace: Path) -> FakeSandbox:
  """Build a docker-free sandbox whose file ops hit a real local dir."""
  return FakeSandbox(
      spec=SandboxSpec("codex__probe-1", "img:tag", "/app", "base"),
      workspace=epath.Path(workspace),
  )


def _script(harness: CodexHarness, workdir: str = "/app") -> str:
  mount = harness.mounts(workdir)[AGENT_SCRIPT_NAME]
  assert isinstance(mount.resource, Inline)
  return mount.resource.content.decode()


# ─── the trace converter ─────────────────────────────────────────────────────


def test_a_tool_item_becomes_a_call_and_a_result():
  # Codex packs a command and its output into ONE item; the canonical model is
  # Anthropic-shaped, so it is expanded back into the assistant's call and the
  # user's result, sharing the item id. That is what lets a Codex trace and a
  # Claude Code trace of the same work read identically downstream.
  conversation = event_stream_to_conversation(_stream(_EVENTS))
  roles = [(m.role.value, m.content[0].type) for m in conversation.messages]
  assert roles == [
      ("assistant", "text"),
      ("assistant", "tool_use"),
      ("user", "tool_result"),
      ("assistant", "tool_use"),
      ("user", "tool_result"),
  ]
  call = conversation.messages[1].content[0]
  result = conversation.messages[2].content[0]
  assert isinstance(call, ToolUseBlock)
  assert isinstance(result, ToolResultBlock)
  assert call.name == "command_execution"
  assert call.input["command"].endswith('calc.py"')
  assert call.id == result.tool_use_id == "item_1"
  assert result.content == "def add(a, b):\n    return a + b\n"
  assert result.is_error is False


def test_a_failed_command_marks_its_result_as_an_error():
  events = [
      {
          "type": "item.completed",
          "item": {
              "id": "c1",
              "type": "command_execution",
              "command": "rg --files",
              "aggregated_output": "rg: command not found",
              "exit_code": 127,
              "status": "completed",
          },
      },
  ]
  conversation = event_stream_to_conversation(_stream(events))
  result = conversation.messages[1].content[0]
  assert isinstance(result, ToolResultBlock)
  assert result.is_error is True


def test_only_completed_items_convert():
  # Every item is emitted twice (started, then completed); converting both
  # would double every tool call in the trace.
  started = {
      "type": "item.started",
      "item": {"id": "x", "type": "agent_message", "text": "partial"},
  }
  assert event_stream_to_conversation(_stream([started])).messages == []


def test_an_empty_trace_converts_to_an_empty_conversation():
  assert event_stream_to_conversation("").messages == []


# ─── the outcome classifier (ADR-0011) ───────────────────────────────────────


def test_a_completed_turn_is_a_clean_finish():
  assert event_stream_outcome(_stream(_EVENTS)) is AgentOutcome.FINISHED
  assert AgentOutcome.FINISHED.retryable is False


def test_an_item_level_error_does_not_fail_the_run():
  """THE regression guard, and a real 0.147.0 behavior.

  A live run emits an ``item.completed`` of type ``error`` — a degraded
  optional feature — on a turn that then completes perfectly. Reading items
  when classifying would report that healthy run as failed and, because
  EXECUTION_ERROR is retryable, spend the budget re-running it.
  """
  noisy = [
      _EVENTS[0],
      {
          "type": "item.completed",
          "item": {
              "id": "item_0",
              "type": "error",
              "message": "Code Mode is unavailable because ...",
          },
      },
      *_EVENTS[1:],
  ]
  assert event_stream_outcome(_stream(noisy)) is AgentOutcome.FINISHED
  # ...and it is still visible in the conversation, not silently dropped.
  texts = [
      b.text
      for m in event_stream_to_conversation(_stream(noisy)).messages
      for b in m.content
      if isinstance(b, TextBlock)
  ]
  assert any("Code Mode is unavailable" in t for t in texts)


@pytest.mark.parametrize(
    "terminal",
    [
        {"type": "turn.failed", "error": {"message": "stream disconnected"}},
        {"type": "error", "message": "invalid peer certificate"},
    ],
)
def test_a_broken_turn_is_a_retryable_execution_error(
    terminal: Mapping[str, Any],
):
  # Both shapes were observed live (a 400 from the API, and a TLS failure).
  # Infrastructure, so ADR-0011 says retry.
  raw = _stream([_EVENTS[0], _EVENTS[1], terminal])
  assert event_stream_outcome(raw) is AgentOutcome.EXECUTION_ERROR
  assert AgentOutcome.EXECUTION_ERROR.retryable is True


def test_a_trace_that_stops_mid_turn_is_truncated():
  raw = _stream([_EVENTS[0], _EVENTS[1], _EVENTS[2]])
  assert event_stream_outcome(raw) is AgentOutcome.TRUNCATED


def test_no_trace_at_all_is_no_output():
  assert event_stream_outcome("") is AgentOutcome.NO_OUTPUT


def test_completed_is_derived_from_the_outcome(tmp_path: Path):
  # `Harness.completed` is @final and derives, so the bit cannot drift.
  sb = _sandbox(tmp_path)
  sb.write(EVENT_STREAM_NAME, _stream(_EVENTS).encode())
  harness = CodexHarness()
  assert harness.outcome(sb) is AgentOutcome.FINISHED
  assert harness.completed(sb) is True


def test_an_absent_trace_reads_as_no_output_rather_than_raising(
    tmp_path: Path,
):
  # A crashed run is a legitimate outcome to report, not an exception.
  assert CodexHarness().outcome(_sandbox(tmp_path)) is AgentOutcome.NO_OUTPUT


# ─── the invocation script ───────────────────────────────────────────────────


def test_the_script_runs_unattended_and_reports_status_out_of_band():
  script = _script(CodexHarness())
  # Codex's own sandbox is bypassed: the container IS the sandbox, and bwrap
  # needs user namespaces that are commonly unavailable inside one.
  assert "--dangerously-bypass-approvals-and-sandbox" in script
  assert "--json" in script  # the trace is the agent's own stdout
  assert script.rstrip().endswith("exit 0")  # teardown must not change
  assert "codex.exit_code" in script  # ...so the real status goes to a file
  assert '< "$SANDBOX_WORKSPACE"/prompt.txt' in script  # prompt on stdin


def test_no_model_flag_unless_one_is_pinned():
  # Measured: pinning an API-tier model fails a ChatGPT login with a 400 before
  # the first turn, so the default defers to whatever the account allows.
  assert "--model" not in _script(CodexHarness())
  assert "--model gpt-5.6-terra" in _script(CodexHarness(model="gpt-5.6-terra"))


def test_extra_config_overrides_are_passed_through():
  script = _script(CodexHarness(extra_config=("features.code_mode_host=true",)))
  assert "-c features.code_mode_host=true" in script


def test_the_harness_stages_no_binary():
  # The binaries are the backend's to place (ADR-0003): a Docker sandbox copies
  # from a host cache, a CI job downloads in place, and a remote sandbox
  # declares the mount in its config before it comes up.
  staged = set(CodexHarness().mounts("/app"))
  assert staged == {AGENT_SCRIPT_NAME, "agent_env.sh"}


def test_an_invalid_env_name_is_refused_not_silently_dropped(tmp_path: Path):
  # Corrupting the sourced env file would make `set -u` report "the agent never
  # ran" with no clue why, so this fails at the boundary instead.
  with pytest.raises(SandboxError, match="invalid environment variable"):
    _ = CodexHarness().run(
        _sandbox(tmp_path),
        prompt="p",
        timeout=1.0,
        env={"not a name": "v"},
    )


# ─── provisioning ────────────────────────────────────────────────────────────


def test_both_binaries_are_required_not_just_codex():
  # The failure this guards is silent: with the code-mode host absent, codex
  # starts, authenticates, answers and exits 0 — having been unable to run a
  # command or edit a file.
  assert BINARY_STEMS == ("codex", "codex-code-mode-host")
  # ...and the helper's path is derived by Codex as a sibling of the binary,
  # so the two constants may not drift apart.
  assert str(Path(CODE_MODE_HOST_AT).parent) == str(Path(BINARY_AT).parent)


def test_release_urls_point_at_the_bare_per_binary_assets():
  url = archive_url("codex", version="0.147.0")
  assert url.endswith("rust-v0.147.0/codex-x86_64-unknown-linux-musl.tar.gz")
  # NOT the -bundle or -package variants: those carry bwrap, a Python runtime
  # and a packaged zsh that this harness never runs.
  assert "-bundle" not in url and "-package" not in url
  assert release_tag("0.147.0") == "rust-v0.147.0"


def test_every_pinned_binary_has_a_pinned_checksum():
  # Upstream's SHA256SUMS does not cover these assets, so the pin here IS the
  # verification; a stem without one must fail loudly rather than download
  # unverified bytes.
  for stem in BINARY_STEMS:
    assert archive_checksum(
        stem, PINNED_CODEX_VERSION, "x86_64-unknown-linux-musl"
    )
  with pytest.raises(ValueError, match="no pinned sha256"):
    _ = archive_checksum("codex", "0.0.0", "x86_64-unknown-linux-musl")


def test_the_auth_observer_stages_the_login_under_codex_home(tmp_path: Path):
  auth = tmp_path / "auth.json"
  _ = auth.write_text("{}")
  mounts = CodexAuthObserver(auth_file=auth, codex_home="/codex-home").mounts()
  assert set(mounts) == {"/codex-home/auth.json"}
  # Writable on purpose: Codex refreshes its token and writes the file back.
  assert mounts["/codex-home/auth.json"].read_only is False


def test_a_missing_login_fails_before_a_container_is_paid_for(tmp_path: Path):
  with pytest.raises(SandboxError, match="codex auth file not found"):
    _ = CodexAuthObserver(auth_file=tmp_path / "nope.json")


def test_the_harness_registers_itself_by_name():
  assert "codex" in registered_harnesses()
