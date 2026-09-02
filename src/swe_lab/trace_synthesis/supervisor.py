"""The supervisor: what it may see, when it speaks, what it may say.

The component behind task 05 (``docs/trace-synthesis/plans/``). It consumes the
actor's live output stream and, when its policy says the moment has come, writes
one short user message into a sink that reaches the actor's stdin — the channel
decided by ADR-0013 (``docs/decisions/``).

Three properties are structural rather than advisory, and each has a test:

- **The information barrier is this module's constructor.** :class:`Observation`
  has no field that can carry the gold patch, the reference or test patch, or
  the hidden tests, and its evidence is built only from records the actor
  produced. The barrier is *not* information-theoretic: the guidebook is
  distilled from privileged material in phase B, and the supervisor does see the
  guidebook. What it never sees is the solution itself.
- **When to speak is a seam.** It is the measured unknown — 8 of 8
  non-compliances in the one graded batch arrived too late — so a
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

# The cap is enforced; "short, directional, not a solution" is not. A
# machine-checkable version of that property is what reshaped the intervention
# into one naming a concrete next action in the graded batch, which is the
# mistake this module declines to repeat one layer down.
MAX_INTERVENTION_CHARS = 400

# The provenance marker. Measured: 0 of 37 interventions carrying it were
# challenged by the actor as unattributed.
INTERVENTION_TAG = "supervisor_note"

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
  patch, the reference or test patch, or the hidden tests, and
  ``test_supervisor_input_carries_no_privileged_field`` asserts the list against
  an exact allowlist so that adding one fails.

  Attributes:
    evidence: What the supervisor may read, in order: the **task statement**,
      the actor's assistant messages, and the results of the tools it called.
      The barrier keeps out the solution, not the goal — a supervisor blind to
      what was asked can only object to style.
    cursor: How many stream events have been consumed, including those that
      carried no message. Identifies where a decision was taken.
    guidebook: Phase B's artifact, the criterion to judge against.
    said: What this supervisor has already said in this run — its **memory**,
      a separate channel from its evidence. Its own words never come back as
      observations, so without this a policy has nothing to check against and
      can repeat itself indefinitely.
  """

  evidence: tuple[Message, ...]
  cursor: int
  guidebook: str
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


# How a message was dispositioned, recorded so the account of a run says why
# something was not judged rather than leaving it missing.
ADMITTED_TASK_STATEMENT = "task-statement"
ADMITTED_ASSISTANT = "assistant"
ADMITTED_TOOL_RESULT = "tool-result"
EXCLUDED_OWN_INTERVENTION = "excluded-own-intervention"
EXCLUDED_EXTERNAL_TEXT = "excluded-external-text"
EXCLUDED_NOTHING_TO_KEEP = "excluded-nothing-to-keep"


@dataclasses.dataclass
class EvidenceFilter:
  """Decides what reaches the supervisor — by **origin**, not by role.

  The barrier keeps out the *solution*, not the *goal*. A supervisor that
  cannot see what the task asked for cannot tell deviation from progress; all
  it can do is object to style. So the task statement is admitted, and what is
  kept out is what the supervisor itself put into the conversation.

  Role is the wrong axis to cut on, and cutting on it was this filter's first
  form: the task statement and a supervisor correction are both ``user``
  messages, and they differ in where they came from.
  """

  _task_statement_open: bool = True
  _task_statement_seen: bool = False

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
      # The actor has started answering, so any later user text is an
      # interjection rather than the brief.
      self._task_statement_open = False
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
      # Our own words, arriving back on the stream. They belong in the
      # supervisor's memory, never in its evidence.
      return None, EXCLUDED_OWN_INTERVENTION
    if self._task_statement_open and not self._task_statement_seen:
      self._task_statement_seen = True
      return message, ADMITTED_TASK_STATEMENT
    return None, EXCLUDED_EXTERNAL_TEXT


@dataclasses.dataclass
class Supervisor:
  """Consumes the actor's stream, consults a policy, writes what it decides.

  Attributes:
    policy: When to speak.
    guidebook: The criterion handed to the policy.
    sink: Where a correction is written. Borrowed: never closed here.
    log: Where the account of the run is written, one row per event consumed.
    now: Clock, injected so the log is testable.
  """

  policy: SpeakPolicy
  guidebook: str
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
        evidence=tuple(self._evidence),
        cursor=self._cursor,
        guidebook=self.guidebook,
        said=tuple(self._said),
    )
    try:
      intervention = self.policy.consider(observation)
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      self._row("gap", reason=f"policy raised: {error!r}")
      return None

    if intervention is None:
      self._row("silent")
      return None
    if self._mute:
      self._row(
          "gap", reason="sink unusable; not attempted", text=intervention.text
      )
      return None

    try:
      self.sink(intervention.rendered())
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      # The channel is gone, but the run is not ours to end: stop speaking and
      # keep accounting for every later event.
      self._mute = True
      self._row("gap", reason=f"sink raised: {error!r}", text=intervention.text)
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
