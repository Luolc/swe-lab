"""The supervisor: what it may see, when it speaks, what it may say.

The component behind task 05 (``docs/trace-synthesis/plans/``). It is the
supervision itself and not a way of reaching the actor: what the policy may
look at, when it is allowed to speak, what it may say, and what the account of
a run records. The one carrier that drives it is the segment loop
(:mod:`~swe_lab.trace_synthesis.segmented_loop`), which stops the actor, asks
this policy, and resumes — ADR-0026.

Three properties are structural rather than advisory, and each has a test:

- **The policy sees the actor and its guidebook.** :class:`Observation` carries
  the actor's own records, the task, and the complete phase-B guidebook when a
  guided workflow supplies one. The shared criterion remains beside it as the
  standard for general engineering practice. The default prompt builder uses
  the guidebook's compact rubric when present and the complete tutorial only
  for an explicit legacy input; a replacement builder still receives the
  complete artifact.
- **Speech has a shallow mechanical floor.** Corrections are non-empty and at
  most 400 characters; the policy also rejects fenced code, diff hunks
  and eight-word copying from the guidebook. These checks do not establish that
  a semantic paraphrase is safe. `supervisor.jsonl` keeps the guidebook
  identity, judge request/reason and emitted text so a person can audit that
  judgement.
- **When to speak is a seam.** It is the open variable of the design, so a
    :class:`SpeakPolicy` is replaceable without touching the consumer, the
    intervention, or the log.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import dataclasses
import hashlib
import json
import re
from typing import Any, Literal, Protocol

from swe_lab.conversation import Message, Role, ToolResultBlock
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

#: Who is shown what the supervisor has already said (:attr:`Observation.said`)
#: when the default prompts are built. ``"writer"``, the default (ADR-0024):
#: the writer sees it and the judge does not — a judge shown its own
#: corrections read them as the actor's record and confirmed itself for the
#: rest of the run (issue #381). ``"both"``: the behaviour before ADR-0024,
#: kept as an A/B arm. ``"none"``: neither call sees it, the A/B control.
#: Every decision row records the value it ran under as ``said_visibility``.
type SaidVisibility = Literal["writer", "both", "none"]

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
    judge_input: The credential-free judge request behind the lapse, when the
      request was built — carried over from the judge's own error, whether
      the answer came back unusable
      (:class:`~swe_lab.trace_synthesis.judge.JudgeAnswerError`) or never
      came back at all
      (:class:`~swe_lab.trace_synthesis.judge.JudgeTransportError`), so the
      boundary records what was asked exactly as one with a valid verdict
      does. ``None`` when the lapse did not come from a judge call at all.
  """

  finish_reason: str | None
  judge_input: Mapping[str, Any] | None

  def __init__(
      self,
      message: str,
      *,
      finish_reason: str | None = None,
      judge_input: Mapping[str, Any] | None = None,
  ) -> None:
    """Record the lapse together with what is known about the call behind it.

    Args:
      message: What went wrong.
      finish_reason: See the class attribute.
      judge_input: See the class attribute.
    """
    super().__init__(message)
    self.finish_reason = finish_reason
    self.judge_input = judge_input


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

  A guidebook-guided workflow supplies the complete phase-B artifact here so
  the judge and writer can steer toward its instance-specific route. What
  arrives is whatever the Oracle wrote — the schema measures it and does not
  gate it (ADR-0027), so a builder reads the representation it finds.
  Workflows without one leave :attr:`guidebook` unset and retain the
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
      can repeat itself indefinitely. Which model call is shown it is
      :data:`SaidVisibility`'s question: the writer by default, the judge only
      under ``"both"`` (ADR-0024).
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
class Verdict:
  """One judge call's answer.

  Attributes:
    off_track: Whether the actor has left the criterion's path. The only field
      that opens the speaking path.
    reason: The judge's own words, recorded but never acted on.
    running_state: The bounded observational state after the evidence this
      verdict judged. The standard model judge requires it; the default keeps
      lightweight custom judges source-compatible.
    deviation_started_steps_ago: How many of the shown steps ago the judge
      believes the deviation began, or ``None`` when it was not asked — which
      is the default. **Never acted on**, exactly like ``reason``: it exists so
      a segmented run can record how many turns late its correction was, which
      is the only evidence a choice of segment length could ever rest on.

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
  """Decides whether the actor is off the criterion's path."""

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
  2. budget remaining, else silent;
  3. cooldown elapsed since the last intervention, else silent;
  4. the writer produces a usable line, else the failure is bounded to this
     boundary and recorded as a lapse. Never a retry.

  The cost of that order is stated rather than hidden: the judge runs on every
  boundary carrying evidence even after the budget is spent, so a ``budget=0``
  policy still pays for a judge it can never act on. That is a property of the
  gate order rather than something any shipped definition asks for: the
  would-have-spoken markers a zero-budget arm existed to collect went with the
  correction channel (ADR-0026), and no definition ships a zero budget today.

  The criterion is a constructor argument rather than a field on
  :class:`Observation`, so it never travels the channel the actor's records
  travel. Two things are enforced here and no more: construction **rejects** a
  criterion whose digest is not
  :data:`~swe_lab.trace_synthesis.criterion.CRITERION_SHA256`, and ``consider``
  **passes** it to the judge on every call, so it is carried rather than stored
  beside one. What a judge then measures against is the judge's own invariant.

  Attributes:
    judge: The off-track call.
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
    said_visibility: Which of the two calls' default prompts render
      :attr:`Observation.said`; see :data:`SaidVisibility`. A record for the
      decision rows rather than a switch: the prompt builders inside ``judge``
      and ``writer`` own the rendering, and
      :func:`~swe_lab.trace_synthesis.judge.supervising_policy` sets both from
      its one argument — pinned by
      ``test_the_policy_records_the_said_visibility_its_builders_were_given``.
      A policy assembled by hand states its own.
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
  said_visibility: SaidVisibility = "writer"

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
          judge_input=getattr(error, "judge_input", None),
      ) from error
    self._verdicts.append(verdict)
    self._running_state = verdict.running_state
    if not verdict.off_track:
      return None

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


