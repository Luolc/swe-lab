"""Tests for the host half of the live correction channel.

The sandbox half is the harness's relay (see ``test_claude_code_harness.py``).
What is interesting here is what the host does *while the agent runs*: it writes
into a directory that is already inside the sandbox because the workspace is
bind-mounted, it ends the run deliberately, and it does not go quietly when the
thing feeding the supervisor dies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path
import shlex
import time
from typing import Any, final

from etils import epath
import pytest

from swe_lab.conversation.model import TextBlock
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    CORRECTION_DONE_NAME,
    CORRECTION_DROP_NAME,
    CORRECTION_FIFO_NAME,
    CORRECTION_PROMPT_NAME,
    CORRECTION_UNCLEAN_NAME,
    EVENT_STREAM_NAME,
)
from swe_lab.harnesses.claude_code.harness import user_event_line
from swe_lab.rollout import (
    CodingAgentTask,
    rollout_outcome,
    RolloutOutcome,
    SUPERVISION_METRIC,
)
from swe_lab.sandbox import RunResult, RunStatus, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.trace_synthesis.channel import (
    CorrectionChannel,
    stream_events,
    SupervisedRun,
    SUPERVISOR_LOG_NAME,
    SupervisorPump,
)
from swe_lab.trace_synthesis.supervisor import (
    Intervention,
    INTERVENTION_TAG,
    LOG_KIND_GAP,
    Observation,
    Supervisor,
)
from swe_lab.workflow import AttemptResult


@final
class _SpeaksOnce:
  """Speaks at the first event it is shown, then never again."""

  def __init__(self) -> None:
    self.calls = 0

  @property
  def name(self) -> str:
    return "speaks-once"

  def consider(self, observation: Observation) -> Intervention | None:
    del observation
    self.calls += 1
    return (
        Intervention(text="look at the failing test")
        if (self.calls == 1)
        else None
    )


@final
class _Raises:
  """A policy that fails, to prove the pump's own failure path."""

  @property
  def name(self) -> str:
    return "raises"

  def consider(self, observation: Observation) -> Intervention | None:
    del observation
    raise RuntimeError("judge unreachable")


def _supervisor(channel: CorrectionChannel, policy: Any) -> Supervisor:
  return Supervisor(
      task="solve it",
      policy=policy,
      sink=channel.sink,
      log=lambda row: None,
  )


def _assistant_event(text: str) -> str:
  return json.dumps(
      {
          "type": "assistant",
          "message": {
              "role": "assistant",
              "content": [
                  {
                      "type": "text",
                      "text": text,
                  }
              ],
          },
      }
  )


def test_a_correction_lands_in_the_sandbox_without_a_transport(tmp_path: Path):
  """The workspace is bind-mounted, so writing the file *is* the delivery.

  No copy step and no second channel: the host writes under the workspace and
  the in-sandbox relay reads the same bytes. A transport here would be a new
  failure surface buying nothing.
  """
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  channel.sink("look at the failing test")

  written = sorted(channel.drop_dir.glob("*.json"))
  assert [p.name for p in written] == ["msg-0001.json"]
  assert json.loads(written[0].read_text()) == {
      "type": "user",
      "message": {
          "role": "user",
          "content": [{"type": "text", "text": "look at the failing test"}],
      },
  }
  # Nothing half-written is left where the relay would match it.
  assert list(channel.drop_dir.glob("*.partial")) == []


def test_corrections_are_ordered_by_the_name_the_relay_reads(tmp_path: Path):
  # The relay appends `*.json` in name order, so the names have to sort the
  # way the corrections were said — not the way the filesystem lists them.
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  for i in range(11):
    channel.sink(f"correction {i}")
  names = sorted(p.name for p in channel.drop_dir.glob("*.json"))
  assert names[0] == "msg-0001.json"
  assert names[-1] == "msg-0011.json"  # zero-padded, so 11 sorts after 2


def test_ending_the_run_is_an_act(tmp_path: Path):
  """Closing the FIFO is what makes the CLI exit, so it starts here.

  The sentinel is the only thing the relay waits for. Nothing else in this
  module writes it, so a run cannot end because something failed — only
  because someone decided it was over.
  """
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  assert not (channel.drop_dir / CORRECTION_DONE_NAME).exists()
  channel.close()
  assert (channel.drop_dir / CORRECTION_DONE_NAME).exists()


