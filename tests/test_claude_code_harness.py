"""Tests for the claude_code harness: mounts, binary, invocation, conversion."""

import json
import os
from pathlib import Path
import subprocess
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
    event_stream_usage,
)
from swe_lab.harnesses.claude_code.constants import (
    AGENT_ENV_NAME,
    AGENT_INFO_NAME,
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    CORRECTION_DONE_NAME,
    CORRECTION_DROP_NAME,
    CORRECTION_FIFO_NAME,
    CORRECTION_PROMPT_NAME,
    CORRECTION_UNCLEAN_NAME,
    INFO_ARTIFACT,
    PROMPT_FILENAME,
    PROXY_BASE_URL,
    PROXY_BINARY_AT,
    PROXY_LOG_NAME,
    PROXY_PORT,
    PROXY_STDERR_NAME,
)
from swe_lab.harnesses.claude_code.proxy import PROXY_SOURCE_ENV
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


def test_proxy_capture_refuses_to_run_the_agent_without_a_live_proxy():
  # The base-URL guard this replaces was inert — the script exports the URL
  # itself, so it could never be empty. What can actually go wrong now is the
  # proxy: it exits (a bad target, no CA bundle in the image) or never starts
  # listening. Either way the run must stop, loudly, rather than let the agent
  # burn a budget against a refused connection.
  script = _script("/app", _proxy_harness())
  assert 'if ! kill -0 "$proxy_pid" 2>/dev/null; then' in script
  assert f"the capture proxy never listened on port {PROXY_PORT}" in script
  assert script.count("exit 78") == 3  # no key, proxy died, proxy never came up


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
  return ClaudeCodeHarness(capture="proxy")


def test_proxy_invocation_routes_through_loopback_and_discards_stdout():
  script = _script("/app", _proxy_harness())
  assert f"export ANTHROPIC_BASE_URL={PROXY_BASE_URL}" in script
  assert "--output-format json" in script  # not stream-json in proxy mode
  assert "> /dev/null" in script  # agent stdout is not the trace
  assert "claude.event_stream.jsonl" not in script  # no stream redirect
  assert '< "$SANDBOX_WORKSPACE"/prompt.txt' in script  # prompt still on stdin


def test_the_script_starts_the_proxy_in_the_sandbox_and_reaps_it():
  # The whole lifecycle lives in the script: start in the background, poll
  # until the port answers (never a fixed sleep), and kill it on every exit
  # path. Nothing is bound on the host and no port is allocated anywhere.
  script = _script("/app", _proxy_harness())
  assert f"{PROXY_BINARY_AT} --port {PROXY_PORT}" in script
  assert f'--output "$SANDBOX_WORKSPACE"/{PROXY_LOG_NAME}' in script
  assert f'> "$SANDBOX_WORKSPACE"/{PROXY_STDERR_NAME} 2>&1 &' in script
  assert f"until (exec 3<>/dev/tcp/127.0.0.1/{PROXY_PORT})" in script
  # Registered with the script's single EXIT trap rather than owning one:
  # Bash keeps only the last trap installed, and this script may start a relay
  # too (see test_the_script_installs_exactly_one_exit_trap).
  assert 'reaped_pids="$proxy_pid $reaped_pids"' in script
  # The proxy is started before the agent is invoked, not after.
  assert script.index("proxy_pid=$!") < script.index(BINARY_AT + " -p")
  # A stream run starts nothing and mentions no proxy at all.
  assert "proxy_pid" not in _script("/app")


def test_the_proxy_target_is_the_run_s_upstream():
  # Not cosmetic: the proxy injects OpenRouter provider preferences and mirrors
  # Anthropic-Beta into X-Anthropic-Beta only when the target is OpenRouter.
  assert "--target https://api.anthropic.com" in _script(
      "/app", _proxy_harness()
  )
  openrouter = ClaudeCodeHarness(
      capture="proxy", proxy_target="https://openrouter.ai/api"
  )
  assert "--target https://openrouter.ai/api" in _script("/app", openrouter)


