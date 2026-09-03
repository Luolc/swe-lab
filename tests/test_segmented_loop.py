"""The segment loop: where it cuts, what it says next, and where it stops.

Driven against a fake actor rather than a container — the loop's own contract is
"read a terminal `result` event, decide, resume", and none of that needs a
sandbox. What *does* need one is the flag composition and the seam's real shape,
and those are the bring-up run's job, not a unit test's.

Two arms wherever a reading could be produced by the wrong thing: the
turn counter is asserted against a stream whose assistant **events** outnumber
its assistant **messages** (counting events would pass a single-arm test), and
each ceiling is asserted with a run that does *not* hit it beside the one that
does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pytest

from swe_lab.sandbox import ExecResult
from swe_lab.trace_synthesis.seam_shape import DirtySeamError
from swe_lab.trace_synthesis.segmented_loop import (
    LOG_KIND_SEGMENT,
    SegmentedRun,
    SegmentedSupervision,
    SegmentRequest,
    STOP_ACTOR_FINISHED,
    STOP_MAX_COST,
    STOP_MAX_SEGMENTS,
    STOP_NO_RESULT_EVENT,
    STOP_OTHER_ENDING,
    turns_taken,
)
from swe_lab.trace_synthesis.supervisor import (
    INTERVENTION_TAG,
    LOG_KIND_LAPSE,
    LOG_KIND_SILENT,
    LOG_KIND_SPOKE,
    NeverSpeak,
    Observation,
    PolicyLapseError,
    SpeakAt,
)

_CUT = "error_max_turns"
_DONE = "success"


def _segment(
    *,
    ids: list[str],
    subtype: str,
    session: str = "session-1",
    uuid: str = "result-uuid",
    cost: float = 0.01,
    events_per_message: int = 1,
) -> str:
  """Render one segment's worth of ``stream-json`` lines.

  Args:
    ids: The assistant message ids this segment produced, in order.
    subtype: The terminal ``result`` event's subtype.
    session: The session id every event carries.
    uuid: The terminal event's own uuid.
    cost: The cumulative cost the terminal event reports.
    events_per_message: How many assistant *events* share each message id —
      the real stream splits thinking and ``tool_use`` across events.

  Returns:
    The lines, newline-terminated.
  """
  lines: list[str] = []
  for message_id in ids:
    for repeat in range(events_per_message):
      lines.append(
          json.dumps(
              {
                  "type": "assistant",
                  "session_id": session,
                  # Every event in a real capture carries one (170 of 170 on
                  # the first end-to-end run), and it is what a resumed
                  # segment is anchored at — a fixture without it would
                  # exercise the *unanchored* path while looking like it
                  # tested the anchored one.
                  "uuid": f"{message_id}-{repeat}",
                  "message": {
                      "id": message_id,
                      "role": "assistant",
                      "content": [{"type": "text", "text": "working"}],
                  },
              }
          )
      )
  lines.append(
      json.dumps(
          {
              "type": "result",
              "subtype": subtype,
              "session_id": session,
              "uuid": uuid,
              "total_cost_usd": cost,
              "num_turns": len(ids),
          }
      )
  )
  return "".join(line + "\n" for line in lines)


@dataclass
class FakeActor:
  """Appends a canned segment to one stream each time it is launched.

  Attributes:
    segments: What each segment appends, in order; the last repeats once
      exhausted, so a test that only cares about the first two need not spell
      out the rest.
    requests: What the loop asked for, in order.
    stream: Everything appended so far — the one file the harness's ``>>``
      redirect produces.
  """

  segments: list[str]
  requests: list[SegmentRequest] = field(default_factory=list)
  stream: str = ""

  def launch(self, request: SegmentRequest) -> ExecResult:
    """Record the request and append that segment's output.

    Args:
      request: What the loop asked for.

    Returns:
      A successful process outcome — the actor's own ending is in the stream,
      not the exit code.
    """
    self.requests.append(request)
    index = min(request.index, len(self.segments) - 1)
    self.stream += self.segments[index]
    return ExecResult(0, "", "")

  def read(self) -> str:
    """Return the appended stream.

    Returns:
      Every segment so far.
    """
    return self.stream


_ANCHORED_WIRE = (
    Path(__file__).resolve().parent / "data/proxy_seam_anchored.jsonl"
).read_text(encoding="utf-8")
_DIRTY_WIRE = (
    Path(__file__).resolve().parent / "data/proxy_seam_dirty.jsonl"
).read_text(encoding="utf-8")


def _supervision(policy: Any = None, **overrides: Any) -> SegmentedSupervision:
  """Build a supervision config with roomy ceilings unless a test narrows one.

  Args:
    policy: The policy; ``NeverSpeak()`` when not given.
    **overrides: Fields to replace.

  Returns:
    The config.
  """
  built = policy or NeverSpeak()
  defaults: dict[str, Any] = {
      "policy_factory": lambda: built,
      "max_segments": 10,
      "wall_clock_seconds": 10_000.0,
      "max_cost_usd": 100.0,
      "turns_per_segment": 5,
  }
  return SegmentedSupervision(**(defaults | overrides))


def _run(
    actor: FakeActor,
    supervision: SegmentedSupervision,
    *,
    wire: str | None = _ANCHORED_WIRE,
) -> list[Any]:
  """Drive one loop over a fake actor and return its log rows.

  Args:
    actor: The fake.
    supervision: The config.
    wire: What the capture proxy's log reads as; ``None`` models a run that
      captured none, which the loop refuses to trust.

  Returns:
    The rows written, in order.
  """
  rows: list[Any] = []
  loop = SegmentedRun(
      supervision=supervision,
      task="fix the bug",
      launch=actor.launch,
      read_stream=actor.read,
      log=rows.append,
      read_wire=None if wire is None else (lambda: wire),
  )
  _ = loop.run(timeout=10_000.0)
  return rows


def _segment_rows(rows: list[Any]) -> list[Any]:
  """Filter the log to the per-segment rows.

  Args:
    rows: Every row.

  Returns:
    The segment rows, in order.
  """
  return [row for row in rows if row["kind"] == LOG_KIND_SEGMENT]


# --- the turn counter -------------------------------------------------------


def test_a_turn_is_a_message_not_an_event():
  """Counting assistant events would over-count; the control arm proves it.

  The real stream splits thinking and ``tool_use`` into separate events sharing
  one message id — 59 events for 32 turns on the first end-to-end capture. A
  stream with one event per message would pass either implementation, so this
  uses one with two.
  """
  raw = _segment(ids=["a", "b", "c"], subtype=_CUT, events_per_message=2)
  events = [json.loads(line) for line in raw.splitlines()]

  assert sum(1 for e in events if e["type"] == "assistant") == 6
  assert turns_taken(events) == 3


# --- where it stops ---------------------------------------------------------


def test_the_loop_stops_when_the_actor_says_it_is_done():
  """A `success` result ends the run, and nothing is resumed after it."""
  actor = FakeActor(segments=[_segment(ids=["a"], subtype=_DONE)])

  rows = _run(actor, _supervision())

  assert len(actor.requests) == 1
  assert _segment_rows(rows)[-1]["stop_reason"] == STOP_ACTOR_FINISHED


def test_a_turn_limited_segment_is_a_cut_and_not_an_ending():
  """The control arm for the test above: the same shape, one field different."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _run(actor, _supervision())

  assert len(actor.requests) == 2
  assert _segment_rows(rows)[0]["stop_reason"] is None
  assert _segment_rows(rows)[-1]["stop_reason"] == STOP_ACTOR_FINISHED