def test_an_unclean_close_is_readable_afterwards(tmp_path: Path):
  """The marker is how "our side fell over" stops looking like "it stopped".

  Written by the relay before it exists and removed only on the deliberate
  close, so its presence after the run is the positive identification that
  keeps a supervisor crash from being charged to the actor (ADR-0016).
  """
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  assert channel.closed_uncleanly is False
  (epath.Path(tmp_path) / CORRECTION_UNCLEAN_NAME).touch()
  assert channel.closed_uncleanly is True


def test_the_pump_feeds_events_and_records_what_was_said(tmp_path: Path):
  # The whole point, end to end on the host side: events written by the agent
  # produce a correction sitting in the drop directory.
  events = epath.Path(tmp_path) / "events.jsonl"
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  pump = SupervisorPump(
      supervisor=_supervisor(channel, _SpeaksOnce()),
      channel=channel,
      events_path=events,
  )
  events.write_text(_assistant_event("editing the wrong file") + "\n")
  assert pump.poll() == 1
  assert [i.text for i in pump.interventions] == ["look at the failing test"]
  assert len(list(channel.drop_dir.glob("*.json"))) == 1
  # …and the supervisor delivered it: the pump does not send a second copy.
  assert channel.delivered == 1


def test_a_half_written_line_is_re_read_rather_than_guessed_at(tmp_path: Path):
  """The file is appended to while it is read, so the tail is often partial.

  Consuming a fragment would hand the supervisor a truncated event; dropping it
  would lose the event entirely once it completes. It is left for the next
  poll instead.
  """
  events = epath.Path(tmp_path) / "events.jsonl"
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  policy = _SpeaksOnce()
  pump = SupervisorPump(
      supervisor=_supervisor(channel, policy),
      channel=channel,
      events_path=events,
  )
  whole = _assistant_event("editing the wrong file")
  events.write_text(whole[: len(whole) // 2])  # no newline yet
  assert pump.poll() == 0
  assert policy.calls == 0  # the supervisor was shown nothing at all

  events.write_text(whole + "\n")  # the same line, now complete
  assert pump.poll() == 1
  assert policy.calls == 1  # and exactly once


def test_a_pump_that_dies_says_so_instead_of_going_quiet(tmp_path: Path):
  """A pump that stops polling must not read as a fully supervised run.

  A supervisor that dies part-way leaves every later boundary unjudged while
  the run finishes and looks complete. An unhealthy pump is therefore not a
  detail of the report: it decides whether the run is evidence about
  supervision at all.
  """
  events = epath.Path(tmp_path) / "events.jsonl"
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  pump = SupervisorPump(
      supervisor=_supervisor(channel, _SpeaksOnce()),
      channel=channel,
      events_path=events,
  )
  assert pump.healthy is True
  # The supervisor absorbs a raising *policy* by design, so break the pump
  # itself: an events path that cannot be read as text.
  events.mkdir(parents=True, exist_ok=True)
  assert pump.poll() == 0
  assert pump.healthy is False
  assert pump.failure is not None


def test_a_raising_policy_never_reaches_the_agent(tmp_path: Path):
  # The supervisor owns this: a policy that raises is a logged gap, not a
  # correction and not a crash. Asserted here because the pump must not
  # "helpfully" turn it into either.
  events = epath.Path(tmp_path) / "events.jsonl"
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  pump = SupervisorPump(
      supervisor=_supervisor(channel, _Raises()),
      channel=channel,
      events_path=events,
  )
  events.write_text(_assistant_event("anything") + "\n")
  assert pump.poll() == 0
  assert pump.healthy is True  # the pump is fine; the policy was not
  assert list(channel.drop_dir.glob("*.json")) == []


def test_only_whole_usable_events_reach_the_supervisor():
  # A fragment and a non-object line are both skipped rather than guessed at.
  events = list(stream_events('{"type":"a"}\nnot json\n[1,2]\n{"type":"b"}'))
  assert [e["type"] for e in events] == ["a", "b"]


_SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "base")


def _fs(tmp_path: Path) -> FakeSandbox:
  """Build a sandbox the observer never reads: both facts are host-side."""
  return FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))