def test_proxy_capture_composes_no_extra_observer():
  # The recorder observer existed only to order a host process against the
  # sandbox lifecycle. With the proxy inside the sandbox there is nothing to
  # order: the script has already reaped it when `run` returns.
  assert [type(o).__name__ for o in _proxy_harness().observers()] == [
      type(o).__name__ for o in ClaudeCodeHarness().observers()
  ]
  assert [type(o).__name__ for o in ClaudeCodeHarness().observers()] == [
      "AgentInfoObserver",
      "ConversationObserver",
      "HarnessOutcomeObserver",
      # Last, and on every capture: the one record here the agent writes
      # itself, and the one that dies with the container's writable layer.
      "NativeTranscriptObserver",
  ]


def test_proxy_capture_declares_the_proxy_binary_as_an_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  # The proxy travels the same seam as the agent binary — declared here,
  # placed by whichever backend is running — and only when it is used.
  #
  # A synthetic source, because declaring the asset reads the real one to
  # version it: cc-reverse-proxy is a sibling checkout this repo does not
  # vendor, and CI has no such sibling. (That the *absence* is reported with a
  # usable message is `test_proxy.py`'s job, not this one's.)
  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text("package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))

  paths = [a.path for a in _proxy_harness().assets()]
  assert paths == [BINARY_AT, PROXY_BINARY_AT]
  # …and a stream run declares no proxy at all, so it is never transferred.
  assert [a.path for a in ClaudeCodeHarness().assets()] == [BINARY_AT]


def test_proxy_native_outputs_registers_the_log_and_the_proxy_s_own_output():
  assert _proxy_harness().native_outputs() == {
      "proxy_log.jsonl": PROXY_LOG_NAME,
      "proxy_stderr.log": PROXY_STDERR_NAME,
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


def test_event_stream_usage_aggregates_each_figure_its_own_way():
  # cost is cumulative over the session and turns are per result, so a
  # segmented run's total is the last cost and the sum of the turns. Summing
  # the cost would count the earlier segments twice.
  segmented = _stream_text(
      [
          {
              "type": "result",
              "subtype": "success",
              "total_cost_usd": 0.03,
              "num_turns": 3,
          },
          {
              "type": "result",
              "subtype": "success",
              "total_cost_usd": 0.05,
              "num_turns": 1,
          },
      ]
  )

  assert event_stream_usage(segmented) == {"cost_usd": 0.05, "num_turns": 4}


def test_event_stream_usage_reports_a_partial_aggregate_as_absent():
  # A metric whose inputs are not all present is absent, not partial. Both of
  # these would otherwise enter a cost average as though measured: the first as
  # a stale earlier segment, the second as a sum over some of the segments.
  final_without_cost = _stream_text(
      [
          {"type": "result", "total_cost_usd": 0.03, "num_turns": 3},
          {"type": "result", "num_turns": 1},
      ]
  )
  assert event_stream_usage(final_without_cost) == {
      "cost_usd": None,
      "num_turns": 4,
  }

  segment_without_turns = _stream_text(
      [
          {"type": "result", "total_cost_usd": 0.03, "num_turns": 3},
          {"type": "result", "total_cost_usd": 0.05},
      ]
  )
  assert event_stream_usage(segment_without_turns) == {
      "cost_usd": 0.05,
      "num_turns": None,
  }


def test_event_stream_usage_reports_absence_rather_than_zero():
  # a run whose trace never landed did not cost nothing; it is unknown, and a
  # zero here would average into a cost estimate as if it were measured
  assert event_stream_usage("") == {"cost_usd": None, "num_turns": None}

  without = _stream_text([{"type": "result", "subtype": "success"}])
  assert event_stream_usage(without) == {"cost_usd": None, "num_turns": None}


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


# ─── the live correction channel (ADR-0013, task 16) ─────────────────────────


def test_the_correction_channel_is_off_unless_it_is_asked_for():
  """The default path keeps the termination mechanism it has.

  With the channel off, stdin is the prompt file and the run ends because that
  file reaches EOF. Turning the channel on deletes that mechanism, so it cannot
  be something a caller gets without choosing it.
  """
  script = ClaudeCodeHarness()._invocation_script("/app")
  assert "--input-format stream-json" not in script
  assert CORRECTION_FIFO_NAME not in script
  assert f'< "$SANDBOX_WORKSPACE"/{PROMPT_FILENAME}' in script


def test_the_relay_opens_the_channel_before_the_agent_reads_it():
  """Opening order is load-bearing, not incidental.

  A shell redirect from a FIFO blocks until a writer opens the other end, so an
  agent started before its relay waits forever — and that wait is indis-
  tinguishable from a slow run until the wall clock ends it.
  """
  script = ClaudeCodeHarness(
      capture="proxy", correction_channel=True
  )._invocation_script("/app")
  lines = script.splitlines()
  relay = next(i for i, line in enumerate(lines) if line.startswith("mkfifo "))
  agent = next(i for i, line in enumerate(lines) if BINARY_AT in line)
  assert relay < agent
  # …and the agent really is reading that FIFO rather than a file.
  assert f'< "$SANDBOX_WORKSPACE"/{CORRECTION_FIFO_NAME}' in lines[agent]
  assert "--input-format stream-json" in lines[agent]


def test_the_prompt_becomes_the_first_message_on_the_channel(tmp_path: Path):
  """Under `--input-format stream-json` a plain prompt file is not readable.

  Every message on this channel is a JSON line, so the task prompt is simply
  the first of them; the human-readable prompt file is still written, on both
  paths, as the record of what was asked.
  """
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  _ = ClaudeCodeHarness(capture="proxy", correction_channel=True).run(
      sb, prompt="solve it", timeout=1.0
  )
  assert json.loads(sb.read(CORRECTION_PROMPT_NAME).decode()) == {
      "type": "user",
      "message": {
          "role": "user",
          "content": [{"type": "text", "text": "solve it"}],
      },
  }
  assert sb.read(PROMPT_FILENAME) == b"solve it"


def test_the_channel_closes_only_on_the_sentinel_and_says_so_when_it_does_not():
  """Termination must be deliberate, and an accident must be visible.

  The CLI exits when stdin reaches EOF, so closing the FIFO's write end *is*
  how a supervised run ends — it has to be produced by whoever decides the task
  is over, never as a side effect of something dying. The marker is the other
  half: written before the relay exists and removed only on that deliberate
  close, so anything else that ends the relay leaves it behind. Failure-closed
  on purpose — a relay that is killed cannot write a marker, but it also cannot
  remove one, and without this a supervisor crash reaches the outside as an
  agent that merely stopped early.
  """
  script = ClaudeCodeHarness(
      capture="proxy", correction_channel=True
  )._invocation_script("/app")
  assert f'touch "$SANDBOX_WORKSPACE"/{CORRECTION_UNCLEAN_NAME}' in script
  # Exactly one path clears the marker, and it runs *before* the close.
  # The ordering is load-bearing: closing makes the reader see EOF, the script
  # then exits, and its EXIT trap kills the relay — so a removal placed after
  # the close races that kill and leaves the marker behind on an ordinary
  # ending. The marker means "the relay never saw a deliberate end", so the
  # sentinel is the moment it stops being true.
  clears = [
      line
      for line in script.splitlines()
      if CORRECTION_UNCLEAN_NAME in line and line.strip().startswith("rm -f")
  ]
  assert len(clears) == 1
  lines = script.splitlines()
  close = next(i for i, line in enumerate(lines) if line.strip() == "exec 3>&-")
  assert lines.index(clears[0]) < close
  # …and the loop it leaves is the one the sentinel ends.
  assert f"{CORRECTION_DROP_NAME}/{CORRECTION_DONE_NAME}" in script


def test_the_script_installs_exactly_one_exit_trap():
  """Bash keeps only the last ``EXIT`` trap, so there must be exactly one.

  The proxy-plus-channel configuration is the *intended* live-channel setup, and
  it starts two background processes. Two helpers each trapping their own would
  leave the earlier one unreaped — a leak that no exit status reports.
  """
  script = ClaudeCodeHarness(
      capture="proxy", correction_channel=True
  )._invocation_script("/app")
  assert script.count("trap ") == 1
  # …and both processes are registered with it.
  assert 'reaped_pids="$proxy_pid $reaped_pids"' in script
  assert 'reaped_pids="$relay_pid $reaped_pids"' in script


def test_the_cleanup_actually_reaps_both_background_processes(tmp_path: Path):
  """Run the generated cleanup, rather than asserting its shape.

  The shape assertions above would still pass if the trap body reaped only the
  first entry, or if a later change registered a pid the loop never reads. This
  runs the real lines from the harness against two live processes and requires
  **both** to be gone afterwards — so removing either reaper, or letting two
  traps overwrite each other, turns it red.

  One child is **stopped**, so TERM is never delivered to it and only the KILL
  escalation removes it. Without that escalation the script returns after its
  grace period with the child still alive, and this test goes red. It costs
  that grace period once, which is what the guarantee is worth.

  The children sleep far longer than the timeout, so they cannot die of old age
  and pass this by accident; and the pids travel through a file rather than a
  pipe, so nothing here waits on a descriptor a child might hold open.
  """
  from swe_lab.harnesses.claude_code.harness import _reap, _reaper_lines

  pid_file = tmp_path / "pids"
  script = "\n".join(
      [
          *_reaper_lines(),
          "sleep 300 >/dev/null 2>&1 &",
          "proxy_pid=$!",
          _reap("proxy_pid"),
          "sleep 300 >/dev/null 2>&1 &",
          "relay_pid=$!",
          _reap("relay_pid"),
          # A **stopped** child, which is the reliable way to model one that
          # does not die on TERM: the signal stays pending and is never
          # delivered, so only the KILL escalation removes it. `bash -c 'trap
          # "" TERM; sleep 300'` does not model it — the shell execs the
          # trailing command and the trap goes with it.
          "sleep 300 >/dev/null 2>&1 &",
          "stubborn_pid=$!",
          'kill -STOP "$stubborn_pid"',
          _reap("stubborn_pid"),
          f'printf "%s %s %s" "$proxy_pid" "$relay_pid" "$stubborn_pid"'
          f" > {pid_file}",
      ]
  )
  # Staged as a file and run like the harness runs its own script, rather than
  # through `bash -c`.
  script_file = tmp_path / "cleanup.sh"
  _ = script_file.write_text(script + "\n")
  # Short, because a working trap returns in milliseconds. A trap that never
  # fires must fail this test quickly rather than hold the suite for the
  # children's lifetime.
  timed_out = False
  try:
    subprocess.run(
        ["bash", str(script_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )
  except subprocess.TimeoutExpired:
    timed_out = True

  survivors: list[str] = []
  for name, pid in zip(
      ("proxy", "relay", "stopped"),
      (int(p) for p in pid_file.read_text().split()),
      strict=True,
  ):
    try:
      os.kill(pid, 0)
    except ProcessLookupError:
      continue
    survivors.append(name)
    os.kill(pid, 9)  # never leave one behind: an orphan destabilises later runs

  assert not timed_out, "the cleanup trap never returned"
  assert not survivors, f"outlived the script: {', '.join(survivors)}"


def test_the_channel_is_refused_without_the_capture_it_requires():
  """A supervised run on stream capture would produce a false trace.

  Stream capture drops the injected message unless replay is asked for, and
  with replay it renders as a ``user`` message what the wire carries as
  ``system`` — so a stream-derived trace of a supervised run asserts a turn the
  model never saw. Refused at construction rather than documented, because
  otherwise the *simplest* caller reaches it through the public field and gets
  something that looks like an ordinary result.
  """
  with pytest.raises(ValueError, match="requires capture='proxy'"):
    _ = ClaudeCodeHarness(correction_channel=True)
  # …and the combination the channel is designed for is accepted.
  assert ClaudeCodeHarness(
      capture="proxy", correction_channel=True
  ).correction_channel
