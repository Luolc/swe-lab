"""Tests for the ``grok`` harness: invocation script, trace, outcome, assets.

The failure-path fixtures are **real** — captured from a live grok 1.0.0
headless run (2026-08-11), trimmed but not reshaped — so the delegation to the
``claude_code`` converter is pinned against what the binary actually emits.
"""

from __future__ import annotations

import json
from pathlib import Path

from etils import epath
import pytest

from swe_lab.harnesses import AgentOutcome, registered_harnesses
from swe_lab.harnesses.common import home_fallback_lines
from swe_lab.harnesses.grok_build import (
    event_stream_outcome,
    event_stream_to_conversation,
    GrokBuildAuthObserver,
    GrokBuildHarness,
)
from swe_lab.harnesses.grok_build.binary import (
    binary_checksum,
    BINARY_SHA256,
    binary_url,
    LINUX_X64,
    PINNED_GROK_BUILD_VERSION,
)
from swe_lab.harnesses.grok_build.constants import (
    AGENT_SCRIPT_NAME,
    EVENT_STREAM_NAME,
    grok_config_dir,
)
from swe_lab.sandbox import Inline, SandboxError, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

# Captured verbatim from a live `grok -p … --output-format
# streaming-messages-json` run (auth-failed, which is exactly why it is a good
# fixture: it proves the terminal error shape). Note the schema: Claude Code's
# stream-json, which is the whole basis for the converter delegation.
_INIT_EVENT: dict[str, object] = {
    "type": "system",
    "subtype": "init",
    "session_id": "",
    "apiKeySource": "user",
    "model": "unknown",
    "cwd": "",
    "permissionMode": "default",
    "tools": [],
    "slash_commands": [],
    "mcp_servers": [],
    "skills": [],
    "uuid": "2e148a59-e904-4a92-b062-a4823abd77e0",
}
_ERROR_RESULT: dict[str, object] = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "duration_ms": 0,
    "num_turns": 0,
    "stop_reason": None,
    "total_cost_usd": 0.0,
    "errors": ["Not signed in. To authenticate without a browser, run: ..."],
}
# The shapes a healthy run emits (Anthropic Messages wire format, per the
# output-format name and the claude_code schema it matches).
_ASSISTANT_EVENT: dict[str, object] = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "PONG"}],
    },
}
_SUCCESS_RESULT: dict[str, object] = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 1,
}


def _stream(events: list[dict[str, object]]) -> str:
  return "".join(json.dumps(e) + "\n" for e in events)


def _script(harness: GrokBuildHarness, workdir: str = "/app") -> str:
  mount = harness.mounts(workdir)[AGENT_SCRIPT_NAME]
  assert isinstance(mount.resource, Inline)
  return mount.resource.content.decode()


def _sandbox(workspace: Path) -> FakeSandbox:
  """Build a docker-free sandbox whose file ops hit a real local dir."""
  return FakeSandbox(
      spec=SandboxSpec("grok__probe-1", "img:tag", "/app", "base"),
      workspace=epath.Path(workspace),
  )


# ─── the converter delegation ────────────────────────────────────────────────


def test_the_captured_failure_classifies_as_a_retryable_execution_error():
  # THE fixture this file exists for: real 1.0.0 output, fed through the
  # delegated claude_code classifier. An auth failure is ours (ADR-0011).
  raw = _stream([_INIT_EVENT, _ERROR_RESULT])
  assert event_stream_outcome(raw) is AgentOutcome.EXECUTION_ERROR
  assert AgentOutcome.EXECUTION_ERROR.retryable is True
  # ...and a trace with no user/assistant messages converts to an empty
  # conversation rather than raising.
  assert event_stream_to_conversation(raw).messages == []


def test_a_clean_finish_and_a_message_convert_through_the_delegation():
  raw = _stream([_INIT_EVENT, _ASSISTANT_EVENT, _SUCCESS_RESULT])
  assert event_stream_outcome(raw) is AgentOutcome.FINISHED
  conversation = event_stream_to_conversation(raw)
  assert [m.role.value for m in conversation.messages] == ["assistant"]
  assert conversation.messages[0].content[0].type == "text"


def test_max_turns_is_reachable_for_this_harness():
  # Grok HAS --max-turns (Codex does not), so the error_max_turns subtype is a
  # real ending here and must map to the non-retryable budget outcome.
  raw = _stream(
      [
          _INIT_EVENT,
          {"type": "result", "subtype": "error_max_turns", "is_error": True},
      ]
  )
  assert event_stream_outcome(raw) is AgentOutcome.MAX_TURNS
  assert AgentOutcome.MAX_TURNS.retryable is False