def _attempt_with(metrics: Mapping[str, float]) -> AttemptResult:
  """Build a finished attempt carrying just the metrics under test."""
  return AttemptResult(
      run=RunResult(
          label="acme__widget-1",
          status=RunStatus.SUCCESS,
          artifacts={},
          metrics=dict(metrics),
      ),
      exec_result=None,
      output_schema=(),
      observers=(),
  )


def _supervised(tmp_path: Path, policy: Any = None) -> SupervisedRun:
  """Start a supervised run over ``tmp_path``, as the engine would.

  Args:
    tmp_path: The workspace, standing in for the bind mount.
    policy: The policy to drive it with; one that speaks once by default.

  Returns:
    The started observer. Every caller must reach ``before_destroy``, which is
    what stops its thread.
  """
  run = SupervisedRun(
      policy_factory=lambda: policy if policy is not None else _SpeaksOnce(),
      task="solve it",
      poll_interval=0.01,
  )
  run.after_create(_fs(tmp_path))
  return run


def _result_event() -> str:
  return json.dumps({"type": "result", "subtype": "success"})


def _wait_until(condition: Callable[[], bool]) -> None:
  """Block until ``condition`` holds, or fail the test.

  The observer polls on its own thread, so a test asserting on what that
  thread did has to wait for it. Bounded, and failing the wait *is* the
  finding: the thing under test never happened.

  Args:
    condition: What the thread is expected to bring about.

  Raises:
    AssertionError: The condition never held within the bound.
  """
  deadline = time.monotonic() + 10.0
  while time.monotonic() < deadline:
    if condition():
      return
    time.sleep(0.01)
  raise AssertionError("the supervising thread never got there")


def test_a_supervised_run_that_stayed_supervised_reports_no_metric(
    tmp_path: Path,
):
  # The metric is an event: a healthy run leaves no key rather than a zero, so
  # a reader cannot mistake "supervised throughout" for "never measured". The
  # account is contributed either way — it is the evidence the run was watched.
  run = _supervised(tmp_path)
  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {}
  assert SUPERVISOR_LOG_NAME in contribution.inline_artifacts


def test_a_dead_pump_produces_the_metric_that_changes_the_outcome_word(
    tmp_path: Path,
):
  """The producer for a signal that already had a consumer.

  `rollout_outcome` reads `supervision.unhealthy` and classifies the run as
  ours. A metric with a consumer and no producer is the same defect as one with
  a producer and no consumer, so the two halves are asserted together: the
  observer emits it, and the classifier acts on it.
  """
  run = _supervised(tmp_path, policy=_Raises())
  # A raising policy is caught by the supervisor, so the pump is failed the way
  # a run fails it: from outside, by the thing it reads.
  assert run.pump is not None
  run.pump.failure = RuntimeError("the judge went away")

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}

  # …and the metric is not merely recorded: it decides the word.
  task = CodingAgentTask(harness=ClaudeCodeHarness())
  attempt = _attempt_with(contribution.metrics)
  assert rollout_outcome(attempt) is RolloutOutcome.SUPERVISION_FAILED
  assert task.outputs_valid(attempt) is False


def test_a_channel_that_closed_on_its_own_produces_it_too(tmp_path: Path):
  # The other way the same fact is reached: the relay's marker survived, so the
  # write end closed without anyone deciding the run was over.
  run = _supervised(tmp_path)
  (epath.Path(tmp_path) / CORRECTION_UNCLEAN_NAME).touch()
  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}


def test_the_supervisors_account_of_the_run_is_persisted(tmp_path: Path):
  """Point 1 of task 01: the run leaves the supervisor's own artifact.

  Attachment is not observable from the actor's side — a supervisor that read
  nothing and one that read everything produce the same rollout. This artifact
  is the difference, so it is declared as an output rather than written as a
  side effect.
  """
  run = _supervised(tmp_path)
  assert [s.name for s in run.output_schema()] == [SUPERVISOR_LOG_NAME]
  events = epath.Path(tmp_path) / EVENT_STREAM_NAME
  _ = events.write_text(_assistant_event("editing the parser") + "\n")

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  rows = [
      json.loads(line)
      for line in contribution.inline_artifacts[SUPERVISOR_LOG_NAME]
      .decode()
      .splitlines()
  ]
  # One row per event consumed, and the row that spoke names the policy that
  # produced the utterance — the field acceptance point 3 reads.
  assert [row["kind"] for row in rows] == ["spoke"]
  assert rows[0]["policy"] == "speaks-once"
  assert rows[0]["text"] == "look at the failing test"