def test_the_segment_ceiling_stops_the_loop():
  """`max_segments` binds even though the actor would keep going."""
  actor = FakeActor(segments=[_segment(ids=["a"], subtype=_CUT)])

  rows = _run(actor, _supervision(max_segments=3))

  assert len(actor.requests) == 3
  assert _segment_rows(rows)[-1]["stop_reason"] == STOP_MAX_SEGMENTS


def test_the_cost_ceiling_stops_the_loop():
  """Cumulative cost is read off the actor's own terminal result event."""
  actor = FakeActor(segments=[_segment(ids=["a"], subtype=_CUT, cost=0.75)])

  rows = _run(actor, _supervision(max_cost_usd=0.5))

  assert len(actor.requests) == 1
  assert _segment_rows(rows)[-1]["stop_reason"] == STOP_MAX_COST


def test_a_cheap_run_is_not_stopped_by_the_cost_ceiling():
  """The control arm: the ceiling must not stop a run that stays under it."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT, cost=0.1),
          _segment(ids=["b"], subtype=_DONE, cost=0.2),
      ]
  )

  rows = _run(actor, _supervision(max_cost_usd=0.5))

  assert len(actor.requests) == 2
  assert _segment_rows(rows)[-1]["stop_reason"] == STOP_ACTOR_FINISHED


def test_a_segment_that_wrote_no_result_event_is_not_read_as_a_cut():
  """No terminal event is its own ending; resuming from it would be a guess."""
  actor = FakeActor(segments=['{"type": "system", "subtype": "init"}\n'])

  rows = _run(actor, _supervision())

  assert len(actor.requests) == 1
  assert _segment_rows(rows)[-1]["stop_reason"] == STOP_NO_RESULT_EVENT


def test_an_error_ending_stops_the_loop_rather_than_resuming():
  """Only the turn limit is a cut; every other error subtype ends the run."""
  actor = FakeActor(
      segments=[_segment(ids=["a"], subtype="error_during_execution")]
  )

  rows = _run(actor, _supervision())

  assert len(actor.requests) == 1
  last = _segment_rows(rows)[-1]
  assert last["stop_reason"] == STOP_OTHER_ENDING
  assert last["stop_subtype"] == "error_during_execution"


# --- what the next segment is told ------------------------------------------


def test_a_silent_seam_sends_the_neutral_continue():
  """Silence is the ordinary case, and it still has to say something."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _run(actor, _supervision(neutral_continue="Carry on."))

  assert actor.requests[1].prompt == "Carry on."
  assert [r["kind"] for r in rows if r["kind"] == LOG_KIND_SILENT]