def test_absent_and_truncated_traces_read_as_such():
  assert event_stream_outcome("") is AgentOutcome.NO_OUTPUT
  assert (
      event_stream_outcome(_stream([_INIT_EVENT, _ASSISTANT_EVENT]))
      is AgentOutcome.TRUNCATED
  )


def test_completed_is_derived_from_the_outcome(tmp_path: Path):
  sb = _sandbox(tmp_path)
  sb.write(EVENT_STREAM_NAME, _stream([_INIT_EVENT, _SUCCESS_RESULT]).encode())
  harness = GrokBuildHarness()
  assert harness.outcome(sb) is AgentOutcome.FINISHED
  assert harness.completed(sb) is True


# ─── the invocation script ───────────────────────────────────────────────────


def test_the_script_runs_unattended_and_reports_status_out_of_band():
  script = _script(GrokBuildHarness())
  assert "--permission-mode bypassPermissions" in script
  assert "--output-format streaming-messages-json" in script
  assert (
      "--prompt-file" in script
  )  # Grok Build's native prompt delivery — no stdin
  # The agent's own status is PROPAGATED, not flattened to 0. Nothing gates on
  # it (no backend raises on a non-zero exec; RunStatus is not derived from
  # it), so this costs no behaviour change and makes the recorded metric real.
  assert script.rstrip().endswith('exit "$status"')
  assert "grok.exit_code" in script  # ...so the real status goes to a file


def test_no_leader_process_survives_the_run():
  # The leader is a shared backend for interactive clients; a one-shot
  # container run must not leave a socket-holding daemon behind the exec.
  assert "--no-leader" in _script(GrokBuildHarness())


def test_model_effort_and_turns_are_pinned_by_default():
  script = _script(GrokBuildHarness())
  assert "--model grok-4.5" in script  # the pinned build's measured default
  assert "--reasoning-effort high" in script  # a real flag, unlike codex
  assert "--max-turns 500" in script
  bare = _script(GrokBuildHarness(model=None, effort=None))
  assert "--model" not in bare
  assert "--reasoning-effort" not in bare


def test_the_home_layout_matches_the_other_harnesses():
  # One knob; the config dir derives from it, so the harness and the auth
  # observer cannot disagree about where grok will look.
  assert grok_config_dir() == "/agent-home/.grok"
  script = _script(GrokBuildHarness())
  # HOME is TIERED, not overridden (#240): the image's value wins, warm
  # toolchain caches with it; config isolation rides the pinned dir below,
  # not HOME.
  for line in home_fallback_lines():
    assert line in script
  assert "export HOME=/agent-home" not in script
  # GROK_HOME pins the config dir grok would otherwise derive from HOME.
  assert "export GROK_HOME=/agent-home/.grok" in script
  assert "mkdir -p /agent-home/.grok" in script


def test_the_harness_stages_no_binary():
  # The binary is the backend's to place (ADR-0003).
  assert set(GrokBuildHarness().mounts("/app")) == {
      AGENT_SCRIPT_NAME,
      "agent_env.sh",
  }


def test_an_invalid_env_name_is_refused_not_silently_dropped(tmp_path: Path):
  with pytest.raises(SandboxError, match="invalid environment variable"):
    _ = GrokBuildHarness().run(
        _sandbox(tmp_path), prompt="p", timeout=1.0, env={"not a name": "v"}
    )


# ─── bare mode ───────────────────────────────────────────────────────────────


def test_bare_is_on_by_default_and_closes_every_door_that_has_a_switch():
  """Grok Build's bare covers what grok CAN switch off (task-29 §6).

  The repo-injection doors with real switches: plan mode, subagents,
  cross-session memory, and web search/fetch — the last of which is also an
  egress door ADR-0010 wants shut regardless.
  """
  assert GrokBuildHarness().bare is True
  script = _script(GrokBuildHarness())
  for flag in (
      "--no-plan",
      "--no-subagents",
      "--no-memory",
      "--disable-web-search",
  ):
    assert flag in script, flag


def test_bare_off_leaves_the_doors_open():
  script = _script(GrokBuildHarness(bare=False))
  for flag in ("--no-plan", "--no-subagents", "--no-memory"):
    assert flag not in script, flag


def test_the_agents_md_door_is_documented_as_open():
  # No flag or config key gates a repo's AGENTS.md (it injects as a prepended
  # user message; --system-prompt-override cannot remove it). The mitigation
  # is detection — the injected message is visible in the captured trace,
  # unlike Claude Code's reminders. This test pins the DOCUMENTATION so the
  # gap cannot silently vanish from the docstring while remaining in the
  # binary.
  doc = GrokBuildHarness.__doc__ or ""
  assert "AGENTS.md" in doc
  assert "detection" in doc