def test_the_run_ends_when_the_supervisor_lets_a_turn_boundary_pass(
    tmp_path: Path,
):
  """Under a live channel somebody has to decide the run is over.

  The actor does not exit when it finishes answering — it waits for more input
  — so a run with nobody to close the channel burns its whole wall clock and
  reaches the outside as the actor's timeout. The moment chosen is the first
  turn boundary the supervisor passes in silence.
  """
  events = epath.Path(tmp_path) / EVENT_STREAM_NAME
  run = _supervised(tmp_path)
  sentinel = epath.Path(tmp_path) / CORRECTION_DROP_NAME / CORRECTION_DONE_NAME

  # The first result is answered rather than let pass: the policy speaks, so
  # the run continues.
  _ = events.write_text(_result_event() + "\n")
  _wait_until(lambda: bool(run.pump and run.pump.interventions))
  assert not sentinel.exists()

  # The second is let pass, and that is the end of the run.
  with events.open("a") as handle:
    _ = handle.write(_result_event() + "\n")
  _wait_until(sentinel.exists)

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {}  # a deliberate close is not a failure


def test_a_pump_that_died_still_ends_the_run(tmp_path: Path):
  """Neither of the two obvious answers, for the reason task 16 §8.1 gives.

  Leaving the channel open charges our breakage to the actor as a spent wall
  clock; closing it silently renders a system failure as an ordinary early
  finish. So it closes **and** reports, and the metric is what keeps the
  ending attributable.
  """
  run = _supervised(tmp_path, policy=_Raises())
  assert run.pump is not None
  run.pump.failure = RuntimeError("the judge went away")
  sentinel = epath.Path(tmp_path) / CORRECTION_DROP_NAME / CORRECTION_DONE_NAME
  _wait_until(sentinel.exists)

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}