def test_a_correction_becomes_the_next_segments_prompt_tagged():
  """The seam *is* the delivery: the rendered intervention is the prompt."""
  # Segment 0 emits one assistant event plus its result, so the policy is
  # consulted at cursor 2.
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )
  policy = SpeakAt(cursors=frozenset({2}), text="check the failing test first")

  rows = _run(actor, _supervision(policy))

  assert actor.requests[1].prompt == (
      f"<{INTERVENTION_TAG}>\ncheck the failing test first\n"
      f"</{INTERVENTION_TAG}>"
  )
  spoke = [row for row in rows if row["kind"] == LOG_KIND_SPOKE]
  assert len(spoke) == 1
  assert spoke[0]["cut_at_turn"] == 1


def test_the_next_segment_resumes_the_session_the_last_one_reported():
  """The session id is read off the terminal event, never assumed."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT, session="abc-123"),
          _segment(ids=["b"], subtype=_DONE, session="abc-123"),
      ]
  )

  _ = _run(actor, _supervision())

  assert actor.requests[0].resume_session_id is None
  assert actor.requests[1].resume_session_id == "abc-123"


# --- the account ------------------------------------------------------------


def test_a_resumed_segment_records_that_the_seam_fabricated_a_record():
  """The claim is about what we did, and it is what locates the artifact.

  A consumer cannot find the synthetic assistant record by inspecting the
  corpus — it carries no marker there — so this row plus the anchors is the
  whole of how it is located.
  """
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT, uuid="cut-uuid"),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _segment_rows(_run(actor, _supervision()))

  assert rows[0]["resume_artifact_expected"] is False
  assert rows[0]["anchor_result_uuid"] == "cut-uuid"
  assert rows[0]["anchor_event_index"] == 1
  assert rows[1]["resume_artifact_expected"] is True


def test_a_policy_lapse_is_bounded_to_its_seam_and_the_run_goes_on():
  """A named hole, and the next seam is judged normally — as in A′."""

  @dataclass
  class LapsingOnce:
    seen: int = 0

    @property
    def name(self) -> str:
      return "lapsing-once"

    def consider(self, observation: Observation) -> None:
      del observation
      self.seen += 1
      if self.seen == 1:
        raise PolicyLapseError("judge call failed", finish_reason="length")
      return None

  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_CUT),
          _segment(ids=["c"], subtype=_DONE),
      ]
  )

  rows = _run(actor, _supervision(LapsingOnce()))

  lapses = [row for row in rows if row["kind"] == LOG_KIND_LAPSE]
  assert len(lapses) == 1
  assert lapses[0]["finish_reason"] == "length"
  assert actor.requests[1].prompt == "Continue."
  assert [row for row in rows if row["kind"] == LOG_KIND_SILENT]
  assert len(actor.requests) == 3


# --- the anchored seam, and the guard that is this path's whole argument -----


def test_a_resumed_segment_is_anchored_at_the_last_message_record():
  """`--resume-session-at` is what keeps the seam free of a fabricated turn.

  Anchored at the last *message* record, not the terminal `result` event: the
  result is the run's own bookkeeping and is not a message the resume can
  attach to.
  """
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT, uuid="result-uuid"),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  _ = _run(actor, _supervision())

  assert actor.requests[0].resume_at_message_id is None
  assert actor.requests[1].resume_at_message_id == "a-0"


def test_a_dirty_seam_is_recorded_and_does_not_stop_the_run():
  """The seam is an observation, not an acceptance condition.

  Recorded so a reader can see what the seam looked like, and deliberately
  non-blocking: the owner ruled that seam shape is post-processing's problem
  and asked for a loop that runs.
  """
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _segment_rows(_run(actor, _supervision(), wire=_DIRTY_WIRE))

  assert len(actor.requests) == 2
  assert rows[1]["seam"]["synthetic_assistants"] == 1


def test_a_clean_seam_records_nothing_to_report():
  """The control arm: the field must not read as dirty for every wire."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _segment_rows(_run(actor, _supervision()))

  assert rows[1]["seam"] is None


