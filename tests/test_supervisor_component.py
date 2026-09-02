"""The supervisor component's structural properties.

Each test here pins an invariant the plan states in words
(``docs/trace-synthesis/plans/task-05-supervisor-the-component.md``). A sentence
in that plan without a test below is a wish, per ``AGENTS.md``.
"""

from __future__ import annotations

import dataclasses

import pytest

from swe_lab.conversation import Message, Role, TextBlock, ToolResultBlock
from swe_lab.trace_synthesis.supervisor import (
    evidence_of,
    Intervention,
    InterventionTooLongError,
    MAX_INTERVENTION_CHARS,
    NeverSpeak,
    Observation,
    Supervisor,
)

# Exactly what a policy may see. Adding a field to Observation must fail this
# test, which is the point: a denylist catches the names we thought of, an
# allowlist catches the one we did not.
ALLOWED_OBSERVATION_FIELDS = {"task", "evidence", "cursor", "said"}

PRIVILEGED_NAMES = (
    "gold_patch",
    "reference_patch",
    "test_patch",
    "hidden_tests",
    "fail_to_pass",
    "pass_to_pass",
    "fix_commit",
)


def assistant_event(text: str) -> dict[str, object]:
  """Build an assistant stream event.

  Args:
    text: The assistant's text.

  Returns:
    One decoded ``stream-json`` event.
  """
  return {
      "type": "assistant",
      "message": {
          "role": "assistant",
          "content": [{"type": "text", "text": text}],
      },
  }


def tool_result_event(text: str) -> dict[str, object]:
  """Build a tool-result event, which arrives on the ``user`` channel.

  Args:
    text: The tool's output.

  Returns:
    One decoded ``stream-json`` event.
  """
  return {
      "type": "user",
      "message": {
          "role": "user",
          "content": [
              {"type": "tool_result", "tool_use_id": "t1", "content": text}
          ],
      },
  }


def user_text_event(text: str) -> dict[str, object]:
  """Build a plain user message — the prompt, or a supervisor correction.

  Args:
    text: The message text.

  Returns:
    One decoded ``stream-json`` event.
  """
  return {
      "type": "user",
      "message": {"role": "user", "content": [{"type": "text", "text": text}]},
  }


DEFAULT_TEXT = "have you checked the failing test first?"


def text_of(evidence: tuple[Message, ...]) -> str:
  """Flatten every readable block of the evidence into one string.

  Args:
    evidence: What a supervisor was given.

  Returns:
    The concatenated text, for asserting what did and did not get through.
  """
  parts: list[str] = []
  for message in evidence:
    for block in message.content:
      if isinstance(block, TextBlock):
        parts.append(block.text)
      elif isinstance(block, ToolResultBlock):
        parts.append(block.content)
  return " ".join(parts)


class Speaks:
  """A policy that says one fixed thing at every event."""

  _text: str

  def __init__(self, text: str = DEFAULT_TEXT):
    self._text = text

  @property
  def name(self) -> str:
    """Return the policy's name.

    Returns:
      ``"speaks"``.
    """
    return "speaks"

  def consider(self, observation: Observation) -> Intervention:
    """Speak, always.

    Args:
      observation: Ignored.

    Returns:
      The fixed intervention.
    """
    del observation
    return Intervention(text=self._text)


def test_supervisor_input_carries_no_privileged_field() -> None:
  """The barrier is the constructor: no field can carry the solution.

  A supervisor handed the gold patch or the hidden tests would produce traces
  whose steering came from the answer rather than from the guidebook, and no
  reading of the trace afterwards could tell the difference.
  """
  fields = {f.name for f in dataclasses.fields(Observation)}
  assert fields == ALLOWED_OBSERVATION_FIELDS
  assert not fields.intersection(PRIVILEGED_NAMES)


def test_the_task_is_given_not_read_off_the_stream() -> None:
  """The goal reaches the policy, and does not depend on watching from event 0.

  The barrier keeps out the solution, not the goal: a supervisor that cannot
  see what was asked cannot tell deviation from progress. Taking it at
  construction means no message on the stream has to be guessed to *be* the
  brief.
  """
  seen: list[str] = []

  class ReadsTheTask:
    """A policy that records the task it was shown."""

    @property
    def name(self) -> str:
      """Return the policy's name.

      Returns:
        ``"reads-the-task"``.
      """
      return "reads-the-task"

    def consider(self, observation: Observation) -> None:
      """Record the task, say nothing.

      Args:
        observation: What the supervisor offers.

      Returns:
        ``None``.
      """
      seen.append(observation.task)
      return None

  supervisor = Supervisor(
      policy=ReadsTheTask(),
      task="Fix the failing colour test in qutebrowser",
      sink=lambda _: None,
      log=lambda _: None,
  )
  _ = supervisor.observe(assistant_event("I will run the tests"))
  assert seen == ["Fix the failing colour test in qutebrowser"]