def said_visibility_of(policy: SpeakPolicy) -> SaidVisibility | None:
  """Return the said visibility a judging policy records, or ``None``.

  Args:
    policy: The policy that was consulted.

  Returns:
    Its :attr:`SpeakWhenOffTrack.said_visibility`, or ``None`` for a policy
    that makes no model call and so has no prompt for the question to apply
    to.
  """
  if isinstance(policy, SpeakWhenOffTrack):
    return policy.said_visibility
  return None


def judge_prompt_sha256(judge_input: Mapping[str, Any] | None) -> str | None:
  """Digest the user prompt behind a verdict, when the record carries one.

  The user prompt is the half of the request that ``said`` can enter, so its
  digest is what pairs a row with its counterpart in another arm: two rows
  with one digest were judged on the same bytes. The system half is pinned
  by test and left out.

  Args:
    judge_input: The credential-free request a verdict carries, or ``None``
      from a judge that exposes none.

  Returns:
    The hex SHA-256 of the first message's string content, or ``None`` when
    the record has no such content.
  """
  if judge_input is None:
    return None
  messages = judge_input.get("messages")
  first = messages[0] if isinstance(messages, list) and messages else None
  content = first.get("content") if isinstance(first, Mapping) else None
  if not isinstance(content, str):
    return None
  return hashlib.sha256(content.encode()).hexdigest()


def lapsed_judge_request(error: PolicyLapseError) -> dict[str, object]:
  """Return the request fields a judge lapse still carries, or nothing.

  A judge whose answer was unusable, or whose transport raised, built its
  request all the same; the row for that boundary records it, with its
  digest, so the pairing across arms survives on exactly the rows where the
  judge failed. A lapse that did not come from a judge call carries nothing
  here — the writer's lapse row takes those fields from the valid verdict
  that preceded it.

  Args:
    error: The bounded failure the policy raised.

  Returns:
    ``judge_input`` and ``judge_prompt_sha256``, or an empty mapping.
  """
  if error.judge_input is None:
    return {}
  return {
      "judge_input": error.judge_input,
      "judge_prompt_sha256": judge_prompt_sha256(error.judge_input),
  }


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


def _admit(message: Message | None) -> Message | None:
  """Decide whether one converted message becomes evidence, by **origin**.

  The barrier keeps out the solution, not the goal. The goal does not travel
  this path at all: the task statement is handed to the supervisor by whoever
  wrote the prompt, so it needs no rule here and cannot be confused with
  anything else on the stream.

  What this admits is therefore exactly what the *actor* produced — its
  assistant messages and the results of its own tool calls. Every user text is
  excluded: text carrying the intervention tag came from the supervisor, and
  anything else is an outside interjection. Neither is an observation of what
  the actor did.

  Stateless by construction: a supervisor attached mid-run reaches the same
  answer on a message as one that watched from the first event.

  Args:
    message: A converted stream message, or ``None``.

  Returns:
    The record to keep, or ``None``.
  """
  if message is None:
    return None
  if message.role == Role.ASSISTANT:
    return message
  results = [b for b in message.content if isinstance(b, ToolResultBlock)]
  if results:
    return Message(role=message.role, content=list(results))
  return None


def evidence_of(events: Sequence[Mapping[str, Any]]) -> tuple[Message, ...]:
  """Build the evidence a supervisor would have seen over a whole stream.

  Args:
    events: Decoded ``stream-json`` events, in order.

  Returns:
    The messages a supervisor would have seen.
  """
  # Imported here, not at module scope: the `claude_code` package's
  # ``__init__`` imports its harness, and the harness takes a
  # ``SegmentedSupervision`` from this package — so a module-level import
  # closes a cycle whenever a trace-synthesis module is imported first. The
  # same reasoning `vocabulary.py`'s docstring gives for existing at all.
  from swe_lab.harnesses.claude_code.convert import event_to_message

  kept = [_admit(event_to_message(e)) for e in events]
  return tuple(m for m in kept if m is not None)