def test_the_guard_can_be_turned_on_and_then_it_stops_the_run():
  """Off by default; on, it raises. Both are asserted so neither is assumed."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  with pytest.raises(DirtySeamError, match="did not hold"):
    _ = _run(actor, _supervision(guard_seam=True), wire=_DIRTY_WIRE)


def test_a_run_without_a_wire_still_runs():
  """No capture to read is not a failure: the seam is recorded, not enforced."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _segment_rows(_run(actor, _supervision(), wire=None))

  assert len(actor.requests) == 2
  assert rows[1]["seam"] is None


def test_the_plain_resume_path_runs_and_says_which_path_it_took():
  """Both resume flavours are supported; the account says which one ran."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _segment_rows(
      _run(actor, _supervision(anchor_resume=False), wire=None)
  )

  assert actor.requests[1].resume_session_id is not None
  assert actor.requests[1].resume_at_message_id is None
  assert [row["anchored"] for row in rows] == [False, False]


def test_an_anchored_run_says_so_on_every_resumed_segment():
  """The control arm for the row above: not False for everything."""
  actor = FakeActor(
      segments=[
          _segment(ids=["a"], subtype=_CUT),
          _segment(ids=["b"], subtype=_DONE),
      ]
  )

  rows = _segment_rows(_run(actor, _supervision()))

  assert [row["anchored"] for row in rows] == [False, True]