@pytest.mark.docker
def test_the_channel_works_in_a_real_container(tmp_path: Path):
  """The parts no shape test can reach: FIFO, blocking open, reaping.

  Everything else in this file runs on the host. Three properties exist only
  inside the container, where a real FIFO and real processes do:

  - the relay's ``exec 3>`` blocks until the reader opens the FIFO, so the
    order the script starts things in is load-bearing;
  - the sentinel closes the write end, the reader sees EOF, and **only** that
    path clears the unclean marker;
  - one ``EXIT`` trap reaps **both** background processes.

  A stand-in stands in for the capture proxy: the pinned proxy binary is not in
  a plain image, and what is under test is the reaping, not the proxy.
  """
  from swe_lab.harnesses.claude_code.harness import (
      _reap,
      _reaper_lines,
      _relay_start_lines,
      user_event_line,
  )
  from swe_lab.sandbox.backends.host import DockerHostSandbox

  workspace = tmp_path / "ws"
  workspace.mkdir()
  sandbox = DockerHostSandbox(
      spec=SandboxSpec("channel-probe", "debian:stable-slim", "/", "none"),
      workspace=epath.Path(workspace),
  )
  sandbox.up()
  try:
    prompt = user_event_line("solve it").strip()
    correction = user_event_line("look at the failing test").strip()
    script = "\n".join(
        [
            "set -u",
            *_reaper_lines(),
            # Stands in for the capture proxy: a second long-lived
            # child, which is what made the two traps overwrite.
            "sleep 300 >/dev/null 2>&1 &",
            "proxy_pid=$!",
            _reap("proxy_pid"),
            f"printf '%s\\n' {shlex.quote(prompt)}"
            f' > "$SANDBOX_WORKSPACE"/{CORRECTION_PROMPT_NAME}',
            *_relay_start_lines(),
            # The host writes into the bind-mounted drop directory
            # while the agent runs; the same files are the same bytes.
            "(",
            "  sleep 1",
            f"  printf '%s\\n' {shlex.quote(correction)}"
            f' > "$SANDBOX_WORKSPACE"/{CORRECTION_DROP_NAME}/m.part',
            f'  mv "$SANDBOX_WORKSPACE"/{CORRECTION_DROP_NAME}/m.part'
            f' "$SANDBOX_WORKSPACE"/{CORRECTION_DROP_NAME}/msg-0001.json',
            "  sleep 1",
            f'  touch "$SANDBOX_WORKSPACE"/{CORRECTION_DROP_NAME}/'
            f"{CORRECTION_DONE_NAME}",
            ") &",
            # The agent's side: read the channel until the deliberate close.
            f'cat "$SANDBOX_WORKSPACE"/{CORRECTION_FIFO_NAME}'
            ' > "$SANDBOX_WORKSPACE"/delivered.jsonl',
            'printf "%s %s" "$proxy_pid" "$relay_pid"'
            ' > "$SANDBOX_WORKSPACE"/pids',
        ]
    )
    _ = (workspace / "channel.sh").write_text(script)
    result = sandbox.run_script("channel.sh", timeout=60.0)
    assert result.ok, result.stderr

    # The prompt is the first message on the channel, the correction follows.
    delivered = (workspace / "delivered.jsonl").read_text().splitlines()
    assert [
        json.loads(line)["message"]["content"][0]["text"] for line in delivered
    ] == ["solve it", "look at the failing test"]

    # The close was deliberate, so the marker is gone. Its survival is what
    # tells a later reader that our side fell over instead.
    assert not (workspace / CORRECTION_UNCLEAN_NAME).exists()

    # Both children were reaped by the single EXIT trap. Checked inside the
    # container, because these are container pids.
    pids = (workspace / "pids").read_text().split()
    _ = (workspace / "alive.sh").write_text(
        "\n".join(f"kill -0 {pid} 2>/dev/null && exit 1" for pid in pids)
        + "\nexit 0\n"
    )
    assert sandbox.run_script(
        "alive.sh", timeout=30.0
    ).ok, "a background process outlived the script"
  finally:
    # The relay ran as root, so the drop directory and FIFO it left are not
    # removable by the test user; clear them from inside before teardown.
    _ = (workspace / "clean.sh").write_text(
        'rm -rf "$SANDBOX_WORKSPACE"/corrections'
        ' "$SANDBOX_WORKSPACE"/claude.stdin.fifo\n'
    )
    _ = sandbox.run_script("clean.sh", timeout=30.0)
    sandbox.down()


def test_an_interjection_survives_conversion_into_the_trace():
  """Point 6 of task 01: delivered is not the same as recorded.

  A correction that reaches the actor and is then dropped by conversion passes
  every earlier check — the channel worked, the actor answered — and leaves no
  evidence that anything was said. So the conversion is asserted over the very
  bytes the channel writes, rather than over a hand-written approximation.
  """
  from swe_lab.harnesses.claude_code.convert import proxy_log_to_conversation

  spoken = Intervention(text="the failing test names the parser")
  # What the relay appends to the actor's stdin, unpacked back to the text the
  # wire carries. Going through `user_event_line` is the point: a change to
  # either end of that shape breaks this test rather than passing it.
  wire_text = json.loads(user_event_line(spoken.rendered()))["message"][
      "content"
  ][0]["text"]
  record = {
      "request": {
          "body": {
              "model": "claude-sonnet-5",
              "messages": [
                  {
                      "role": "user",
                      "content": [{"type": "text", "text": wire_text}],
                  }
              ],
          }
      },
      "response": {
          "status": 200,
          "message": {
              "role": "assistant",
              "content": [{"type": "text", "text": "looking at it now"}],
          },
      },
      "complete": True,
  }

  conversation = proxy_log_to_conversation(json.dumps(record) + "\n")
  rendered = "\n".join(
      block.text
      for message in conversation.messages
      for block in message.content
      if isinstance(block, TextBlock)
  )
  assert f"<{INTERVENTION_TAG}>" in rendered
  assert spoken.text in rendered


