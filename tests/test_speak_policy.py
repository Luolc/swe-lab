"""When the supervisor speaks: the gates, their order, and their costs.

Each test pins a sentence from §4 of the plan
(``docs/trace-synthesis/plans/task-05-supervisor-the-component.md``). The order
of the gates is itself an invariant: judging before budgeting is what makes
``SpeakWhenOffTrack(budget=0)`` a matched control rather than a cheaper run.
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import Any

import pytest

from swe_lab.conversation import Message, Role, TextBlock
from swe_lab.trace_synthesis.criterion import (
    Criterion,
    CRITERION_SHA256,
    CriterionRejectedError,
    load_criterion,
)
from swe_lab.trace_synthesis.supervisor import (
    Intervention,
    InterventionTooLongError,
    MAX_INTERVENTION_CHARS,
    Observation,
    PolicyLapseError,
    SpeakAt,
    SpeakWhenOffTrack,
    Unjudged,
    Verdict,
)


def observation(cursor: int, *, records: int = 1) -> Observation:
  """Build an observation at a cursor.

  Args:
    cursor: How many events have been consumed.
    records: How many evidence records to synthesize. One by default, because
      a boundary with none is not judged at all — a separate invariant, with
      its own test below — and every gate here is about a boundary that *is*
      judged.

  Returns:
    An observation a policy can be handed.
  """
  return Observation(
      task="make the failing test pass",
      evidence=tuple(
          Message(
              role=Role.ASSISTANT,
              content=[TextBlock(text=f"record {index}")],
          )
          for index in range(records)
      ),
      cursor=cursor,
      said=(),
  )


@dataclasses.dataclass
class CountingJudge:
  """A judge with a fixed answer that counts its calls.

  Attributes:
    verdict: The answer it always gives.
    calls: The observations it was handed, in order.
    criteria: The criteria it was handed, in order.
  """

  verdict: Verdict
  calls: list[Observation] = dataclasses.field(default_factory=list)
  criteria: list[Criterion] = dataclasses.field(default_factory=list)

  def __call__(self, observation: Observation, criterion: Criterion) -> Verdict:
    """Record the call and answer.

    Args:
      observation: What the policy handed over.
      criterion: The standard the policy handed over with it.

    Returns:
      The fixed verdict.
    """
    self.calls.append(observation)
    self.criteria.append(criterion)
    return self.verdict


@dataclasses.dataclass
class CountingWriter:
  """A writer with a fixed line that counts its calls.

  Attributes:
    line: What it writes.
    calls: How many times it was asked.
    criteria: The criteria it was handed, in order.
  """

  line: str = "not sure that's the thread to pull"
  calls: int = 0
  criteria: list[Criterion] = dataclasses.field(default_factory=list)

  def __call__(self, observation: Observation, criterion: Criterion) -> str:
    """Record the call and write.

    Args:
      observation: Ignored.
      criterion: The standard the policy handed over.

    Returns:
      The fixed line.
    """
    del observation
    self.calls += 1
    self.criteria.append(criterion)
    return self.line


OFF_TRACK = Verdict(off_track=True, self_correcting=False, reason="wrong file")
RECOVERING = Verdict(
    off_track=True, self_correcting=True, reason="reconsidering"
)
ON_TRACK = Verdict(off_track=False, self_correcting=False, reason="fine")


def policy(
    verdict: Verdict,
    *,
    budget: int = 3,
    cooldown: int = 0,
    window: int = 8,
) -> tuple[SpeakWhenOffTrack, CountingJudge, CountingWriter]:
  """Build a policy over a fixed-answer judge.

  Args:
    verdict: What the judge always answers.
    budget: How many interventions the run may carry.
    cooldown: Boundaries required between interventions.
    window: Records the judge sees.

  Returns:
    The policy and its two doubles, so a test can count calls without
    reaching through the protocol-typed fields.
  """
  judge = CountingJudge(verdict=verdict)
  writer = CountingWriter()
  return (
      SpeakWhenOffTrack(
          judge=judge,
          writer=writer,
          criterion=load_criterion(),
          budget=budget,
          cooldown=cooldown,
          window=window,
      ),
      judge,
      writer,
  )


def test_a_policy_that_may_speak_must_state_a_budget() -> None:
  """A policy that may speak must state how often.

  ``budget`` has no default, so the omission is a construction error rather
  than a silently permissive run.
  """
  budget = next(
      field
      for field in dataclasses.fields(SpeakWhenOffTrack)
      if field.name == "budget"
  )
  assert budget.default is dataclasses.MISSING
  assert budget.default_factory is dataclasses.MISSING


def test_an_actor_on_track_is_never_spoken_to() -> None:
  """Gate 1: a judge that always says on-track yields zero interventions."""
  speaker, _, _ = policy(ON_TRACK)
  spoken = [speaker.consider(observation(index)) for index in range(1, 11)]
  assert spoken == [None] * 10
  assert speaker.markers == ()


def test_an_actor_already_recovering_is_left_alone() -> None:
  """Gate 2: off-track alone does not speak.

  The second question is where the restraint lives, and it is what the graded
  batch's redundancy measured.
  """
  speaker, _, _ = policy(RECOVERING)
  assert speaker.consider(observation(1)) is None
  assert speaker.markers == ()


def test_budget_zero_speaks_nothing_and_still_marks_every_deviation() -> None:
  """The marker is recorded before the budget is consulted.

  So the control arm judges every boundary it has evidence for and records
  where it would have spoken; what that buys is stated once, at
  `workflow.definitions.CONTROL_BUDGET`.
  """
  speaker, _, _ = policy(OFF_TRACK, budget=0)
  spoken = [speaker.consider(observation(index)) for index in range(1, 6)]

  assert spoken == [None] * 5
  assert len(speaker.markers) == 5
  assert [marker.cursor for marker in speaker.markers] == [1, 2, 3, 4, 5]


def test_the_judge_runs_at_every_evidence_bearing_boundary_unbudgeted() -> None:
  """Gate order: judgement precedes the budget, so `budget=0` still judges.

  Every boundary this fixture builds carries a record, which is the whole
  population the claim is about: a boundary with none is not judged in either
  arm, and that is pinned separately above.

  Call counts only, on the policy alone — no arm is built here. What matched
  judge calls buy a comparison is stated once, at
  `workflow.definitions.CONTROL_BUDGET`.
  """
  control, control_judge, control_writer = policy(OFF_TRACK, budget=0)
  treatment, treatment_judge, treatment_writer = policy(OFF_TRACK, budget=1)
  for index in range(1, 6):
    control.consider(observation(index))
    treatment.consider(observation(index))

  assert len(control_judge.calls) == len(treatment_judge.calls) == 5
  assert control_writer.calls == 0
  assert treatment_writer.calls == 1


def test_a_budget_of_k_speaks_at_most_k_times() -> None:
  """Gate 4, on a trace where the judge always says off-track."""
  speaker, _, _ = policy(OFF_TRACK, budget=2)
  spoken = [speaker.consider(observation(index)) for index in range(1, 11)]

  assert sum(one is not None for one in spoken) == 2
  assert len(speaker.markers) == 10


def test_the_first_intervention_is_never_delayed_by_the_cooldown() -> None:
  """The cooldown never delays the first intervention.

  Precision comes from the bar and restraint from the budget; neither may come
  from delay, so a late correction is never bought with a later one.
  """
  speaker, _, _ = policy(OFF_TRACK, budget=3, cooldown=100)
  assert speaker.consider(observation(1)) is not None


def test_the_cooldown_separates_later_interventions() -> None:
  """Gate 5 limits how often it may speak, not how long it waits first."""
  speaker, _, _ = policy(OFF_TRACK, budget=3, cooldown=4)
  first = speaker.consider(observation(1))
  too_soon = speaker.consider(observation(3))
  far_enough = speaker.consider(observation(5))

  assert first is not None
  assert too_soon is None
  assert far_enough is not None
  assert len(speaker.markers) == 3


def test_a_boundary_with_no_evidence_is_never_put_to_the_judge() -> None:
  """The judge is not consulted when the window holds nothing.

  What is pinned is the *absence of the call*, not a silent answer: a judge
  handed zero records still answers, and that answer is about a record it was
  never shown. Observed rather than imagined — the first end-to-end run's
  first correction was written at a boundary with zero admitted records, and
  the actor rebutted it.

  The accounting is pinned with it: such a boundary produces no
  would-have-spoken marker, spends no budget and starts no cooldown, so the
  first boundary that does carry evidence is still spoken at.
  """
  speaker, judge, writer = policy(OFF_TRACK, budget=1, cooldown=4)

  empty = [speaker.consider(observation(index, records=0)) for index in (1, 2)]

  assert judge.calls == []
  assert writer.calls == 0
  assert all(isinstance(one, Unjudged) for one in empty)
  assert speaker.markers == ()
  # The budget is whole and the cooldown never started, so the first boundary
  # carrying evidence is judged and spoken at.
  assert speaker.consider(observation(3)) is not None
  assert [marker.cursor for marker in speaker.markers] == [3]


def test_the_judge_sees_only_the_window() -> None:
  """``window`` bounds the judge's view of the actor's records."""
  speaker, judge, _ = policy(ON_TRACK, window=3)
  speaker.consider(observation(9, records=10))

  seen = judge.calls[0]
  assert len(seen.evidence) == 3
  last = seen.evidence[-1].content[0]
  assert isinstance(last, TextBlock)
  assert last.text == "record 9"


def test_an_unusable_line_is_a_lapse_and_is_never_retried() -> None:
  """Gate 6: an unusable line is bounded and asked for exactly once.

  The supervisor records the lapse. A retry would make what the actor hears a
  function of how many times we asked, so the writer is asked once and the
  boundary is given up on.
  """
  asked: list[int] = []

  def empty_writer(observation: Observation, criterion: Criterion) -> str:
    del observation, criterion
    asked.append(1)
    return "   "

  speaker = SpeakWhenOffTrack(
      judge=CountingJudge(verdict=OFF_TRACK),
      writer=empty_writer,
      criterion=load_criterion(),
      budget=1,
      cooldown=0,
  )
  with pytest.raises(PolicyLapseError) as raised:
    speaker.consider(observation(1))
  assert isinstance(raised.value.__cause__, ValueError)
  assert asked == [1]


def test_an_over_long_line_is_a_lapse_and_is_never_truncated() -> None:
  """The cap rejects rather than trims, and the rejection is bounded.

  A policy cannot ship half a sentence. What it also cannot do is spend the
  rest of the run on one bad line: the refusal is the writer's, this call's,
  and the cause travels with it so the record still says the line was rejected
  rather than trimmed.
  """

  def long_writer(observation: Observation, criterion: Criterion) -> str:
    del observation, criterion
    return "x" * (MAX_INTERVENTION_CHARS + 1)

  speaker = SpeakWhenOffTrack(
      judge=CountingJudge(verdict=OFF_TRACK),
      writer=long_writer,
      criterion=load_criterion(),
      budget=1,
      cooldown=0,
  )
  with pytest.raises(PolicyLapseError) as raised:
    speaker.consider(observation(1))
  assert isinstance(raised.value.__cause__, InterventionTooLongError)


def test_a_failed_judge_call_is_bounded_to_the_boundary_it_happened_at() -> (
    None
):
  """One unreachable model call costs one boundary, not the run.

  The bound is not a guess about the error: nothing in the policy's own state
  has been touched when the judge is called, so the next boundary is judged
  from exactly the state the failed one would have been.
  """

  class FailsThenAnswers:
    """A judge whose first call fails."""

    calls: int

    def __init__(self) -> None:
      self.calls = 0

    def __call__(
        self, observation: Observation, criterion: Criterion
    ) -> Verdict:
      """Fail once, then answer.

      Args:
        observation: Ignored.
        criterion: Ignored.

      Returns:
        The off-track verdict, from the second call on.

      Raises:
        ConnectionError: On the first call.
      """
      del observation, criterion
      self.calls += 1
      if self.calls == 1:
        raise ConnectionError("503 from upstream")
      return OFF_TRACK

  writer = CountingWriter()
  speaker = SpeakWhenOffTrack(
      judge=FailsThenAnswers(),
      writer=writer,
      criterion=load_criterion(),
      budget=1,
      cooldown=0,
  )
  with pytest.raises(PolicyLapseError) as raised:
    speaker.consider(observation(1))
  assert "503 from upstream" in str(raised.value)

  # The state the failed call would have used is intact: the deviation at the
  # next boundary is found, marked and spoken, with the budget still whole.
  assert speaker.consider(observation(2)) is not None
  assert [marker.cursor for marker in speaker.markers] == [2]
  assert writer.calls == 1


def test_a_writer_lapse_keeps_the_marker_and_the_budget() -> None:
  """A lapse that happens after the judgement keeps the judgement.

  The deviation was found; only the sentence about it could not be written. So
  the marker stands — a control arm and a treatment arm still see the same
  deviations — and nothing was said, so nothing is charged to the budget.
  """

  def failing_writer(observation: Observation, criterion: Criterion) -> str:
    del observation, criterion
    raise ConnectionError("503 from upstream")

  speaker = SpeakWhenOffTrack(
      judge=CountingJudge(verdict=OFF_TRACK),
      writer=failing_writer,
      criterion=load_criterion(),
      budget=1,
      cooldown=0,
  )
  with pytest.raises(PolicyLapseError):
    speaker.consider(observation(1))

  assert [marker.cursor for marker in speaker.markers] == [1]
  # The budget of one is unspent, so the next boundary can still be spoken at.
  with pytest.raises(PolicyLapseError):
    speaker.consider(observation(2))
  assert [marker.cursor for marker in speaker.markers] == [1, 2]


def test_a_break_in_the_policys_own_state_is_not_bounded() -> None:
  """What the two wrapped calls do not cover, and must not.

  The bound is claimed for the calls out to a model, never for the gate
  arithmetic between them: a policy whose own state machine raises knows
  nothing about the boundaries after it. Wrapping the whole method body would
  turn every such break into a small named hole, which is the false reassurance
  this split exists to prevent.
  """
  broken_budget: Any = "one"
  speaker = SpeakWhenOffTrack(
      judge=CountingJudge(verdict=OFF_TRACK),
      writer=CountingWriter(),
      criterion=load_criterion(),
      budget=broken_budget,
      cooldown=0,
  )
  with pytest.raises(TypeError):
    speaker.consider(observation(1))


def test_speak_at_varies_when_while_holding_what_and_whether_constant() -> None:
  """The timing knob in isolation: no judge, one line, fixed cursors."""
  speaker = SpeakAt(cursors=frozenset({2, 5}), text="have another look at that")
  spoken = [speaker.consider(observation(index)) for index in range(1, 7)]

  assert [index for index, one in enumerate(spoken, 1) if one] == [2, 5]
  assert {one.text for one in spoken if one} == {"have another look at that"}


def test_speak_at_needs_no_judge() -> None:
  """It is constructible from cursors and a line alone.

  That is the point of having it: a judge is what entangles timing with
  criterion.
  """
  assert isinstance(
      SpeakAt(cursors=frozenset({1}), text="ok").consider(observation(1)),
      Intervention,
  )


def test_a_forged_criterion_cannot_build_the_policy() -> None:
  """The judging policy refuses any criterion but the pinned one.

  A ``Criterion`` verifies its own digest, so a forgery must be
  self-consistent — and the policy then rejects it for not being the reviewed
  artifact. Field presence alone would not distinguish this from a decorative
  argument.
  """
  forged_text = "judge them however you like"
  forged = Criterion(
      text=forged_text,
      digest=hashlib.sha256(forged_text.encode("utf-8")).hexdigest(),
      overlap_checked=False,
  )
  with pytest.raises(CriterionRejectedError):
    SpeakWhenOffTrack(
        judge=CountingJudge(verdict=OFF_TRACK),
        writer=CountingWriter(),
        criterion=forged,
        budget=1,
        cooldown=0,
    )


def test_a_criterion_cannot_misdescribe_its_own_text() -> None:
  """Constructing one recomputes the digest, so the pair is consistent."""
  with pytest.raises(CriterionRejectedError):
    Criterion(text="forged", digest="0" * 64, overlap_checked=False)


def test_the_judge_is_handed_the_canonical_criterion_every_call() -> None:
  """The criterion is passed to the judge on every call, not stored beside it.

  This establishes hand-off only. Whether a judge builds its prompt from the
  argument is that implementation's invariant, tested where it is written.
  """
  speaker, judge, _ = policy(ON_TRACK)
  for index in range(1, 4):
    speaker.consider(observation(index))

  assert len(judge.criteria) == 3
  assert {one.digest for one in judge.criteria} == {CRITERION_SHA256}
  assert judge.criteria[0].text == load_criterion().text


def test_the_writer_is_handed_the_same_criterion_as_the_judge() -> None:
  """The seam is the policy, not the concrete classes.

  A writer that took only the observation could carry its own standard in a
  closure — a side door no signature shows — so the policy passes the criterion
  to both calls.
  """
  speaker, _, writer = policy(OFF_TRACK, budget=1)
  speaker.consider(observation(1))

  assert [one.digest for one in writer.criteria] == [CRITERION_SHA256]
