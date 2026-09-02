"""Tests for the host half of the live correction channel.

The sandbox half is the harness's relay (see ``test_claude_code_harness.py``).
What is interesting here is what the host does *while the agent runs*: it writes
into a directory that is already inside the sandbox because the workspace is
bind-mounted, it ends the run deliberately, and it does not go quietly when the
thing feeding the supervisor dies.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any, final

from etils import epath

from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    CORRECTION_DONE_NAME,
    CORRECTION_UNCLEAN_NAME,
)
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
    CorrectionChannelObserver,
    stream_events,
    SupervisorPump,
)
from swe_lab.trace_synthesis.supervisor import (
    Intervention,
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
  """A measured failure, not an imagined one.

  In the steered re-run a polling thread died on a malformed reply at boundary
  13 and every later boundary went unjudged — the run looked complete and was
  unsupervised from there on. An unhealthy pump is not a detail of the report:
  it decides whether the run is evidence about supervision at all.
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


def _observer(
    tmp_path: Path,
) -> tuple[CorrectionChannelObserver, SupervisorPump, CorrectionChannel]:
  channel = CorrectionChannel(workspace=epath.Path(tmp_path))
  pump = SupervisorPump(
      supervisor=_supervisor(channel, _SpeaksOnce()),
      channel=channel,
      events_path=epath.Path(tmp_path) / "events.jsonl",
  )
  return CorrectionChannelObserver(pump=pump, channel=channel), pump, channel


def test_a_supervised_run_that_stayed_supervised_reports_nothing(
    tmp_path: Path,
):
  # The metric is an event: a healthy run leaves no key rather than a zero, so
  # a reader cannot mistake "supervised throughout" for "never measured".
  observer, _, _ = _observer(tmp_path)
  assert observer.before_destroy(_fs(tmp_path)) is None


def test_a_dead_pump_produces_the_metric_that_changes_the_outcome_word(
    tmp_path: Path,
):
  """The producer for a signal that already had a consumer.

  `rollout_outcome` reads `supervision.unhealthy` and classifies the run as
  ours. A metric with a consumer and no producer is the same defect as one with
  a producer and no consumer, so the two halves are asserted together: the
  observer emits it, and the classifier acts on it.
  """
  observer, pump, _ = _observer(tmp_path)
  pump.failure = RuntimeError("the judge went away")

  contribution = observer.before_destroy(_fs(tmp_path))
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
  observer, _, _ = _observer(tmp_path)
  (epath.Path(tmp_path) / CORRECTION_UNCLEAN_NAME).touch()
  contribution = observer.before_destroy(_fs(tmp_path))
  assert contribution is not None
  assert contribution.metrics == {SUPERVISION_METRIC: 1.0}
