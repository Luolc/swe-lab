"""The supervisor: what it may see, when it speaks, what it may say.

The component behind task 05 (``docs/trace-synthesis/plans/``). It consumes the
actor's live output stream and, when its policy says the moment has come, writes
one short user message into a sink that reaches the actor's stdin — the channel
decided by ADR-0013 (``docs/decisions/``).

Three properties are structural rather than advisory, and each has a test:

- **The policy sees the actor and its guidebook.** :class:`Observation` carries
  the actor's own records, the task, and the complete phase-B guidebook when a
  guided workflow supplies one. The shared criterion remains beside it as the
  standard for general engineering practice; the guidebook supplies the
  instance-specific route.
- **Speech has a shallow mechanical floor.** Corrections are non-empty and at
  most 400 characters; the policy also rejects fenced code, diff hunks
  and eight-word copying from the guidebook. These checks do not establish that
  a semantic paraphrase is safe. `supervisor.jsonl` keeps the guidebook
  identity, judge request/reason and emitted text so a person can audit that
  judgement.
- **When to speak is a seam.** It is the open variable of the design, so a
    :class:`SpeakPolicy` is replaceable without touching the consumer, the
    intervention, or the log.
- **The sink is borrowed, never owned.** The CLI exits when its stdin reaches
  EOF, so closing the sink *is* the termination mechanism and belongs to
  whoever owns the process. This component only writes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import dataclasses
import datetime
import hashlib
import json
import re
from typing import Any, Protocol

from swe_lab.conversation import Message, Role, TextBlock, ToolResultBlock
from swe_lab.trace_synthesis.context_components import (
    CompleteAssistantTurnSelector,
    EvidenceSelector,
    INITIAL_RUNNING_STATE,
)
from swe_lab.trace_synthesis.criterion import (
    Criterion,
    CRITERION_SHA256,
    CriterionRejectedError,
    shingles,
)

# The cap is the enforceable part of the intervention's shape. "Short,
# directional, not a solution" is read by a human and deliberately not asserted
# here: a predicate that can check it forces the correction to name a concrete
# action, which is most of the way to handing over the answer.
MAX_INTERVENTION_CHARS = 400

# The provenance marker: what makes an intervention identifiable as external,
# so the actor can mistake it neither for its own output nor for a tool's.
INTERVENTION_TAG = "supervisor_note"

#: The log row for a boundary the supervisor could not cover **and cannot bound
#: the reach of** — the sink failed, or the policy broke in a way it did not
#: name. Named because it is read outside this module: a gap means the actor
#: passed that boundary unsupervised and nothing is known about the boundaries
#: after it, so the run stops being evidence about supervision at all.
LOG_KIND_GAP = "gap"

#: The log row for a *bounded* failure: this one boundary went unsupervised, for
#: the reason recorded, and the policy asserted its own state survived. What it
#: proves is a named hole — which boundary, and why — so the run stays evidence
#: carrying that fact. What it does not prove is that the actor did anything at
#: that boundary, or that the next one was covered: a later lapse says
#: otherwise, and the count of them is the reading.
LOG_KIND_LAPSE = "lapse"

#: The log row for a correction that was delivered. Named for the same reason:
#: a consumer counts these to say what the run's supervision cost and did.
LOG_KIND_SPOKE = "spoke"

#: The log row for a boundary the policy **decided** to stay quiet at. It is a
#: judgement: the policy looked at this boundary and had nothing to say.
LOG_KIND_SILENT = "silent"

#: The log row for a boundary where **no decision was taken at all**, for a
#: reason the policy names (:class:`Unjudged`). Separate from
#: :data:`LOG_KIND_SILENT` on purpose: one row meaning both "judged, nothing
#: was wrong" and "nothing was judged" cannot be read either way, and a
#: consumer counting silences as coverage would over-count what the run's
#: supervision actually looked at. Not a failure either — a lapse is a
#: boundary that should have been covered and was not, this is one there was
#: nothing to cover at.
LOG_KIND_UNJUDGED = "unjudged"

#: Where a correction is written. A borrowed callable — see the module note.
Sink = Callable[[str], None]

#: Where the account of the run is written, one JSON object per call.
LogWriter = Callable[[Mapping[str, Any]], None]


class InterventionTooLongError(ValueError):
  """Raised when a policy produces text over :data:`MAX_INTERVENTION_CHARS`."""


class WriterOutputRejectedError(ValueError):
  """Raised when writer output matches a mechanically blocked shape."""


_FENCED_CODE = re.compile(r"^[ \t]*(?:>[ \t]*)*(?:```|~~~)", re.MULTILINE)
_DIFF_HUNK = re.compile(r"^@{2,}(?:[ \t]|[-+])", re.MULTILINE)


def _check_writer_output(text: str, guidebook: str | None) -> None:
  """Reject shallow answer-like forms without claiming semantic safety."""
  if _FENCED_CODE.search(text):
    raise WriterOutputRejectedError(
        "writer output contains a fenced code block"
    )
  if _DIFF_HUNK.search(text):
    raise WriterOutputRejectedError("writer output contains a diff hunk header")
  if guidebook is not None and shingles(text) & shingles(guidebook):
    raise WriterOutputRejectedError(
        "writer output copies an eight-word guidebook shingle"
    )


class PolicyLapseError(Exception):
  """A policy failure the policy itself bounds to the boundary it happened at.

  Raising this is an assertion about the *policy*, not about the actor: this
  call could not produce a decision, and the policy's own state is intact, so
  the next boundary will be judged normally.

  **Scope is declared, never inferred.** Only the policy knows which of its
  failures it can bound, so the supervisor reads the declaration and does not
  classify on its behalf — an exception that does not carry it is unbounded by
  definition, and the run is excluded. That default is the honest one: silence
  about scope is not a claim of a small one.

  Attributes:
    finish_reason: The model call's ``finish_reason`` when the lapse traces to
      one, carried over from a :class:`~swe_lab.trace_synthesis.judge.
      JudgeAnswerError`'s own attribute of the same name. ``"length"`` means
      the token budget ran out before an answer could be produced or parsed —
      a configuration problem — while anything else means the model finished
      and the answer was still unusable — a judgment-quality problem.
      ``None`` when the lapse did not come from a judge answer at all (a
      transport error, or the writer's line being empty or too long). Folding
      both causes into one undifferentiated lapse is exactly what made every
      lapse in a 902-call replay look the same until someone read the raw
      calls by hand (issue #383); this field is how ``supervisor.jsonl`` tells
      them apart without a rerun.
  """

  finish_reason: str | None

  def __init__(self, message: str, *, finish_reason: str | None = None) -> None:
    """Record the lapse together with the finish reason behind it, if any.

    Args:
      message: What went wrong.
      finish_reason: See the class attribute.
    """
    super().__init__(message)
    self.finish_reason = finish_reason


@dataclasses.dataclass(frozen=True)
class Intervention:
  """One thing the supervisor says, bounded and attributable.

  Attributes:
    text: What to say. Rejected rather than truncated when over the cap, so
      that a policy cannot silently ship half a sentence.
  """

  text: str

  def __post_init__(self) -> None:
    """Enforce the bounds that are enforceable.

    Raises:
      ValueError: The text is empty or blank.
      InterventionTooLongError: The text is over :data:`MAX_INTERVENTION_CHARS`.
    """
    if not self.text.strip():
      raise ValueError("an intervention may not be empty")
    if len(self.text) > MAX_INTERVENTION_CHARS:
      raise InterventionTooLongError(
          f"{len(self.text)} chars > {MAX_INTERVENTION_CHARS}"
      )

  def rendered(self) -> str:
    """Return the message as the actor receives it, tagged.

    Returns:
      The text wrapped in :data:`INTERVENTION_TAG`, so the actor can tell it
      apart from its own output and from a tool's.
    """
    return f"<{INTERVENTION_TAG}>\n{self.text}\n</{INTERVENTION_TAG}>"


@dataclasses.dataclass(frozen=True)
class Unjudged:
  """No decision was taken at this boundary, and why.

  Returned instead of ``None`` so the two can be told apart downstream:
  ``None`` is a judgement that came out silent, and this is the absence of a
  judgement. The supervisor records it as :data:`LOG_KIND_UNJUDGED` with the
  reason, never as a silence.

  Attributes:
    reason: Why no decision was taken, in the policy's own words.
  """

  reason: str


@dataclasses.dataclass(frozen=True)
class Observation:
  """Everything a policy is allowed to see.

  A guidebook-guided workflow validates and supplies the complete phase-B
  artifact here so the judge and writer can steer toward its instance-specific
  route. Workflows without one leave :attr:`guidebook` unset and retain the
  general-practice criterion alone. Raw gold/reference/test patches and hidden
  tests have no separate field.

  Attributes:
    task: What the actor was asked to do, handed over at construction by
      whoever wrote the prompt. The barrier keeps out the solution, not the
      goal: a supervisor blind to what was asked cannot tell deviation from
      progress, and can only object to style.
    evidence: What the actor produced, in order — its assistant messages and
      the results of its own tool calls.
    cursor: How many stream events have been consumed, including those that
      carried no message. Identifies where a decision was taken.
    said: What this supervisor has already said in this run — its **memory**,
      a separate channel from its evidence. Its own words never come back as
      observations, so without this a policy has nothing to check against and
      can repeat itself indefinitely.
    guidebook: The complete phase-B guidebook for this instance, or ``None``
      for a workflow that uses only the shared criterion.
    running_state: The last valid bounded observational state before the
      selected evidence. The standard policy owns and replaces this value.
  """

  task: str
  evidence: tuple[Message, ...]
  cursor: int
  said: tuple[Intervention, ...]
  guidebook: str | None = None
  running_state: str = INITIAL_RUNNING_STATE


class SpeakPolicy(Protocol):
  """Decides whether and when to speak — never what channel, never the run.

  A policy that cannot decide says how far the failure reaches, because only it
  knows: :class:`PolicyLapseError` out of ``consider`` bounds it to that one
  boundary, and any other exception leaves the reach unstated and excludes the
  run.
  """

  @property
  def name(self) -> str:
    """Return the policy's name, recorded on every decision.

    Returns:
      A short stable identifier.
    """
    ...

  def consider(
      self, observation: Observation
  ) -> Intervention | Unjudged | None:
    """Decide whether to speak at this point.

    Args:
      observation: What the actor has produced so far, and the criterion.

    Returns:
      What to say, ``None`` to stay silent, or :class:`Unjudged` when the
      policy took no decision here at all. Silence is the ordinary case and is
      not an error; so is declining to decide, for a reason the policy names.
    """
    ...


@dataclasses.dataclass(frozen=True)
class NeverSpeak:
  """A policy that consults nothing and never speaks.

  For plumbing: it exercises the channel, the pump and the record without a
  model behind them. It is **not** the paired control — that arm is
  `SpeakWhenOffTrack` with a budget of zero, which judges every boundary it
  has evidence for and has nothing left to spend. Why it has to be that one and
  not this one is stated once, at
  :data:`swe_lab.workflow.definitions.CONTROL_BUDGET`.
  """

  @property
  def name(self) -> str:
    """Return the policy's name.

    Returns:
      ``"never-speak"``.
    """
    return "never-speak"

  def consider(self, observation: Observation) -> Intervention | None:
    """Stay silent.

    Args:
      observation: Ignored.

    Returns:
      ``None``, always.
    """
    del observation
    return None


@dataclasses.dataclass(frozen=True)
class Verdict:
  """One judge call's answer: two questions, not one.

  Attributes:
    off_track: Whether the actor has left the criterion's path.
    self_correcting: Whether, left alone, it would come back by itself.
      Recorded but never acted on. It remains in the verdict so telemetry can
      measure how often it would have vetoed an off-track judgement before a
      later breaking change decides whether to remove it from the contract.
    reason: The judge's own words, recorded but never acted on.
    running_state: The bounded observational state after the evidence this
      verdict judged. The standard model judge requires it; the default keeps
      lightweight custom judges source-compatible.
    deviation_started_steps_ago: How many of the shown steps ago the judge
      believes the deviation began, or ``None`` when it was not asked — which
      is the default, and every A′ run. **Never acted on**, exactly like
      ``reason``: it exists so a segmented run can record how many turns late
      its correction was, which is the only evidence a choice of segment length
      could ever rest on.

      **The unit is a rendered step, not a turn**, and the two differ: one turn
      emits several stream events (59 events for 32 turns on the first
      end-to-end capture) and the judge sees one line per admitted record.
      Converting here would manufacture a precise-looking number out of an
      estimate, so the raw answer is carried and the reader is told what it
      counts.
    judge_input: The exact credential-free model request behind the verdict,
      when the judge exposes one for the host-side audit log.
  """

  off_track: bool
  self_correcting: bool
  reason: str = ""
  running_state: str = INITIAL_RUNNING_STATE
  deviation_started_steps_ago: int | None = None
  judge_input: Mapping[str, Any] | None = None


@dataclasses.dataclass(frozen=True, kw_only=True)
class WriterObservation(Observation):
  """The bounded observation plus the verdict the writer must explain.

  A subtype keeps the established two-argument writer and prompt-builder
  contracts intact. Custom writers that need only the observation continue to
  accept it, while the standard writer can include the structured decision.

  Attributes:
    verdict: The valid judgement for this same selected evidence.
  """

  verdict: Verdict


class Judge(Protocol):
  """Decides whether the actor is off track and whether it will recover."""

  def __call__(self, observation: Observation, criterion: Criterion) -> Verdict:
    """Judge one moment against the criterion.

    Args:
      observation: The evidence window and the task.
      criterion: The standard to measure against, passed explicitly on every
        call. Whether an implementation builds its prompt from this argument
        rather than an embedded standard is that implementation's invariant,
        with its own named test; a protocol cannot compel a parameter's use.

    Returns:
      The verdict for this moment.
    """
    ...


class Writer(Protocol):
  """Turns a decision to speak into the line the actor receives."""

  def __call__(self, observation: Observation, criterion: Criterion) -> str:
    """Write one short, hedged, directional line.

    Args:
      observation: The same observation the judge saw.
      criterion: The same criterion the judge was handed, passed explicitly for
        the same reason: a closure carrying its own standard is a side door,
        and one that no signature shows.

    Returns:
      The text of the correction.
    """
    ...


@dataclasses.dataclass(frozen=True)
class WouldHaveSpoken:
  """A deviation the judge found, recorded whether or not speech followed.

  This is what the control arm produces: ``SpeakWhenOffTrack(budget=0)`` judges
  every boundary it has evidence for and speaks at none, so its markers are the
  points at which the treatment arm would have intervened. What that buys a
  comparison is stated once, at
  :data:`swe_lab.workflow.definitions.CONTROL_BUDGET`.

  Attributes:
    cursor: Where the deviation was found.
    reason: The judge's stated reason.
    deviation_started_steps_ago: Where the judge believes it *began*, in the
      unit :class:`Verdict` defines — ``None`` unless the judge was asked. Two
      different quantities: this record is written where a deviation was
      noticed, and a supervisor that only knows that cannot say how late it
      was.
    judge_input: The exact credential-free model request behind the judgement,
      when available.
  """

  cursor: int
  reason: str
  deviation_started_steps_ago: int | None = None
  judge_input: Mapping[str, Any] | None = None


@dataclasses.dataclass(frozen=True)
class SpeakAt:
  """Speaks a fixed line at fixed cursors, with no judge at all.

  The timing knob in isolation: it varies *when* while holding *what* and
  *whether* constant. A policy whose trigger is entangled with its criterion
  cannot isolate timing at all — the two move together, so no comparison
  between its arms can attribute a difference to either one.

  Attributes:
    cursors: The cursor values at which to speak.
    text: The line, identical at every one of them.
  """

  cursors: frozenset[int]
  text: str

  @property
  def name(self) -> str:
    """Return the policy's name.

    Returns:
      ``"speak-at"``.
    """
    return "speak-at"

  def consider(self, observation: Observation) -> Intervention | None:
    """Speak if this cursor is one of the fixed points.

    Args:
      observation: Read only for its cursor.

    Returns:
      The fixed line, or ``None``.
    """
    if observation.cursor not in self.cursors:
      return None
    return Intervention(text=self.text)


@dataclasses.dataclass
class SpeakWhenOffTrack:
  """Judges what it has evidence for; speaks when off track and affordable.

  **A boundary with no evidence is not judged at all.** Before the gates
  below, an empty evidence window returns :class:`Unjudged`: there is nothing
  for the judge to measure against the criterion, so asking it yields an answer
  about a record it was never shown. The window is empty only until the actor's
  first message, so this covers the head of a run and nothing else — which is
  where it was observed to matter, on the first end-to-end run (the replay is
  in task 05 §4.3). It is a statement about *zero* evidence and nothing wider:
  a window holding few records is judged exactly as before.

  **The budget gates speech, not judgement.** Past that precondition,
  ``consider`` returns ``None`` unless every gate passes, in this order:

  1. the judge says off track, else silent;
  2. the would-have-spoken marker is recorded — *before* any budget is
     consulted;
  3. budget remaining, else silent;
  4. cooldown elapsed since the last intervention, else silent;
  5. the writer produces a usable line, else the failure is bounded to this
     boundary and recorded as a lapse. Never a retry.

  The cost of that order is stated rather than hidden: the judge runs on every
  boundary carrying evidence even after the budget is spent, so a ``budget=0``
  policy still pays for a judge it can never act on. The precondition above
  does not touch that matching — it depends on the evidence window alone, so
  two arms fed the same stream skip the same boundaries. Why that cost is worth
  paying — what the two supervised definitions are and are not matched on — is
  stated once, at :data:`swe_lab.workflow.definitions.CONTROL_BUDGET`.

  The criterion is a constructor argument rather than a field on
  :class:`Observation`, so it never travels the channel the actor's records
  travel. Two things are enforced here and no more: construction **rejects** a
  criterion whose digest is not
  :data:`~swe_lab.trace_synthesis.criterion.CRITERION_SHA256`, and ``consider``
  **passes** it to the judge on every call, so it is carried rather than stored
  beside one. What a judge then measures against is the judge's own invariant.

  Attributes:
    judge: The off-track / self-correcting call.
    writer: The line-writing call.
    criterion: The loaded, digest-checked standard the judge measures against.
    budget: How many interventions a whole run may carry. **No default**: a
      policy that may speak must state how often. No measured value; see the
      task-05 plan.
    cooldown: How many boundaries must pass *between* interventions. It never
      delays the first one: precision comes from the bar and restraint from the
      budget, so a late correction is never bought with a later one. No
      measured value.
    window: How many complete recent assistant turns the judge sees. No
      measured value.
    selector: How the evidence window is selected without splitting a turn.
  """

  judge: Judge
  writer: Writer
  criterion: Criterion
  budget: int
  cooldown: int = 4
  window: int = 8
  selector: EvidenceSelector = dataclasses.field(
      default_factory=CompleteAssistantTurnSelector
  )

  _markers: list[WouldHaveSpoken] = dataclasses.field(default_factory=list)
  _spoken_at: list[int] = dataclasses.field(default_factory=list)
  _verdicts: list[Verdict] = dataclasses.field(default_factory=list)
  _running_state: str = INITIAL_RUNNING_STATE

  def __post_init__(self) -> None:
    """Refuse a criterion that is not the pinned one.

    Raises:
      CriterionRejectedError: The criterion's digest is not
        :data:`~swe_lab.trace_synthesis.criterion.CRITERION_SHA256`.
    """
    if self.criterion.digest != CRITERION_SHA256:
      raise CriterionRejectedError(
          f"policy criterion {self.criterion.digest} is not the pinned"
          f" {CRITERION_SHA256}"
      )

  @property
  def name(self) -> str:
    """Return the policy's name.

    Returns:
      ``"speak-when-off-track"``.
    """
    return "speak-when-off-track"

  @property
  def markers(self) -> tuple[WouldHaveSpoken, ...]:
    """Return every deviation found, spoken or not.

    Returns:
      The markers in the order they were recorded. A non-zero count on a
      ``budget=0`` run is what proves the judge still ran.
    """
    return tuple(self._markers)

  @property
  def verdicts(self) -> tuple[Verdict, ...]:
    """Return every valid judgement, including silent ones.

    Returns:
      The verdicts in the order they were produced.
    """
    return tuple(self._verdicts)

  @property
  def running_state(self) -> str:
    """Return the latest valid running state."""
    return self._running_state

  def consider(
      self, observation: Observation
  ) -> Intervention | Unjudged | None:
    """Decide whether to speak at this boundary.

    Args:
      observation: The actor's records so far, the task and the criterion.

    Both calls out to a model are bounded to this boundary: any ``Exception``
    they raise — an upstream error, an unparseable answer, or a line
    :class:`Intervention` rejects as empty or over the cap — becomes a
    :class:`PolicyLapseError`, and the supervisor records a lapse. When the
    judge's failure carries a ``finish_reason`` (see
    :class:`~swe_lab.trace_synthesis.judge.JudgeAnswerError`), it travels onto
    the :class:`PolicyLapseError` unchanged, so the record can tell a
    token-budget lapse from an unparseable one. A
    ``BaseException`` is not caught: an interrupt is not this policy's to
    reinterpret as a small hole. The bound
    comes from *where* the failure happened rather than from what was raised: a
    judge call fails before this method has touched its own state, and a writer
    call fails after the deviation is already recorded and before any budget is
    spent. Neither is retried — retrying would make what the actor hears a
    function of how many times we asked — and neither is a reason to disbelieve
    the next boundary.

    Anything raised outside those two calls is this policy's own state machine
    breaking, which it cannot bound and therefore does not: it propagates
    unclassified and the supervisor records a gap.

    Returns:
      What to say, ``None``, or :class:`Unjudged` when the evidence window was
      empty and the judge was therefore not consulted. Silence is the ordinary
      case, and is a judgement; the third answer is the absence of one.

    Raises:
      PolicyLapseError: A model call failed, or produced a line
        :class:`Intervention` refused.
    """
    windowed = dataclasses.replace(
        observation,
        evidence=self.selector.select(observation.evidence, limit=self.window),
        running_state=self._running_state,
    )
    if not windowed.evidence:
      return Unjudged(reason="no actor evidence in the window")
    try:
      verdict = self.judge(windowed, self.criterion)
    except Exception as error:  # noqa: BLE001 - re-raised with its scope named
      raise PolicyLapseError(
          f"judge call failed: {error!r}",
          finish_reason=getattr(error, "finish_reason", None),
      ) from error
    self._verdicts.append(verdict)
    self._running_state = verdict.running_state
    if not verdict.off_track:
      return None

    self._markers.append(
        WouldHaveSpoken(
            cursor=observation.cursor,
            reason=verdict.reason,
            deviation_started_steps_ago=verdict.deviation_started_steps_ago,
            judge_input=verdict.judge_input,
        )
    )

    if len(self._spoken_at) >= self.budget:
      return None
    if self._spoken_at and observation.cursor - self._spoken_at[-1] < (
        self.cooldown
    ):
      return None

    try:
      writer_observation = WriterObservation(
          task=windowed.task,
          evidence=windowed.evidence,
          cursor=windowed.cursor,
          said=windowed.said,
          guidebook=windowed.guidebook,
          running_state=windowed.running_state,
          verdict=verdict,
      )
      text = self.writer(writer_observation, self.criterion)
      _check_writer_output(text, observation.guidebook)
      intervention = Intervention(text=text)
    except Exception as error:  # noqa: BLE001 - re-raised with its scope named
      unusable = f"writer produced no usable line: {error!r}"
      raise PolicyLapseError(unusable) from error
    self._spoken_at.append(observation.cursor)
    return intervention


# How a message was dispositioned, recorded so the account of a run says why
# something was not judged rather than leaving it missing.
ADMITTED_ASSISTANT = "assistant"
ADMITTED_TOOL_RESULT = "tool-result"
EXCLUDED_OWN_INTERVENTION = "excluded-own-intervention"
EXCLUDED_EXTERNAL_TEXT = "excluded-external-text"
EXCLUDED_NOTHING_TO_KEEP = "excluded-nothing-to-keep"


@dataclasses.dataclass
class EvidenceFilter:
  """Decides what reaches the supervisor — by **origin**, not by role.

  The barrier keeps out the solution, not the goal. The goal does not travel
  this path at all: the task statement is handed to the supervisor at
  construction, by whoever wrote the prompt, so it needs no rule here and
  cannot be confused with anything else on the stream.

  What this filter admits is therefore exactly what the *actor* produced — its
  assistant messages and the results of its own tool calls. Every user text is
  excluded, distinguished only so the record can say which kind it was: text
  carrying the intervention tag came from this supervisor, and anything else is
  an outside interjection. Neither is an observation of what the actor did.

  Stateless by construction: a supervisor attached mid-run reaches the same
  verdict on a message as one that watched from the first event.
  """

  def admit(self, message: Message | None) -> tuple[Message | None, str]:
    """Decide whether one message becomes evidence.

    Args:
      message: A converted stream message, or ``None``.

    Returns:
      The record to keep (or ``None``), and the disposition that says why.
    """
    if message is None:
      return None, EXCLUDED_NOTHING_TO_KEEP

    if message.role == Role.ASSISTANT:
      return message, ADMITTED_ASSISTANT

    results = [b for b in message.content if isinstance(b, ToolResultBlock)]
    if results:
      return Message(role=message.role, content=list(results)), (
          ADMITTED_TOOL_RESULT
      )

    text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
    if not text:
      return None, EXCLUDED_NOTHING_TO_KEEP
    if f"<{INTERVENTION_TAG}>" in text:
      return None, EXCLUDED_OWN_INTERVENTION
    return None, EXCLUDED_EXTERNAL_TEXT


@dataclasses.dataclass
class Supervisor:
  """Consumes the actor's stream, consults a policy, writes what it decides.

  Attributes:
    policy: When to speak.
    task: What the actor was asked to do; see :class:`Observation`.
    sink: Where a correction is written. Borrowed: never closed here.
    log: Where the account of the run is written, one row per event consumed.
    guidebook: The phase-B artifact, when this is a guidebook-guided run.
    now: Clock, injected so the log is testable.
  """

  policy: SpeakPolicy
  task: str
  sink: Sink
  log: LogWriter
  guidebook: str | None = None
  now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
      datetime.UTC
  )

  _evidence: list[Message] = dataclasses.field(default_factory=list)
  _said: list[Intervention] = dataclasses.field(default_factory=list)
  _filter: EvidenceFilter = dataclasses.field(default_factory=EvidenceFilter)
  _cursor: int = 0
  _mute: bool = False
  _disposition: str = EXCLUDED_NOTHING_TO_KEEP

  def observe(self, event: Mapping[str, Any]) -> Intervention | None:
    """Consume one stream event and act on it.

    Every call writes exactly one log row, so the account of a run has no
    silent gaps: a judgement, a silence, a boundary the policy took no decision
    at, a lapse the policy bounded to this one boundary, or a gap of unknown
    reach.

    Args:
      event: One decoded ``stream-json`` event.

    Returns:
      What was said at this event, or ``None``.
    """
    # Imported here, not at module scope: the `claude_code` package's
    # ``__init__`` imports its harness, and the harness takes a
    # ``SegmentedSupervision`` from this package — so a module-level import
    # closes a cycle whenever a trace-synthesis module is imported first. The
    # same reasoning `vocabulary.py`'s docstring gives for existing at all.
    from swe_lab.harnesses.claude_code.convert import event_to_message

    self._cursor += 1
    record, self._disposition = self._filter.admit(event_to_message(event))
    if record is not None:
      self._evidence.append(record)

    observation = Observation(
        task=self.task,
        evidence=tuple(self._evidence),
        cursor=self._cursor,
        said=tuple(self._said),
        guidebook=self.guidebook,
    )
    verdict_count = (
        len(self.policy.verdicts)
        if isinstance(self.policy, SpeakWhenOffTrack)
        else 0
    )
    try:
      decision = self.policy.consider(observation)
    except PolicyLapseError as error:
      # The policy bounded this one; the run keeps its evidence value and the
      # next boundary is judged normally. finish_reason distinguishes a
      # token-budget lapse ("length") from an unparseable-answer one (any
      # other value, or None when the lapse was not a judge answer at all) —
      # see PolicyLapseError.
      self._row(
          LOG_KIND_LAPSE,
          reason=f"policy lapsed: {error!r}",
          finish_reason=error.finish_reason,
          **self._verdict_audit_after(verdict_count),
      )
      return None
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      self._row(
          LOG_KIND_GAP,
          reason=f"policy raised: {error!r}",
          **self._verdict_audit_after(verdict_count),
      )
      return None

    if isinstance(decision, Unjudged):
      # Not a silence: nothing was judged here, and the reason says what the
      # policy had instead of a decision.
      self._row(LOG_KIND_UNJUDGED, reason=decision.reason)
      return None
    if decision is None:
      self._row(
          LOG_KIND_SILENT,
          **self._verdict_audit_after(verdict_count, decision=True),
      )
      return None
    intervention = decision
    if self._mute:
      self._row(
          LOG_KIND_GAP,
          reason="sink unusable; not attempted",
          text=intervention.text,
          **self._verdict_audit_after(verdict_count),
      )
      return None

    try:
      self.sink(intervention.rendered())
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      # The channel is gone, but the run is not ours to end: stop speaking and
      # keep accounting for every later event.
      self._mute = True
      self._row(
          LOG_KIND_GAP,
          reason=f"sink raised: {error!r}",
          text=intervention.text,
          **self._verdict_audit_after(verdict_count),
      )
      return None

    self._said.append(intervention)
    self._row(
        LOG_KIND_SPOKE,
        text=intervention.text,
        **self._verdict_audit_after(verdict_count, decision=True),
    )
    return intervention

  def _verdict_audit_after(
      self, count: int, *, decision: bool = False
  ) -> dict[str, object]:
    """Return audit fields for the valid verdict created by this decision."""
    if not isinstance(self.policy, SpeakWhenOffTrack):
      return {}
    verdicts = self.policy.verdicts
    if len(verdicts) <= count:
      return {}
    verdict = verdicts[-1]
    audit: dict[str, object] = {
        "judge_input": verdict.judge_input,
        "judge_reason": verdict.reason,
        "off_track": verdict.off_track,
        "self_correcting": verdict.self_correcting,
        "running_state": verdict.running_state,
    }
    if decision:
      audit["reason"] = verdict.reason
    return audit

  def _row(self, kind: str, **extra: object) -> None:
    """Write one row of the run's account.

    Args:
      kind: ``"spoke"``, ``"silent"``, ``"unjudged"``, ``"lapse"`` or
        ``"gap"``.
      **extra: Fields specific to the kind.
    """
    self.log(
        {
            "cursor": self._cursor,
            "at": self.now().isoformat(),
            "policy": self.policy.name,
            "kind": kind,
            "evidence": self._disposition,
            "guidebook_sha256": (
                hashlib.sha256(self.guidebook.encode()).hexdigest()
                if self.guidebook is not None
                else None
            ),
            **extra,
        }
    )


def jsonl_writer(path: Any) -> LogWriter:
  """Return a writer appending one JSON object per line to ``path``.

  Args:
    path: An open-able path.

  Returns:
    A :data:`LogWriter`.
  """

  def write(row: Mapping[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
      _ = handle.write(json.dumps(row) + "\n")

  return write


def evidence_of(events: Sequence[Mapping[str, Any]]) -> tuple[Message, ...]:
  """Build the evidence a supervisor would have seen over a whole stream.

  Args:
    events: Decoded ``stream-json`` events, in order.

  Returns:
    The messages a supervisor would have seen.
  """
  # Function-local for the reason given in ``Supervisor.observe``.
  from swe_lab.harnesses.claude_code.convert import event_to_message

  evidence_filter = EvidenceFilter()
  kept = [evidence_filter.admit(event_to_message(e))[0] for e in events]
  return tuple(m for m in kept if m is not None)