def test_a_supervisor_attached_mid_run_admits_no_user_text() -> None:
  """Where the supervisor started must not change what counts as evidence.

  A filter that promoted "the first user text I happened to see" to the brief
  would admit an outside interjection as the task whenever it attached after
  the actor had already spoken — or after an event it could not represent.
  """
  supervisor = Supervisor(
      policy=NeverSpeak(),
      task="the real task",
      sink=lambda _: None,
      log=lambda _: None,
  )
  rows: list[dict[str, object]] = []
  supervisor.log = lambda row: rows.append(dict(row))
  _ = supervisor.observe(user_text_event("actually, try the other file"))
  assert evidence_of([user_text_event("actually, try the other file")]) == ()
  assert rows[0]["evidence"] == "excluded-external-text"


def test_the_supervisors_own_words_never_come_back_as_evidence() -> None:
  """Its own correction is memory, not observation.

  The correction returns on the same stream as a ``user`` message. Admitted as
  evidence, the supervisor would be reading its own output as something the
  actor did.
  """
  events = [
      user_text_event("Fix the failing colour test"),
      assistant_event("I will run the tests"),
      tool_result_event("3 failed"),
      user_text_event(Intervention(text="check the ordering").rendered()),
  ]
  evidence = evidence_of(events)
  assert "supervisor_note" not in text_of(evidence)
  assert "check the ordering" not in text_of(evidence)


def test_no_user_text_is_evidence_whoever_wrote_it() -> None:
  """Evidence is what the actor produced; the brief arrives by another route.

  The rule cuts on **origin**: a correction this supervisor wrote and an
  outside interjection are both user messages, neither is an observation of
  what the actor did, and the task statement does not need this path because it
  is handed over at construction.
  """
  events = [
      user_text_event("Fix the failing colour test"),
      assistant_event("working on it"),
      tool_result_event("3 failed"),
      user_text_event(Intervention(text="check the ordering").rendered()),
      user_text_event("actually, try the other file"),
  ]
  evidence = evidence_of(events)
  assert [m.role for m in evidence] == [Role.ASSISTANT, Role.USER]
  assert text_of(evidence) == "working on it 3 failed"


def test_every_event_is_dispositioned_in_the_record() -> None:
  """The log says *why* a message was not judged, rather than omitting it."""
  rows: list[dict[str, object]] = []
  supervisor = Supervisor(
      policy=NeverSpeak(),
      task="the task",
      sink=lambda _: None,
      log=lambda row: rows.append(dict(row)),
  )
  for event in (
      user_text_event("the task"),
      assistant_event("ok"),
      tool_result_event("output"),
      user_text_event(Intervention(text="a nudge").rendered()),
      user_text_event("someone else"),
  ):
    _ = supervisor.observe(event)
  assert [r["evidence"] for r in rows] == [
      "excluded-external-text",
      "assistant",
      "tool-result",
      "excluded-own-intervention",
      "excluded-external-text",
  ]


def test_what_it_said_is_remembered_outside_the_evidence() -> None:
  """Memory and evidence are different channels.

  Since its own words never return as evidence, a policy has nothing to check
  against unless the supervisor keeps them — and would repeat itself forever.
  """
  seen: list[tuple[int, int]] = []

  class Records:
    """A policy that records what it was given, then speaks."""

    @property
    def name(self) -> str:
      """Return the policy's name.

      Returns:
        ``"records"``.
      """
      return "records"

    def consider(self, observation: Observation) -> Intervention:
      """Record the sizes of both channels, then speak.

      Args:
        observation: What the supervisor offers.

      Returns:
        A fresh intervention.
      """
      seen.append((len(observation.evidence), len(observation.said)))
      return Intervention(text=f"nudge {len(observation.said)}")

  supervisor = Supervisor(
      policy=Records(),
      task="the task",
      sink=lambda _: None,
      log=lambda _: None,
  )
  _ = supervisor.observe(assistant_event("one"))
  _ = supervisor.observe(assistant_event("two"))
  assert seen == [(1, 0), (2, 1)]


