"""The supervisor component's structural properties.

Each test here pins an invariant the plan states in words
(``docs/trace-synthesis/plans/task-05-supervisor-the-component.md``). A sentence
in that plan without a test below is a wish, per ``AGENTS.md``.

**What a policy may see, and what may become evidence** — the component's own
interface. What a *carrier* does with a decision (the rows it writes, the
lapses it bounds, the corrections it delivers) belongs to the carrier, and the
one carrier is the segment loop: those invariants are pinned in
``test_segmented_loop.py``, not duplicated here.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from swe_lab.conversation import Message, Role, TextBlock, ToolResultBlock
from swe_lab.trace_synthesis.supervisor import (
    evidence_of,
    Intervention,
    InterventionTooLongError,
    MAX_INTERVENTION_CHARS,
    Observation,
)

# Exactly what a policy may see. Adding a field to Observation must fail this
# test, which is the point: a denylist catches the names we thought of, an
# allowlist catches the one we did not.
ALLOWED_OBSERVATION_FIELDS = {
    "task",
    "evidence",
    "cursor",
    "said",
    "guidebook",
    "running_state",
}


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


def test_a_policy_may_see_exactly_these_six_things() -> None:
  """The interface is an allowlist, so a new field has to be argued for.

  The guidebook is on it: the phase-B artifact reaches the policy beside the
  actor's evidence. That it *arrives* on a real run is the carrier's job and is
  asserted there, by
  ``test_segmented_decision_rows_distinguish_both_guidebook_modes``.
  """
  fields = {f.name for f in dataclasses.fields(Observation)}
  assert fields == ALLOWED_OBSERVATION_FIELDS


@pytest.mark.parametrize(
    "privileged_field",
    [
        "gold_patch",
        "reference_patch",
        "test_patch",
        "hidden_tests",
        "fail_to_pass",
        "pass_to_pass",
        "fix_commit",
    ],
)
def test_supervisor_input_rejects_separate_privileged_material(
    privileged_field: str,
) -> None:
  """The guidebook is the only privileged derivative in the interface."""
  values: dict[str, Any] = {
      "task": "the task",
      "evidence": (),
      "cursor": 0,
      "said": (),
      "guidebook": "the reviewed derivative",
      privileged_field: "PRIVILEGED-SENTINEL",
  }

  with pytest.raises(TypeError, match=privileged_field):
    Observation(**values)


def test_the_task_is_given_not_read_off_the_stream() -> None:
  """The goal reaches the policy, and does not depend on watching from event 0.

  The barrier keeps out the solution, not the goal: a supervisor that cannot
  see what was asked cannot tell deviation from progress. Handing it over means
  no message on the stream has to be guessed to *be* the brief — and the two
  halves are asserted together here, because either alone is satisfiable by the
  wrong design: the task is a field of the interface, **and** a user text on the
  stream never becomes evidence, so nothing on that stream could stand in for
  it.
  """
  assert "task" in ALLOWED_OBSERVATION_FIELDS
  assert evidence_of([user_text_event("actually, try the other file")]) == ()


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