def test_a_boundary_the_policy_could_not_judge_invalidates_the_run(
    tmp_path: Path,
):
  """A gap is not a silence, and the run must not be counted as if it were.

  `observe` returns ``None`` for both a policy that raised and a policy that
  chose not to speak, so keying the end of the run on that alone classifies our
  failure as the supervisor's decision — a run that was never judged at that
  boundary closes cleanly, leaves no metric, and stays in the denominator of
  every rate computed over it. Driven end to end here: a real ``result`` event
  through a raising policy, then the terminal contribution, then the word.
  """
  events = epath.Path(tmp_path) / EVENT_STREAM_NAME
  run = _supervised(tmp_path, policy=_Raises())
  _ = events.write_text(_result_event() + "\n")
  # The gap ends the run, so the sentinel is the public signal that the
  # supervising thread has finished with this event.
  _wait_until(
      (
          epath.Path(tmp_path) / CORRECTION_DROP_NAME / CORRECTION_DONE_NAME
      ).exists
  )

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  # The supervisor recorded the boundary as one it could not cover…
  rows = [
      json.loads(line)
      for line in contribution.inline_artifacts[SUPERVISOR_LOG_NAME]
      .decode()
      .splitlines()
  ]
  assert [row["kind"] for row in rows] == [LOG_KIND_GAP]
  # …and that reaches the outcome word rather than stopping at the log.
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}
  attempt = _attempt_with(contribution.metrics)
  assert rollout_outcome(attempt) is RolloutOutcome.SUPERVISION_FAILED
  assert (
      CodingAgentTask(harness=ClaudeCodeHarness()).outputs_valid(attempt)
      is False
  )


def test_a_gap_mid_turn_ends_the_run_rather_than_letting_it_continue(
    tmp_path: Path,
):
  """Everything after a gap is unsupervised, so there is nothing to buy.

  The run is already disqualified at that point; letting the actor keep going
  spends the rest of the budget producing a rollout that cannot be used as
  evidence about supervision either way.
  """
  events = epath.Path(tmp_path) / EVENT_STREAM_NAME
  run = _supervised(tmp_path, policy=_Raises())
  # Not a `result`: nothing here is a turn boundary, so only the gap can end
  # the run.
  _ = events.write_text(_assistant_event("still working") + "\n")
  sentinel = epath.Path(tmp_path) / CORRECTION_DROP_NAME / CORRECTION_DONE_NAME
  _wait_until(sentinel.exists)

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}


def test_a_correction_that_could_not_be_delivered_invalidates_the_run(
    tmp_path: Path,
):
  """The other half of the same gap: the policy spoke and nobody heard it.

  The supervisor mutes itself and logs a gap when the sink raises, which from
  the actor's side is identical to a run nobody tried to correct. Reproduced by
  occupying the name the first correction is renamed onto — a real failure of
  the delivery step, not a patched sink — and deliberately one that leaves the
  drop directory usable, so the deliberate close still succeeds and the pump
  stays healthy. What is under test is the **gap**, and it is the only thing
  that can raise the metric here.
  """
  occupied = epath.Path(tmp_path) / CORRECTION_DROP_NAME / "msg-0001.json"
  occupied.mkdir(parents=True)
  _ = (occupied / "in the way").write_text("")
  events = epath.Path(tmp_path) / EVENT_STREAM_NAME
  run = _supervised(tmp_path)

  _ = events.write_text(_result_event() + "\n")
  # The gap ends the run, so the sentinel is the public signal that the
  # supervising thread has finished with this event.
  _wait_until(
      (
          epath.Path(tmp_path) / CORRECTION_DROP_NAME / CORRECTION_DONE_NAME
      ).exists
  )

  contribution = run.before_destroy(_fs(tmp_path))
  assert contribution is not None
  rows = [
      json.loads(line)
      for line in contribution.inline_artifacts[SUPERVISOR_LOG_NAME]
      .decode()
      .splitlines()
  ]
  assert rows[0]["kind"] == LOG_KIND_GAP
  assert "sink raised" in rows[0]["reason"]
  # The pump never failed and the channel closed deliberately: with the gap
  # ignored, this run would report as a clean, fully supervised one.
  assert run.pump is not None and run.pump.healthy
  assert run.channel is not None and not run.channel.closed_uncleanly
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}
  assert (
      rollout_outcome(_attempt_with(contribution.metrics))
      is RolloutOutcome.SUPERVISION_FAILED
  )
