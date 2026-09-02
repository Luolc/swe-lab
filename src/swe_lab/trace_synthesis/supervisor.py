"""The supervisor: what it may see, when it speaks, what it may say.

The component behind task 05 (``docs/trace-synthesis/plans/``). It consumes the
actor's live output stream and, when its policy says the moment has come, writes
one short user message into a sink that reaches the actor's stdin — the channel
decided by ADR-0013 (``docs/decisions/``).

Three properties are structural rather than advisory, and each has a test:

- **The information barrier is this module's constructor.** :class:`Observation`
  has no field that can carry the gold patch, the reference or test patch, or
  the hidden tests, and its evidence is built only from records the actor
  produced. There is no field for a phase-B guidebook either, and that absence
  is deliberate: the Oracle writes a guidebook with the reference patch, the
  grading procedure and the unpurged history in hand, so a supervisor reading
  one would be steering by the answer without ever quoting it. The judge
  measures against one pinned criterion instead, so every load yields the same
  text and a per-instance criterion cannot be swapped in without the loader
  rejecting it — and whether that shared text is free of solution knowledge is
  left to review of the artifact itself
  (:mod:`swe_lab.trace_synthesis.criterion`).
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
import json
from typing import Any, Protocol

from swe_lab.conversation import Message, Role, TextBlock, ToolResultBlock
from swe_lab.harnesses.claude_code.convert import event_to_message
from swe_lab.trace_synthesis.criterion import (
    Criterion,
    CRITERION_SHA256,
    CriterionRejectedError,
)

# The cap is the enforceable part of the intervention's shape. "Short,
# directional, not a solution" is read by a human and deliberately not asserted
# here: a predicate that can check it forces the correction to name a concrete
# action, which is most of the way to handing over the answer.
MAX_INTERVENTION_CHARS = 400

# The provenance marker: what makes an intervention identifiable as external,
# so the actor can mistake it neither for its own output nor for a tool's.
INTERVENTION_TAG = "supervisor_note"

#: The log row for a boundary the supervisor could not judge or could not speak
#: at — a policy that raised, or a sink that did. Named because it is read
#: outside this module: a gap means the actor passed that boundary
#: **unsupervised**, which is not the same fact as a deliberate silence even
#: though both leave the actor untouched.
LOG_KIND_GAP = "gap"

#: Where a correction is written. A borrowed callable — see the module note.
Sink = Callable[[str], None]

#: Where the account of the run is written, one JSON object per call.
LogWriter = Callable[[Mapping[str, Any]], None]


class InterventionTooLongError(ValueError):
  """Raised when a policy produces text over :data:`MAX_INTERVENTION_CHARS`."""


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
class Observation:
  """Everything a policy is allowed to see.

  The field list *is* the information barrier: there is no field for the gold
  patch, the reference or test patch, the hidden tests, or a **phase-B
  guidebook** — the Oracle writes that one with the reference patch, the exact
  grading procedure and the repository's unpurged history in hand
  (:mod:`swe_lab.trace_synthesis.oracle`), so handing it to a judge would walk
  the answer through a second door. What the judge measures against is the
  pinned criterion it is built with, not anything travelling this channel. And
  ``test_supervisor_input_carries_no_privileged_field`` asserts the list against
  an exact allowlist so that adding one fails.

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
  """

  task: str
  evidence: tuple[Message, ...]
  cursor: int
  said: tuple[Intervention, ...]


class SpeakPolicy(Protocol):
  """Decides whether and when to speak — never what channel, never the run."""

  @property
  def name(self) -> str:
    """Return the policy's name, recorded on every decision.

    Returns:
      A short stable identifier.
    """
    ...

  def consider(self, observation: Observation) -> Intervention | None:
    """Decide whether to speak at this point.

    Args:
      observation: What the actor has produced so far, and the criterion.

    Returns:
      What to say, or ``None`` to stay silent. Silence is the ordinary case
      and is not an error.
    """
    ...


@dataclasses.dataclass(frozen=True)
class NeverSpeak:
  """A policy that never speaks.

  Not padding: the control arm has to run *the same* supervisor with speech
  disabled, or the arms differ by the judge calls, their latency and their cost
  as well as by the corrections, and the comparison stops being paired.
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
    self_correcting: Whether, left alone, it would come back by itself. This
      is where the restraint lives — an actor that has just said "that didn't
      work, let me reconsider" is already doing what an intervention would ask
      for.
    reason: The judge's own words, recorded but never acted on.
  """

  off_track: bool
  self_correcting: bool
  reason: str = ""


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

  This is what the control arm produces. ``SpeakWhenOffTrack(budget=0)`` judges
  every boundary and speaks at none, so its markers are the points at which the
  treatment arm would have intervened — which is what lets the two arms be
  compared at matched deviation points rather than only at their endpoints.

  Attributes:
    cursor: Where the deviation was found.
    reason: The judge's stated reason.
  """

  cursor: int
  reason: str


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
  """Judges every boundary; speaks when off track, unrecovering and affordable.

  **The budget gates speech, not judgement.** ``consider`` returns ``None``
  unless every gate passes, in this order:

  1. the judge says off track, else silent;
  2. the judge says it will not self-correct, else silent;
  3. the would-have-spoken marker is recorded — *before* any budget is
     consulted;
  4. budget remaining, else silent;
  5. cooldown elapsed since the last intervention, else silent;
  6. the writer produces a usable line, else the failure propagates and the
     supervisor records a gap. Never a retry.

  The cost of that order is stated rather than hidden: the judge runs on every
  boundary even after the budget is spent, so a treatment run and a control run
  pay the same judge calls. That is what makes the two arms differ by the
  corrections alone.

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
    window: How many of the actor's records the judge sees. No measured value.
  """

  judge: Judge
  writer: Writer
  criterion: Criterion
  budget: int
  cooldown: int = 4
  window: int = 8

  _markers: list[WouldHaveSpoken] = dataclasses.field(default_factory=list)
  _spoken_at: list[int] = dataclasses.field(default_factory=list)

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

  def consider(self, observation: Observation) -> Intervention | None:
    """Decide whether to speak at this boundary.

    Args:
      observation: The actor's records so far, the task and the criterion.

    An unusable line from the writer — empty, or over the cap — is rejected by
    :class:`Intervention` and propagates to the supervisor, which records the
    gap. It is deliberately not caught here: retrying would make what the actor
    hears a function of how many times we asked.

    Returns:
      What to say, or ``None``. Silence is the ordinary case.
    """
    windowed = dataclasses.replace(
        observation, evidence=observation.evidence[-self.window :]
    )
    verdict = self.judge(windowed, self.criterion)
    if not verdict.off_track or verdict.self_correcting:
      return None

    self._markers.append(
        WouldHaveSpoken(cursor=observation.cursor, reason=verdict.reason)
    )

    if len(self._spoken_at) >= self.budget:
      return None
    if self._spoken_at and observation.cursor - self._spoken_at[-1] < (
        self.cooldown
    ):
      return None

    intervention = Intervention(text=self.writer(windowed, self.criterion))
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
    now: Clock, injected so the log is testable.
  """

  policy: SpeakPolicy
  task: str
  sink: Sink
  log: LogWriter
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
    silent gaps: a judgement, a silence, or an explicit gap where the policy or
    the sink failed.

    Args:
      event: One decoded ``stream-json`` event.

    Returns:
      What was said at this event, or ``None``.
    """
    self._cursor += 1
    record, self._disposition = self._filter.admit(event_to_message(event))
    if record is not None:
      self._evidence.append(record)

    observation = Observation(
        task=self.task,
        evidence=tuple(self._evidence),
        cursor=self._cursor,
        said=tuple(self._said),
    )
    try:
      intervention = self.policy.consider(observation)
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      self._row(LOG_KIND_GAP, reason=f"policy raised: {error!r}")
      return None

    if intervention is None:
      self._row("silent")
      return None
    if self._mute:
      self._row(
          LOG_KIND_GAP,
          reason="sink unusable; not attempted",
          text=intervention.text,
      )
      return None

    try:
      self.sink(intervention.rendered())
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      # The channel is gone, but the run is not ours to end: stop speaking and
      # keep accounting for every later event.
      self._mute = True
      self._row(
          LOG_KIND_GAP, reason=f"sink raised: {error!r}", text=intervention.text
      )
      return None

    self._said.append(intervention)
    self._row("spoke", text=intervention.text)
    return intervention

  def _row(self, kind: str, **extra: object) -> None:
    """Write one row of the run's account.

    Args:
      kind: ``"spoke"``, ``"silent"`` or ``"gap"``.
      **extra: Fields specific to the kind.
    """
    self.log(
        {
            "cursor": self._cursor,
            "at": self.now().isoformat(),
            "policy": self.policy.name,
            "kind": kind,
            "evidence": self._disposition,
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
  evidence_filter = EvidenceFilter()
  kept = [evidence_filter.admit(event_to_message(e))[0] for e in events]
  return tuple(m for m in kept if m is not None)
