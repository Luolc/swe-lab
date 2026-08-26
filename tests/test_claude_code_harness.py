"""Tests for the claude_code harness: mounts, binary, invocation, conversion."""

import json
from pathlib import Path
from typing import get_args

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
from swe_lab.harnesses import AgentOutcome
from swe_lab.harnesses.claude_code import (
    Capture,
    ClaudeCodeHarness,
    Effort,
    event_stream_outcome,
    event_stream_to_conversation,
)
from swe_lab.harnesses.claude_code.constants import (
    AGENT_ENV_NAME,
    AGENT_INFO_NAME,
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    INFO_ARTIFACT,
    PROXY_LOG_NAME,
)
from swe_lab.harnesses.common import AgentInfoObserver, home_fallback_lines
from swe_lab.sandbox import (
    Contribution,
    ExecResult,
    Inline,
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


def _stream_text(events: list[dict[str, object]]) -> str:
  return "\n".join(json.dumps(e) for e in events) + "\n"


def _script(workdir: str, harness: ClaudeCodeHarness | None = None) -> str:
  mount = (harness or ClaudeCodeHarness()).mounts(workdir)[AGENT_SCRIPT_NAME]
  assert isinstance(mount.resource, Inline)
  return mount.resource.content.decode()


def test_mounts_stage_agent_script_and_env_only():
  mounts = ClaudeCodeHarness().mounts("/app")
  # the agent script and its (empty) env file — NOT the prompt, which is the
  # dataset's and staged by the composition, and NOT the binary, which is the
  # backend's (see test_binary_is_never_staged_by_the_harness)
  assert set(mounts) == {AGENT_SCRIPT_NAME, AGENT_ENV_NAME}
  assert mounts[AGENT_SCRIPT_NAME].executable is True
  env_mount = mounts[AGENT_ENV_NAME]
  assert isinstance(env_mount.resource, Inline)
  assert env_mount.resource.content == b""  # filled in by run(env=...)


def test_invocation_script_shape_and_quoting():
  script = _script("/weird dir")
  # HOME is TIERED, not overridden (#240): the image's value wins, warm
  # toolchain caches with it; config isolation rides the pinned dir below,
  # not HOME.
  for line in home_fallback_lines():
    assert line in script
  assert "export HOME=/agent-home" not in script
  assert "export CLAUDE_CONFIG_DIR=/agent-home/.claude" in script
  assert "export IS_SANDBOX=1" in script
  assert "cd '/weird dir'" in script  # shlex.quote'd workdir with a space
  assert f"{BINARY_AT} -p " in script  # no inline prompt in the argv
  assert "--bare" in script  # bare is the default for an unattended run
  assert "--output-format stream-json --verbose" in script
  assert "--dangerously-skip-permissions" in script
  # every interactive/plan-mode tool denied — none is covered by
  # --dangerously-skip-permissions, and each hangs an unattended run
  assert (
      "--disallowedTools EnterPlanMode,ExitPlanMode,AskUserQuestion" in script
  )
  assert "--max-turns 500" in script  # agent-loop runaway guard
  # the agent's status is reported out-of-band; the script itself exits 0 so
  # container teardown is unchanged
  assert '> "$SANDBOX_WORKSPACE"/claude.exit_code' in script
  # The agent's own status is PROPAGATED, not flattened to 0. Nothing gates on
  # it (no backend raises on a non-zero exec; RunStatus is not derived from
  # it), so this costs no behaviour change and makes the recorded metric real.
  assert script.rstrip().endswith('exit "$status"')
  assert "|| true" not in script
  # the prompt is piped in on stdin (no shell-quoting hazard)
  assert '< "$SANDBOX_WORKSPACE"/prompt.txt' in script
  assert '> "$SANDBOX_WORKSPACE"/claude.event_stream.jsonl' in script


def test_bare_can_be_turned_off_for_an_oauth_composition():
  # Bare reads neither OAuth nor the keychain, so a composition authenticating
  # by CLAUDE_CODE_OAUTH_TOKEN must opt out — and then gets no API-key guard.
  script = _script("/app", ClaudeCodeHarness(bare=False))
  assert "--bare" not in script
  assert "ANTHROPIC_API_KEY" not in script


def test_bare_guards_the_only_credential_it_can_use():
  # Without the guard a missing key surfaces as a plain-text "Not logged in"
  # result on stdout — a successful-looking run that did nothing.
  script = _script("/app", ClaudeCodeHarness(bare=True))
  assert f"{BINARY_AT} -p --bare " in script
  assert 'if [ -z "${ANTHROPIC_API_KEY:-}" ]; then' in script
  assert "exit 78" in script


def test_proxy_capture_also_guards_its_base_url():
  script = _script("/app", _proxy_harness())
  assert 'if [ -z "${ANTHROPIC_BASE_URL:-}" ]; then' in script


def test_optional_bounds_are_omitted_unless_asked_for():
  # None means "leave the agent's own default alone", not "pass a default".
  plain = _script("/app")
  assert "--max-budget-usd" not in plain
  assert "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS" not in plain
  bounded = _script(
      "/app",
      ClaudeCodeHarness(max_budget_usd=2.5, subagent_wait_ceiling_ms=90_000),
  )
  assert "--max-budget-usd 2.5" in bounded
  assert "export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=90000" in bounded


def test_an_oversized_prompt_is_refused_before_it_is_staged(tmp_path: Path):
  # Claude Code caps piped stdin at 10 MB and exits non-zero past it; failing
  # here says so, instead of reading the cap back as an opaque agent failure.
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  with pytest.raises(SandboxError, match="caps piped stdin"):
    ClaudeCodeHarness().run(
        sb, prompt="x" * (10 * 1024 * 1024 + 1), timeout=1.0
    )
  assert not (tmp_path / "prompt.txt").exists()  # never staged


def test_binary_is_never_staged_by_the_harness():
  # The harness INVOKES the agent at the agreed path but never puts it there:
  # provisioning it is each backend's own call (its `observers()`), so this
  # must hold for every harness configuration — otherwise a backend that
  # downloads its own copy would collide with a mount it never wanted.
  for harness in (
      ClaudeCodeHarness(),
      ClaudeCodeHarness(bare=True),
      _proxy_harness(),
  ):
    assert BINARY_AT not in harness.mounts("/app")
    assert BINARY_AT in _script("/app", harness)  # …but it is still invoked


def test_native_outputs():
  assert ClaudeCodeHarness().native_outputs() == {
      "event_stream.jsonl": "claude.event_stream.jsonl",
      "stderr.log": "claude.stderr.log",
      "exit_code.txt": "claude.exit_code",
  }


# ─── proxy capture ──────────────────────────────────────────────────────────


def _proxy_harness() -> ClaudeCodeHarness:
  return ClaudeCodeHarness(
      capture="proxy", proxy_base_url="http://host.docker.internal:20001"
  )


def test_proxy_invocation_routes_through_base_url_and_discards_stdout():
  script = _script("/app", _proxy_harness())
  assert "export ANTHROPIC_BASE_URL=http://host.docker.internal:20001" in script
  assert "--output-format json" in script  # not stream-json in proxy mode
  assert "> /dev/null" in script  # agent stdout is not the trace
  assert "claude.event_stream.jsonl" not in script  # no stream redirect
  assert '< "$SANDBOX_WORKSPACE"/prompt.txt' in script  # prompt still on stdin


def test_proxy_capture_needs_no_url_and_composes_its_own_recorder():
  # The harness runs the recorder itself, so a port is all it needs: the URL
  # the agent dials defaults to the container→host gateway on that port, and
  # the recorder is composed FIRST, before the converter that reads its log.
  harness = ClaudeCodeHarness(capture="proxy", proxy_port=20005)
  assert harness.agent_proxy_url == "http://host.docker.internal:20005"
  assert [type(o).__name__ for o in harness.observers()] == [
      "AgentInfoObserver",
      "ProxyRecorder",
      "ConversationObserver",
      "HarnessOutcomeObserver",
  ]
  # …and STREAM composes none of the proxy machinery (the info observer is
  # unconditional — which build ran is worth recording either way).
  assert [type(o).__name__ for o in ClaudeCodeHarness().observers()] == [
      "AgentInfoObserver",
      "ConversationObserver",
      "HarnessOutcomeObserver",
  ]


def test_proxy_native_outputs_registers_proxy_log():
  assert _proxy_harness().native_outputs() == {
      "proxy_log.jsonl": PROXY_LOG_NAME,
      "stderr.log": "claude.stderr.log",
      "exit_code.txt": "claude.exit_code",
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
  ClaudeCodeHarness().run(sb, prompt="PROMPT", timeout=30.0)
  assert sb.scripts == [AGENT_SCRIPT_NAME]
  # no injected env → the staged env file is left untouched (still empty)
  assert not sb.exists(AGENT_ENV_NAME)


def test_script_sources_the_env_file_after_its_own_defaults():
  script = _script("/app")
  lines = script.splitlines()
  source_line = f'. "$SANDBOX_WORKSPACE"/{AGENT_ENV_NAME}'
  # after the harness's own exports (so a caller can override them) ...
  assert lines.index("export IS_SANDBOX=1") < lines.index(source_line)
  # ... but before the agent invocation that consumes them
  assert lines.index(source_line) < len(lines) - 1


def test_proxy_url_is_not_overridable_by_injected_env():
  # The composition wired this run to a recording proxy; caller env is sourced
  # before that export, so it cannot redirect the agent away from it.
  script = _script("/app", _proxy_harness())
  lines = script.splitlines()
  source_line = f'. "$SANDBOX_WORKSPACE"/{AGENT_ENV_NAME}'
  base_url = next(line for line in lines if "ANTHROPIC_BASE_URL" in line)
  assert lines.index(source_line) < lines.index(base_url)


def test_run_writes_injected_env_as_quoted_exports(tmp_path: Path):
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  ClaudeCodeHarness().run(
      sb,
      prompt="PROMPT",
      timeout=30.0,
      env={"MY_FLAG": "1", "ENDPOINT": "http://host:8080/x y"},
  )
  written = sb.read(AGENT_ENV_NAME).decode()
  assert "export MY_FLAG=1" in written
  # a value with a space is shell-quoted, so sourcing it cannot word-split
  assert "export ENDPOINT='http://host:8080/x y'" in written
  assert sb.scripts == [AGENT_SCRIPT_NAME]  # the script still drives the run


def test_run_rejects_an_invalid_env_name(tmp_path: Path):
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  # a name that is not a shell identifier would corrupt the sourced file, which
  # `set -u` would surface as a silent no-run — fail loudly instead
  with pytest.raises(SandboxError, match="invalid environment variable name"):
    ClaudeCodeHarness().run(
        sb, prompt="PROMPT", timeout=30.0, env={"BAD NAME": "x"}
    )
  assert sb.scripts == []  # nothing ran


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


def test_event_stream_outcome_reads_the_terminal_result():
  assert event_stream_outcome(_stream_text(_EVENTS)) is AgentOutcome.FINISHED

  errored = _stream_text(
      [{"type": "result", "subtype": "error", "is_error": True}]
  )
  # an unrecognized error subtype falls back to the catch-all it is a flavour
  # of, rather than reading as a clean finish
  assert event_stream_outcome(errored) is AgentOutcome.EXECUTION_ERROR

  assert event_stream_outcome("") is AgentOutcome.NO_OUTPUT


@pytest.mark.parametrize(
    ("subtype", "expected"),
    [
        ("error_max_turns", AgentOutcome.MAX_TURNS),
        ("error_max_budget_usd", AgentOutcome.MAX_BUDGET),
        (
            "error_max_structured_output_retries",
            AgentOutcome.MAX_OUTPUT_RETRIES,
        ),
        ("error_during_execution", AgentOutcome.EXECUTION_ERROR),
    ],
)
def test_every_error_subtype_maps_to_its_own_outcome(
    subtype: str, expected: AgentOutcome
):
  # SDKResultErrorSchema enumerates exactly these four; collapsing them would
  # lose the budget-vs-infrastructure distinction the retry policy is built on.
  raw = _stream_text([{"type": "result", "subtype": subtype, "is_error": True}])
  assert event_stream_outcome(raw) is expected


def test_success_carrying_is_error_is_not_a_clean_finish():
  # is_error is independent of the subtype: the loop ended, but its final turn
  # was an API error — ours, and so retryable, unlike a clean finish.
  raw = _stream_text(
      [{"type": "result", "subtype": "success", "is_error": True}]
  )
  assert event_stream_outcome(raw) is AgentOutcome.FINISHED_WITH_API_ERROR
  assert AgentOutcome.FINISHED_WITH_API_ERROR.retryable is True
  assert AgentOutcome.FINISHED.retryable is False


def test_a_trace_without_a_result_event_is_truncated():
  # A hard kill (SIGKILL, OOM) emits no result at all — distinct from "no
  # trace", and distinct from any ending the agent chose.
  raw = _stream_text([{"type": "system", "subtype": "init"}])
  assert event_stream_outcome(raw) is AgentOutcome.TRUNCATED
  assert AgentOutcome.TRUNCATED.retryable is True


def test_budget_endings_are_never_retryable():
  # The fairness invariant: an agent that spent a budget it was given does not
  # get a second one. Named so a future member cannot quietly join this set.
  assert AgentOutcome.MAX_TURNS.retryable is False
  assert AgentOutcome.MAX_BUDGET.retryable is False
  assert AgentOutcome.MAX_OUTPUT_RETRIES.retryable is False


# ─── the agent-info observer ────────────────────────────────────────────────


def _info_run(
    tmp_path: Path, results: list[ExecResult]
) -> tuple[FakeSandbox, Contribution | None]:
  """Drive the observer's hooks the way the manager does."""
  sb = FakeSandbox(
      spec=_SPEC, workspace=epath.Path(tmp_path), run_results=results
  )
  observer = AgentInfoObserver(binary=BINARY_AT, artifact=INFO_ARTIFACT)
  observer.after_create(sb)
  return sb, observer.before_destroy(sb)


def test_agent_info_captures_version_and_help_as_an_artifact(tmp_path: Path):
  sb, contribution = _info_run(
      tmp_path,
      [
          ExecResult(0, "2.1.220 (Claude Code)\n", ""),
          ExecResult(0, "Usage: claude\n", ""),
      ],
  )
  # both flags were asked for, against the agreed path
  assert len(sb.commands) == 2
  assert all(BINARY_AT in c for c in sb.commands)
  assert "--version" in sb.commands[0] and "--help" in sb.commands[1]
  # …and the combined output landed in the workspace, registered under the
  # name a reader of a persisted manifest will see
  assert contribution is not None
  assert contribution.artifacts == {INFO_ARTIFACT: AGENT_INFO_NAME}
  text = (tmp_path / AGENT_INFO_NAME).read_text()
  assert "2.1.220 (Claude Code)" in text
  assert "Usage: claude" in text


def test_agent_info_records_a_binary_that_cannot_run(tmp_path: Path):
  # The case the file exists FOR: the binary is there but unrunnable (wrong
  # libc, bad mode). The failure text is the artifact's whole value, so it must
  # be captured, not swallowed.
  sb, contribution = _info_run(
      tmp_path,
      [ExecResult(126, "", "cannot execute: no such file\n")] * 2,
  )
  assert contribution is not None
  text = (tmp_path / AGENT_INFO_NAME).read_text()
  assert "cannot execute" in text
  assert "[exit 126]" in text
  del sb


def test_agent_info_never_fails_the_run(tmp_path: Path):
  # A diagnostic that can abort the thing it documents is worse than none.
  sb = FakeSandbox(
      spec=_SPEC,
      workspace=epath.Path(tmp_path),
      run_error=SandboxError("exec is broken"),
  )
  observer = AgentInfoObserver(binary=BINARY_AT, artifact=INFO_ARTIFACT)
  observer.after_create(sb)  # must not raise
  contribution = observer.before_destroy(sb)
  # it still recorded that the interrogation itself failed
  assert contribution is not None
  assert "[did not run]" in (tmp_path / AGENT_INFO_NAME).read_text()


def test_agent_info_output_is_declared_but_not_required(tmp_path: Path):
  del tmp_path
  (schema,) = AgentInfoObserver(
      binary=BINARY_AT, artifact=INFO_ARTIFACT
  ).output_schema()
  assert schema.name == INFO_ARTIFACT
  assert schema.required is False  # a run without it is still a valid run


def test_the_effort_is_pinned_and_defaults_to_high():
  # `--help` on 2.1.220 does not state the agent's own default, so leaving it
  # unset makes a sweep depend on whatever the build prefers.
  assert "--effort high" in _script("/app", ClaudeCodeHarness())
  assert "--effort xhigh" in _script("/app", ClaudeCodeHarness(effort="xhigh"))


def test_effort_carries_exactly_the_values_the_pinned_agent_accepts():
  # Read off the binary, not a doc: 2.1.220 answers an unknown value with
  # "Valid values: low, medium, high, xhigh, max." All five probed as accepted
  # and a sixth as rejected.
  #
  # A Literal rather than an enum: the value IS the flag text and carries no
  # behavior. Runtime validation lives where the text actually arrives — the
  # CLI override boundary — see
  # `test_effort_is_overridable_and_a_typo_is_refused`.
  assert get_args(Effort.__value__) == (
      "low",
      "medium",
      "high",
      "xhigh",
      "max",
  )


def test_capture_carries_both_strategies():
  assert get_args(Capture.__value__) == ("stream", "proxy")