# ─── endpoint override ───────────────────────────────────────────────────────


def test_base_url_is_a_flag_and_never_carries_the_key():
  script = _script(GrokBuildHarness(base_url="https://gw.internal/v1"))
  assert "--xai-api-base-url https://gw.internal/v1" in script
  # The key travels only by pass_env; nothing key-like belongs in the script.
  assert "XAI_API_KEY=" not in script
  assert "--xai-api-base-url" not in _script(GrokBuildHarness())


def test_extra_flags_come_last_so_they_can_correct_anything():
  script = _script(GrokBuildHarness(extra_flags=("--verbatim",)))
  assert script.index("--disable-web-search") < script.index("--verbatim")


# ─── provisioning ────────────────────────────────────────────────────────────


def test_one_binary_no_companion():
  # Codex's trap does not apply here: grok spawns no code-mode-host analogue
  # (verified by a live tool-using run), so the cache path is a FILE and the
  # pin table needs exactly one entry per (version, platform).
  assert (PINNED_GROK_BUILD_VERSION, LINUX_X64) in BINARY_SHA256
  url = binary_url()
  assert url.endswith(f"grok-{PINNED_GROK_BUILD_VERSION}-{LINUX_X64}")
  assert ".tar" not in url  # a bare binary, not an archive


def test_an_unpinned_version_is_refused_rather_than_fetched_unverified():
  # The official installer verifies nothing, so the in-repo pin IS the whole
  # verification story; fetching without one would be an unverified download.
  with pytest.raises(ValueError, match="no pinned sha256"):
    _ = binary_checksum("0.0.0", LINUX_X64)


# ─── auth ────────────────────────────────────────────────────────────────────


def test_the_login_is_staged_inline_under_the_derived_grok_dir(tmp_path: Path):
  observer = GrokBuildAuthObserver(auth_json=b'{"scope": {}}')
  target, mount = next(iter(observer.mounts().items()))
  assert target == "/agent-home/.grok/auth.json"
  assert isinstance(mount.resource, Inline)
  # Writable on purpose: grok refreshes the token and writes the file back.
  assert mount.read_only is False

  path = tmp_path / "auth.json"
  _ = path.write_bytes(b'{"scope": {"refresh_token": "x"}}')
  assert GrokBuildAuthObserver.from_file(path).auth_json == path.read_bytes()


def test_the_credential_is_never_shown_in_a_repr():
  observer = GrokBuildAuthObserver(auth_json=b'{"scope": {"key": "sEcReT"}}')
  assert "sEcReT" not in repr(observer)
  assert "auth_json" not in repr(observer)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"   ", "is empty"),
        (b"not json", "not valid JSON"),
        (b'["a list"]', "must be a JSON object"),
    ],
)
def test_an_unusable_credential_is_refused_on_the_host(
    payload: bytes, message: str
):
  with pytest.raises(SandboxError, match=message):
    _ = GrokBuildAuthObserver(auth_json=payload)


def test_a_missing_login_fails_before_a_container_is_paid_for(tmp_path: Path):
  with pytest.raises(SandboxError, match="grok auth file not found"):
    _ = GrokBuildAuthObserver.from_file(tmp_path / "nope.json")


# ─── registration ────────────────────────────────────────────────────────────


def test_grok_build_is_selectable_by_name_through_the_cli():

  from swe_lab.cli.overrides import apply_overrides, parse_overrides
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.workflow.registry import workflow_definition

  assert "grok_build" in registered_harnesses()
  entries = apply_overrides(
      workflow_definition("rollout"),
      parse_overrides(
          ["--rollout.harness=grok_build", "--rollout.harness.effort=low"]
      ),
  )
  task = entries[0].task
  assert isinstance(task, CodingAgentTask)
  assert isinstance(task.harness, GrokBuildHarness)
  assert task.harness.effort == "low"


def test_a_field_of_another_agent_is_refused_with_the_valid_ones():

  from swe_lab.cli.overrides import (
      apply_overrides,
      OverrideError,
      parse_overrides,
  )
  from swe_lab.workflow.registry import workflow_definition

  with pytest.raises(OverrideError, match="not a field of GrokBuildHarness"):
    _ = apply_overrides(
        workflow_definition("rollout"),
        parse_overrides(
            ["--rollout.harness=grok_build", "--rollout.harness.capture=proxy"]
        ),
    )