def test_an_over_length_intervention_is_refused() -> None:
  """Over-cap text raises rather than truncating.

  Truncation would ship half a sentence to the actor and record it as a
  delivered intervention.
  """
  Intervention(text="x" * MAX_INTERVENTION_CHARS)
  with pytest.raises(InterventionTooLongError):
    Intervention(text="x" * (MAX_INTERVENTION_CHARS + 1))


def test_every_intervention_carries_its_tag() -> None:
  """What reaches the actor is attributable as external."""
  rendered = Intervention(text="try the other direction").rendered()
  assert rendered.startswith("<supervisor_note>")
  assert rendered.endswith("</supervisor_note>")
  assert "try the other direction" in rendered


def test_the_supervisor_emits_only_its_own_message() -> None:
  """It never writes a tool result or an assistant turn.

  Everything the actor observed stays exactly as the tool produced it; the
  supervisor adds a message and alters nothing.
  """
  written: list[str] = []
  rows: list[dict[str, object]] = []
  supervisor = Supervisor(
      policy=Speaks(),
      task="the task",
      sink=written.append,
      log=lambda row: rows.append(dict(row)),
  )
  supervisor.observe(tool_result_event("the tool's own bytes"))
  assert written == [Intervention(text=DEFAULT_TEXT).rendered()]
  assert "the tool's own bytes" not in "".join(written)


def test_a_policy_that_raises_is_recorded_as_a_gap() -> None:
  """A dropped decision appears in the record, never silently skipped.

  A judge that fails leaves the boundary unjudged either way; what must not
  happen is that the record looks the same as one where the supervisor chose
  silence.
  """

  class Raises:
    """A policy whose judge fails."""

    @property
    def name(self) -> str:
      """Return the policy's name.

      Returns:
        ``"raises"``.
      """
      return "raises"

    def consider(self, observation: Observation) -> Intervention | None:
      """Fail.

      Args:
        observation: Ignored.

      Returns:
        Never returns.

      Raises:
        RuntimeError: Always.
      """
      del observation
      raise RuntimeError("judge exploded")

  rows: list[dict[str, object]] = []
  supervisor = Supervisor(
      policy=Raises(),
      task="the task",
      sink=lambda _: None,
      log=lambda row: rows.append(dict(row)),
  )
  supervisor.observe(assistant_event("hello"))
  assert [r["kind"] for r in rows] == ["gap"]
  assert "judge exploded" in str(rows[0]["reason"])


def test_a_failing_sink_mutes_but_never_ends_the_run() -> None:
  """A dead channel stops speech and is recorded; it does not close anything.

  Closing the sink is what ends the actor's run, so a write failure must not
  escalate into termination.
  """

  def broken(_: str) -> None:
    raise OSError("broken pipe")

  rows: list[dict[str, object]] = []
  supervisor = Supervisor(
      policy=Speaks(),
      task="the task",
      sink=broken,
      log=lambda row: rows.append(dict(row)),
  )
  supervisor.observe(assistant_event("one"))
  supervisor.observe(assistant_event("two"))
  assert [r["kind"] for r in rows] == ["gap", "gap"]
  assert "broken pipe" in str(rows[0]["reason"])
  assert "not attempted" in str(rows[1]["reason"])


def test_the_log_accounts_for_every_event() -> None:
  """One row per event consumed: a judgement, a silence, or a gap."""
  rows: list[dict[str, object]] = []
  supervisor = Supervisor(
      policy=NeverSpeak(),
      task="the task",
      sink=lambda _: None,
      log=lambda row: rows.append(dict(row)),
  )
  events = [assistant_event("a"), tool_result_event("b"), {"type": "system"}]
  for event in events:
    supervisor.observe(event)
  assert [r["cursor"] for r in rows] == [1, 2, 3]
  assert {r["kind"] for r in rows} == {"silent"}
  assert {r["policy"] for r in rows} == {"never-speak"}


def test_a_policy_is_replaceable_without_touching_anything_else() -> None:
  """The seam is real: swapping the policy is the whole of the difference."""
  spoken: list[str] = []
  silent: list[str] = []
  for policy, sink in ((Speaks(), spoken), (NeverSpeak(), silent)):
    supervisor = Supervisor(
        policy=policy,
        task="the task",
        sink=sink.append,
        log=lambda _: None,
    )
    supervisor.observe(assistant_event("same event"))
  assert len(spoken) == 1
  assert not silent
