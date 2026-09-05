"""The segmented supervision loop: stop every N turns, judge, resume.

The second carrier for the supervision stack of
:mod:`~swe_lab.trace_synthesis.supervisor`. A′ (ADR-0013) writes a correction
onto the actor's live stdin while one ``run()`` blocks; this one cuts the run
into segments with ``--max-turns``, consults the same policy at each cut, and
resumes. **Everything above the delivery mechanism is shared** — the judge, the
writer, the criterion pin, the evidence filter, the intervention rendering and
the log vocabulary are the ones A′ uses, unchanged.

The design record is
``docs/trace-synthesis/plans/task-22-segmented-supervision-loop.md``. Three
things in it are load-bearing enough to repeat where the code is:

**1. The seam record is how the one hard requirement is met — not a detector.**
Resuming with plain ``--resume`` makes the harness fabricate an ``assistant``
record reading "No response requested." that no model wrote, and training on it
would take loss on tokens the model never generated. It **cannot be found by
inspection** in the corpus that is trained on: the fields that identify it
(``message.model == "<synthetic>"``, an absent ``requestId``) exist only in the
CLI's session transcript, while ``convert.py`` reads the ``stream-json`` stream
or the proxy log, where the record arrives as an ordinary assistant text block
with no marker at all. Matching its text would give the same answer for "our
seam fabricated this" and "the model wrote that sentence".

So this loop **records every seam it cuts** — which it can do exactly, because
it cut them — and a consumer excludes by coordinate. The stronger repair is
structural rather than clerical: ``--resume-session-at`` produces no such record
at all (measured, feasibility report §9.1), and is evaluated after bring-up.

**2. What N costs, which nothing else will announce.** At ``N = 5`` an actor
that goes wrong on turn 1 is not corrected until turn 5, so turns 2-4 are a
*wrong and uncorrected* trajectory. Training on them teaches the model to keep
making that mistake. This is **not** covered by point 1 — those are tokens the
model really did write, in a state we chose not to interrupt — and it is a
separate post-processing action somebody has to take deliberately.

**3. ``cooldown`` rarely binds here, and that is stated rather than hidden.**
:class:`~swe_lab.trace_synthesis.supervisor.SpeakWhenOffTrack` measures it in
``Observation.cursor`` units, which are consumed stream events; consecutive
seams are many events apart, so the gate never closes. The spacing knob on this
path is :attr:`SegmentedSupervision.turns_per_segment`, and the shipped
definition defaults ``cooldown`` to zero so the inertness is deliberate rather
than an accident a reader has to rediscover. It remains configurable for
experiments that need to test that assumption.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import dataclasses
import datetime
import hashlib
import json
from typing import Any

from swe_lab.sandbox import ExecResult

from .seam_shape import (
    DirtySeamError,
    read_seam,
    seam_is_clean,
    SeamReading,
)
from .supervisor import (
    evidence_of,
    Intervention,
    LOG_KIND_GAP,
    LOG_KIND_LAPSE,
    LOG_KIND_SILENT,
    LOG_KIND_SPOKE,
    LOG_KIND_UNJUDGED,
    LogWriter,
    Observation,
    PolicyLapseError,
    SpeakPolicy,
    SpeakWhenOffTrack,
    Unjudged,
    Verdict,
)

#: The row recording one segment's ending: what it was cut by, where it was cut,
#: and what the cut fabricated. Separate from the policy-decision rows because
#: it is a fact about the mechanism rather than a judgement — and because a
#: consumer locating the fabricated records reads only these.
LOG_KIND_SEGMENT = "segment"

#: The terminal ``result`` subtype that means "the turn budget ran out", which
#: is this loop's cut point rather than a failure. Every other subtype ends the
#: run. Measured, with three other fields that agree, in the feasibility report
#: §3; the loop reads this one because it is the only one that names *which*
#: bounded exit happened.
CUT_SUBTYPE = "error_max_turns"

#: The subtype meaning the actor considers the task finished.
SUCCESS_SUBTYPE = "success"


@dataclasses.dataclass(frozen=True)
class SegmentedSupervision:
  """How a run is cut, judged and resumed — and where it is made to stop.

  Attributes:
    policy_factory: Builds the policy for one attempt. **A factory, not a
      policy**, for the reason ``supervision()`` is one on the A′ side: a
      judging policy carries per-run state — budget spent, cooldown, the
      markers it has recorded — and these definitions are module-level, so a
      shared instance would let one instance's spent budget silence the next
      one's corrections with nothing to show for it.
    max_segments: The hard ceiling on segments. The large default keeps normal
      rollouts away from it while remaining finite, because ``--max-turns``
      stops being the runaway guard here: on an
      unsegmented run it bounds the whole agent loop at
      :attr:`~swe_lab.harnesses.claude_code.harness.ClaudeCodeHarness.max_turns`,
      and under segmentation it bounds *one segment*. The run-level guard is
      ``max_segments * turns_per_segment``. It must stay bounded so a segment
      whose ending is misread cannot resume forever.
    wall_clock_seconds: The ceiling on elapsed time, checked between segments.
      Its large default is a runaway guard, not a spending limit.
    max_cost_usd: The ceiling on cumulative spend, read from each segment's own
      terminal ``result`` event. **Deliberately not ``--max-budget-usd``**: that
      flag writes a running balance into the actor's context, so the actor can
      see it is on a budget — a guard the actor can see is a treatment, not a
      guard (feasibility report, Amendment 1).
    turns_per_segment: How many model round-trips a segment may take, passed as
      ``--max-turns``. The owner's number is 5; it is a parameter because the
      only evidence for any value is the detection-latency distribution this
      loop records, which does not exist yet.
    cooldown: Minimum policy cursor distance between corrections. It defaults
      to zero because seams are ordinarily many stream events apart, but is a
      parameter so that assumption can be tested rather than baked in.
    anchor_resume: Resume with ``--resume-session-at <message id>`` rather than
      plain ``--resume``. On by default because it is free and produces a
      cleaner seam (feasibility report §9.1), and **nothing depends on it**:
      the owner ruled on 2026-09-03 that seam shape is post-processing's
      problem and asked for a loop that runs. Off falls back to plain
      ``--resume``, which is a supported configuration and not a degraded one.
    guard_seam: Stop the run when a resumed segment's wire shows the
      plain-resume artifacts. **Off**, and deliberately: under the same ruling
      the seam is not an acceptance condition, so this check records rather
      than blocks. The reading is written into the account either way when a
      wire is available; turning this on makes it raise instead.
    neutral_continue: What the next segment is told when the policy stays
      silent. Short and directionless on purpose — it is the control against
      which a correction's effect is read.
    guidebook_name: Workspace input containing the phase-B guidebook, or
      ``None`` when this run uses only the general-practice criterion.
  """

  policy_factory: Callable[[int], SpeakPolicy]
  max_segments: int = 1_000
  wall_clock_seconds: float = 86_400.0
  max_cost_usd: float = 1_000.0
  turns_per_segment: int = 5
  cooldown: int = 0
  anchor_resume: bool = True
  guard_seam: bool = False
  neutral_continue: str = "Continue."
  guidebook_name: str | None = None


@dataclasses.dataclass(frozen=True)
class SegmentRequest:
  """One segment's launch parameters, as the loop hands them to the harness.

  Attributes:
    index: The segment's 0-based position in the run.
    prompt: What this segment is told — the task on segment 0, and afterwards
      either a correction or :attr:`SegmentedSupervision.neutral_continue`.
    resume_session_id: The session to resume, or ``None`` for segment 0.
    resume_at_message_id: The message record to anchor the resume at, passed as
      ``--resume-session-at``. ``None`` on segment 0, and on a run that has
      :attr:`SegmentedSupervision.anchor_resume` off — the ineligible path.
    turns: The ``--max-turns`` value for this segment.
    timeout: Seconds this segment may take.
  """

  index: int
  prompt: str
  resume_session_id: str | None
  resume_at_message_id: str | None
  turns: int
  timeout: float


#: Launches one segment and returns its process outcome. Supplied by the
#: harness, which owns argv construction, staging and the sandbox; injected so
#: the loop is testable without a container.
SegmentLauncher = Callable[[SegmentRequest], ExecResult]

#: Returns the run's event-stream text as it stands now. Segments append to one
#: file, so each call returns every segment so far.
StreamReader = Callable[[], str]


@dataclasses.dataclass(frozen=True)
class SegmentEnding:
  """What one segment's terminal ``result`` event says.

  Attributes:
    subtype: ``"error_max_turns"`` at a cut, ``"success"`` when the actor is
      done, another error subtype otherwise. ``None`` when the segment wrote no
      ``result`` event at all, which is its own ending and not a cut.
    session_id: The session to resume, when the event carried one.
    cost_usd: Cumulative spend over the session, per the event.
    result_uuid: The terminal event's own ``uuid`` — half of the coordinate a
      consumer uses to find the seam in a corpus carrying no marker.
    event_index: That event's index in the appended stream — the other half.
  """

  subtype: str | None
  session_id: str | None
  cost_usd: float | None
  result_uuid: str | None
  event_index: int | None


#: The event types that are message records, and therefore the ones
#: ``--resume-session-at`` can anchor at. A ``result`` is the run's own
#: bookkeeping and a ``system`` event is the harness's, so neither is one.
MESSAGE_EVENT_TYPES = ("assistant", "user")


def last_message_uuid(events: Sequence[Mapping[str, Any]]) -> str | None:
  """Return the uuid of the most recent message record in the stream.

  This is the anchor a resumed segment is given. Every event in a real capture
  carries a ``uuid`` (170 of 170 on the first end-to-end run), so a ``None``
  here means there was no message record at all rather than a record without an
  identifier.

  Args:
    events: The decoded stream, in order.

  Returns:
    The uuid, or ``None`` when the stream holds no message record.
  """
  for event in reversed(events):
    if event.get("type") not in MESSAGE_EVENT_TYPES:
      continue
    uuid = event.get("uuid")
    if isinstance(uuid, str) and uuid:
      return uuid
  return None


def parse_events(raw: str) -> list[Mapping[str, Any]]:
  """Decode the appended event stream, skipping lines that are not JSON objects.

  Args:
    raw: The event-stream file's contents.

  Returns:
    The decoded events, in order.
  """
  events: list[Mapping[str, Any]] = []
  for line in raw.splitlines():
    line = line.strip()
    if not line:
      continue
    try:
      event = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(event, dict):
      events.append(event)
  return events


def turns_taken(events: Sequence[Mapping[str, Any]]) -> int:
  """Count the actor's turns: distinct assistant ``message.id`` values.

  A turn is one model round-trip, and ``type: assistant`` **events** outnumber
  assistant **messages** — thinking and ``tool_use`` arrive as separate events
  sharing one message id. Counting events is the trap the feasibility report
  named (§2) and it is real on production data too: the first end-to-end
  capture has 59 assistant events, 32 distinct message ids, and a ``result``
  event reading ``num_turns: 32``.

  Args:
    events: The decoded stream, in order.

  Returns:
    How many turns the actor has taken across every segment so far.
  """
  ids = {
      message.get("id")
      for event in events
      if event.get("type") == "assistant"
      for message in [event.get("message")]
      if isinstance(message, Mapping) and message.get("id") is not None
  }
  return len(ids)


def last_ending(
    events: Sequence[Mapping[str, Any]], *, since: int = 0
) -> SegmentEnding:
  """Read one segment's terminal ``result`` event.

  ``since`` is what makes the reading the segment's own. The stream file is
  appended to across segments, so a scan of the whole thing answers "how did
  this segment end" with the *previous* segment's ending whenever the current
  one wrote no ``result`` — and a stale ``error_max_turns`` is indistinguishable
  from a fresh cut, so the loop would resume a segment that never ended.

  Args:
    events: The decoded stream, in order — every segment's, appended.
    since: Index of this segment's first event. Events before it belong to
      earlier segments and are not this segment's ending.

  Returns:
    What that event says, with every field ``None`` when this segment wrote no
    ``result`` event at all — an ending this loop must not read as a cut.
    ``event_index`` is an index into ``events``, not into the slice.
  """
  for index in range(len(events) - 1, since - 1, -1):
    event = events[index]
    if event.get("type") != "result":
      continue
    cost = event.get("total_cost_usd")
    uuid = event.get("uuid")
    session = event.get("session_id")
    subtype = event.get("subtype")
    return SegmentEnding(
        subtype=str(subtype) if subtype is not None else None,
        session_id=str(session) if session is not None else None,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        result_uuid=str(uuid) if uuid is not None else None,
        event_index=index,
    )
  return SegmentEnding(
      subtype=None,
      session_id=None,
      cost_usd=None,
      result_uuid=None,
      event_index=None,
  )


#: Why the loop stopped, recorded on the final segment row. Each is a different
#: fact about the run and none of them substitutes for another.
STOP_ACTOR_FINISHED = "actor-finished"
STOP_NO_RESULT_EVENT = "no-result-event"
STOP_OTHER_ENDING = "other-ending"
STOP_MAX_SEGMENTS = "max-segments"
STOP_WALL_CLOCK = "wall-clock"
STOP_MAX_COST = "max-cost"
#: The anchored seam did not hold — or the capture could not say it did. Not a
#: ceiling and not an ending: the run is stopped because it has begun producing
#: traces that ``spec.md`` §7 disqualifies, and the loop raises rather than
#: returning so the run is recorded as an error with its artifacts intact.
STOP_DIRTY_SEAM = "dirty-seam"


def _verdicts_of(policy: SpeakPolicy) -> tuple[Verdict, ...]:
  """Return the valid verdicts a judging policy keeps, or none.

  Args:
    policy: The policy that was consulted.

  Returns:
    Its verdicts, or an empty tuple for a policy that keeps none.
  """
  return policy.verdicts if isinstance(policy, SpeakWhenOffTrack) else ()


@dataclasses.dataclass
class SegmentedRun:
  """Drives the segment loop and writes the account of it.

  Attributes:
    supervision: The policy and the three ceilings.
    task: What the actor was asked to do, for the supervisor's observation.
    launch: How one segment is run.
    read_stream: How the appended event stream is read back.
    log: Where the account goes, one JSON object per row.
    read_wire: How the capture proxy's log is read back, or ``None`` when the
      run captures no wire. **The guard on the anchored seam is the only check
      standing behind this path's argument**, so a run that resumes with an
      anchor and cannot read a wire is refused rather than trusted — see
      :mod:`swe_lab.trace_synthesis.seam_shape`.
    guidebook: The complete phase-B artifact, when the configured workflow
      supplied one.
    now: Clock, injected so the log is testable.
  """

  supervision: SegmentedSupervision
  task: str
  launch: SegmentLauncher
  read_stream: StreamReader
  log: LogWriter
  read_wire: StreamReader | None = None
  guidebook: str | None = None
  now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(
      datetime.UTC
  )

  _said: list[Intervention] = dataclasses.field(default_factory=list)
  _segments: int = 0
  _policy: SpeakPolicy | None = None

  @property
  def policy(self) -> SpeakPolicy:
    """Return this run's policy, built at the start of :meth:`run`.

    Returns:
      The policy.

    Raises:
      RuntimeError: Read before ``run`` built one.
    """
    if self._policy is None:
      raise RuntimeError("the policy is built when the run starts")
    return self._policy

  @property
  def segments_run(self) -> int:
    """How many segments were launched.

    Returns:
      The count.
    """
    return self._segments

  @property
  def corrections(self) -> tuple[Intervention, ...]:
    """What was said, in order.

    Returns:
      The interventions delivered as segment prompts.
    """
    return tuple(self._said)

  def run(self, *, timeout: float) -> ExecResult:
    """Run segments until the actor finishes or a ceiling is reached.

    Args:
      timeout: Seconds the whole run may take. Each segment is given whatever
        is left of it and of the wall-clock ceiling, whichever is smaller, so a
        late segment cannot outlive the run's own budget.

    Returns:
      The last segment's process outcome — the run's, since a segmented run
      ends where its last segment does.

    Raises:
      DirtySeamError: A resumed segment's wire showed the plain-resume
        artifacts, or could not be read at all. Raised rather than returned:
        the manager records a raising action as a run error while teardown
        still collects the artifacts, and the alternative is a trace that
        reads as ordinary and is ineligible.
    """
    # One policy per run, built here rather than shared by the definition —
    # see `SegmentedSupervision.policy_factory`.
    self._policy = self.supervision.policy_factory(self.supervision.cooldown)
    started = self.now()
    prompt = self.task
    session_id: str | None = None
    anchor: str | None = None
    last: ExecResult = ExecResult(0, "", "")
    cost = 0.0
    # How much of the appended stream belongs to earlier segments. Counted
    # forward from zero rather than measured before each launch: segment 0
    # truncates the file *inside* `launch`, so a pre-launch read would count a
    # previous run's leftovers and skip past this run's own first ending.
    consumed = 0

    while True:
      elapsed = (self.now() - started).total_seconds()
      budget = min(
          timeout - elapsed, self.supervision.wall_clock_seconds - elapsed
      )
      request = SegmentRequest(
          index=self._segments,
          prompt=prompt,
          resume_session_id=session_id,
          resume_at_message_id=(
              anchor if self.supervision.anchor_resume else None
          ),
          turns=self.supervision.turns_per_segment,
          timeout=max(budget, 0.0),
      )
      last = self.launch(request)
      self._segments += 1

      events = parse_events(self.read_stream())
      ending = last_ending(events, since=consumed)
      turns = turns_taken(events)
      if ending.cost_usd is not None:
        cost += ending.cost_usd
      session_id = ending.session_id or session_id
      anchor = last_message_uuid(events) or anchor

      stop = self._stop_reason(ending, started=started, cost=cost)
      dirty = self._seam_reading(request)
      if dirty is not None and self.supervision.guard_seam:
        self._segment_row(
            request,
            ending,
            turns=turns,
            cost=cost,
            stop=STOP_DIRTY_SEAM,
            seam=dataclasses.asdict(dirty),
        )
        raise DirtySeamError(
            f"the resume seam did not hold on segment {request.index}:"
            f" {dirty}"
        )
      self._segment_row(
          request,
          ending,
          turns=turns,
          cost=cost,
          stop=stop,
          seam=None if dirty is None else dataclasses.asdict(dirty),
      )
      if stop is not None:
        return last

      prompt = self._seam_prompt(
          events[consumed:],
          cursor=len(events),
          turns=turns,
          index=request.index,
      )
      consumed = len(events)

  def _seam_reading(self, request: SegmentRequest) -> SeamReading | None:
    """Check the wire after a resumed segment; report a reading only if bad.

    Runs on a segment that resumed and had a wire to read. **A run without a
    wire is not a failure here**: the seam is recorded, not enforced (see
    :attr:`SegmentedSupervision.guard_seam`), so the honest answer to "was the
    seam clean" is simply unavailable and the account says so with a ``None``.

    Args:
      request: The segment that just ran.

    Returns:
      The reading when the seam cannot be shown to be clean, else ``None`` —
      which covers both "it was clean" and "there was nothing to read". The
      two are distinguishable in the account by whether the run captured a
      wire at all, and nothing downstream currently needs to tell them apart.
    """
    if request.resume_session_id is None or self.read_wire is None:
      return None
    reading = read_seam(self.read_wire())
    return None if seam_is_clean(reading) else reading

  def _stop_reason(
      self,
      ending: SegmentEnding,
      *,
      started: datetime.datetime,
      cost: float,
  ) -> str | None:
    """Decide whether this segment is the last one, and why.

    The ceilings are checked **after** the ending, so a run that finished
    normally on its last affordable segment is recorded as having finished
    rather than as having hit a ceiling.

    Args:
      ending: What the segment's terminal ``result`` event said.
      started: When the run began.
      cost: Cumulative spend so far.

    Returns:
      One of the ``STOP_*`` reasons, or ``None`` to continue.
    """
    if ending.subtype is None:
      return STOP_NO_RESULT_EVENT
    if ending.subtype == SUCCESS_SUBTYPE:
      return STOP_ACTOR_FINISHED
    if ending.subtype != CUT_SUBTYPE:
      return STOP_OTHER_ENDING
    if self._segments >= self.supervision.max_segments:
      return STOP_MAX_SEGMENTS
    elapsed = (self.now() - started).total_seconds()
    if elapsed >= self.supervision.wall_clock_seconds:
      return STOP_WALL_CLOCK
    if cost >= self.supervision.max_cost_usd:
      return STOP_MAX_COST
    return None

  def _seam_prompt(
      self,
      events: Sequence[Mapping[str, Any]],
      *,
      cursor: int,
      turns: int,
      index: int,
  ) -> str:
    """Consult the policy at this seam and return the next segment's prompt.

    Both failure modes the policy can declare are handled exactly as
    :class:`~swe_lab.trace_synthesis.supervisor.Supervisor` handles them: a
    :class:`~swe_lab.trace_synthesis.supervisor.PolicyLapseError` is bounded to
    this seam and recorded as a lapse, and anything else is a gap of unknown
    reach. Neither ends the run — the actor still needs a prompt, and the
    neutral continue is what an unsupervised seam looks like.

    Args:
      events: The events from the segment that just completed.
      cursor: The cumulative event position at this boundary.
      turns: The actor's cumulative turn count at this cut.
      index: The segment that just ended.

    Returns:
      The correction, rendered, or the neutral continue.
    """
    observation = Observation(
        task=self.task,
        evidence=evidence_of(events),
        cursor=cursor,
        said=tuple(self._said),
        guidebook=self.guidebook,
    )
    # Read the valid judgement back from the concrete policy. `SpeakPolicy`
    # returns a decision rather than a verdict, and widening it would change
    # what policies that never judge must implement.
    before = len(_verdicts_of(self.policy))
    try:
      decision = self.policy.consider(observation)
    except PolicyLapseError as error:
      self._decision_row(
          LOG_KIND_LAPSE,
          index=index,
          turns=turns,
          reason=f"policy lapsed: {error!r}",
          finish_reason=error.finish_reason,
          **self._verdict_audit_after(before),
      )
      return self.supervision.neutral_continue
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      self._decision_row(
          LOG_KIND_GAP,
          index=index,
          turns=turns,
          reason=f"policy raised: {error!r}",
          **self._verdict_audit_after(before),
      )
      return self.supervision.neutral_continue

    # Only a verdict this call produced. Reading the tail regardless would
    # attribute an earlier seam's judgement to this one.
    verdicts = _verdicts_of(self.policy)
    found = verdicts[-1] if len(verdicts) > before else None
    located = {
        "deviation_started_steps_ago": (
            found.deviation_started_steps_ago if found is not None else None
        ),
        # The denominator for reading the number above: it counts rendered
        # steps and this counts turns, and one turn renders as several steps.
        "evidence_records": len(observation.evidence),
        **self._verdict_audit_after(before, decision=True),
    }

    if isinstance(decision, Unjudged):
      self._decision_row(
          LOG_KIND_UNJUDGED, index=index, turns=turns, reason=decision.reason
      )
      return self.supervision.neutral_continue
    if decision is None:
      self._decision_row(LOG_KIND_SILENT, index=index, turns=turns, **located)
      return self.supervision.neutral_continue

    self._said.append(decision)
    self._decision_row(
        LOG_KIND_SPOKE, index=index, turns=turns, text=decision.text, **located
    )
    return decision.rendered()

  def _verdict_audit_after(
      self, count: int, *, decision: bool = False
  ) -> dict[str, object]:
    """Return audit fields for the valid verdict created by this decision."""
    verdicts = _verdicts_of(self.policy)
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

  def _segment_row(
      self,
      request: SegmentRequest,
      ending: SegmentEnding,
      *,
      turns: int,
      cost: float,
      stop: str | None,
      **extra: object,
  ) -> None:
    """Record one segment's ending, and what its resume fabricated.

    ``resume_artifact_expected`` is a claim about what **we** did — this
    segment was launched with plain ``--resume``, which makes the harness write
    a synthetic assistant record at its head — and not a detection of what
    appeared. That is the whole point: the corpus carries no marker, and we
    know because we cut the seam.

    Args:
      request: What this segment was launched with.
      ending: What its terminal ``result`` event said.
      turns: Cumulative turns after it.
      cost: Cumulative spend after it.
      stop: Why the loop stopped here, or ``None`` if it did not.
      **extra: Fields specific to this row — the seam reading when the guard
        refused it.
    """
    self.log(
        {
            "kind": LOG_KIND_SEGMENT,
            "at": self.now().isoformat(),
            "policy": self.policy.name,
            "guidebook_sha256": self._guidebook_sha256(),
            "segment": request.index,
            "turns_requested": request.turns,
            "turns_total": turns,
            "stop_subtype": ending.subtype,
            "session_id": ending.session_id,
            "cost_usd": cost,
            "resumed": request.resume_session_id is not None,
            "resume_artifact_expected": request.resume_session_id is not None,
            "anchor_event_index": ending.event_index,
            "anchor_result_uuid": ending.result_uuid,
            "resume_at_message_id": request.resume_at_message_id,
            # Which resume flavour this segment used, stated rather than left
            # to a reader who would otherwise have to know what the flag
            # means. Both are supported; the anchored one leaves a cleaner
            # seam, and seam shape is post-processing's problem.
            "anchored": request.resume_at_message_id is not None,
            "stop_reason": stop,
            **extra,
        }
    )

  def _decision_row(
      self, kind: str, *, index: int, turns: int, **extra: object
  ) -> None:
    """Record what the policy decided at one seam.

    Args:
      kind: One of the shared ``LOG_KIND_*`` values.
      index: The segment that just ended.
      turns: The actor's cumulative turn count at this cut — requirement C's
        first quantity, and knowable only while running.
      **extra: Fields specific to the kind.
    """
    self.log(
        {
            "kind": kind,
            "at": self.now().isoformat(),
            "policy": self.policy.name,
            "guidebook_sha256": self._guidebook_sha256(),
            "segment": index,
            "cut_at_turn": turns,
            **extra,
        }
    )

  def _guidebook_sha256(self) -> str | None:
    """Return the identity recorded for the guidebook, when this run has one."""
    if self.guidebook is None:
      return None
    return hashlib.sha256(self.guidebook.encode()).hexdigest()
