"""The host half of the live correction channel.

The sandbox half is in the Claude Code harness: a FIFO on the agent's stdin,
fed by an in-sandbox relay that reads a drop directory in the workspace. This
module is what writes into that directory, and what reads the agent's live
event stream to decide when to.

**There is no transport here on purpose.** The workspace is bind-mounted into
the container (``-v {workspace}:{mount_at}``, see ``sandbox/backends/host.py``),
so a file the host writes into ``<workspace>/corrections`` is already inside the
sandbox, and the agent's event stream is already on the host. A second
transport — a socket, an exec — would be a new failure surface buying nothing.

Two properties are structural rather than advisory, and each has a test:

- **Ending the run is deliberate.** :meth:`CorrectionChannel.close` writes the
  sentinel the relay waits for. Closing the FIFO's write end is what makes the
  CLI exit, so it must be an act, never a side effect.
- **A pump that dies leaves evidence.** A supervisor that stops polling
  part-way leaves every later boundary unjudged while the run itself finishes
  and looks complete, so silence is indistinguishable from supervision. Hence
  :class:`SupervisorPump` records the failure that stopped it and reports
  itself unhealthy, which is what disqualifies the run as evidence about
  supervision.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
import json
import pathlib
import threading
from typing import Any, final, override, Protocol, runtime_checkable

from etils import epath

from swe_lab.harnesses.claude_code.constants import (
    CORRECTION_DONE_NAME,
    CORRECTION_DROP_NAME,
    CORRECTION_UNCLEAN_NAME,
    EVENT_STREAM_NAME,
)
from swe_lab.harnesses.claude_code.convert import user_event_line
from swe_lab.rollout import SUPERVISION_LAPSE_METRIC, SUPERVISION_METRIC
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    SandboxError,
    SandboxFs,
    SandboxObserver,
)

from .judge import supervising_policy, Transport
from .supervisor import (
    Intervention,
    LOG_KIND_GAP,
    LOG_KIND_LAPSE,
    LOG_KIND_SPOKE,
    SpeakPolicy,
    Supervisor,
)

#: How many actor events the supervisor was consulted about — one per row of
#: the account, which is what makes ``boundaries == 0`` read as "never
#: supervised" rather than "supervised and quiet".
#:
#: **Not the judge-call count.** A boundary whose evidence window is empty
#: consults no judge and is recorded as
#: :data:`~swe_lab.trace_synthesis.supervisor.LOG_KIND_UNJUDGED`, so for the
#: shipped policy this count is an *upper bound* on the calls a supervised run
#: pays for beyond the actor. The gap is those rows and is read off the
#: account, which is also the only place a `boundaries > 0, corrections == 0`
#: run says which of "judged and stayed silent" and "never judged" it was.
#: Counted separately from the actor because the repo's per-rollout cost figure
#: is the actor's alone.
BOUNDARIES_METRIC = "supervision.boundaries"

#: How many corrections were delivered. Never absent on a supervised run, so
#: "spoke zero times" is distinguishable from "was not supervised" — the
#: artifact below says which.
CORRECTIONS_METRIC = "supervision.corrections"

#: The supervisor's own account of a run, one JSON object per event consumed.
#: Named here because this is what persists it; a reader checking that a run
#: was supervised at all reads this artifact.
SUPERVISOR_LOG_NAME = "supervisor.jsonl"

# How long `before_destroy` waits for the supervising thread to notice it
# should stop. A thread doing its job needs a poll interval plus one read; a
# thread that needs longer is blocked in something that does not return, which
# is a lost supervisor rather than a slow one.
JOIN_TIMEOUT_SECONDS = 10.0


@dataclass
class CorrectionChannel:
  """Writes corrections into a running sandbox, and ends the run.

  Attributes:
    workspace: The run's workspace — the host side of the bind mount, so a file
      written under it is visible in the sandbox without any copy step.
    delivered: How many corrections have been written, which is also the
      ordering key: the relay reads ``*.json`` in name order.
  """

  workspace: epath.Path
  delivered: int = 0

  def __post_init__(self) -> None:
    """Create the drop directory, before anything in the sandbox can.

    The relay creates it too (``mkdir -p``), and the sandbox runs as root while
    this process does not: a directory root made first in a bind mount is one
    this side can no longer write into. Whoever creates it owns it, so it is
    created here — the channel exists before the actor's script starts, and the
    relay then finds it already there. Root can write into ours; we cannot
    write into root's.
    """
    self.drop_dir.mkdir(parents=True, exist_ok=True)

  @property
  def drop_dir(self) -> epath.Path:
    """The directory the in-sandbox relay polls."""
    return self.workspace / CORRECTION_DROP_NAME

  def sink(self, rendered: str) -> None:
    """Write one correction for the relay to append to the agent's stdin.

    This is the component's :data:`~swe_lab.trace_synthesis.supervisor.Sink`,
    so the supervisor stays the only thing that decides *whether* to speak and
    this stays the only thing that knows *how* a message reaches the sandbox.
    A pump that also delivered would send everything twice.

    Written to a temporary name and renamed, because the relay polls for
    ``*.json`` and would otherwise be free to read a half-written file — the
    rename is the only step that makes the file match.

    Args:
      rendered: The message text, already rendered by the supervisor.
    """
    self.drop_dir.mkdir(parents=True, exist_ok=True)
    self.delivered += 1
    final = self.drop_dir / f"msg-{self.delivered:04d}.json"
    staging = self.drop_dir / f".{final.name}.partial"
    _ = staging.write_text(user_event_line(rendered))
    _ = staging.replace(final)

  def close(self) -> None:
    """End the run, deliberately.

    The relay closes the FIFO's write end when it sees this file, and the CLI
    exits on stdin EOF. That chain *is* the termination mechanism, so it starts
    here — from whoever decided the task is over — and never from something
    dying.
    """
    self.drop_dir.mkdir(parents=True, exist_ok=True)
    (self.drop_dir / CORRECTION_DONE_NAME).touch()

  @property
  def closed_uncleanly(self) -> bool:
    """Whether the channel ended any way other than the deliberate close.

    The relay writes its marker before it exists and removes it only on that
    close, so this is failure-closed: a relay that is killed cannot write a
    marker, but it also cannot remove one. Read after the run, it is what
    separates "the agent stopped early" from "our side fell over".
    """
    return (self.workspace / CORRECTION_UNCLEAN_NAME).exists()


def stream_events(text: str) -> Iterator[Mapping[str, Any]]:
  """Parse whole JSON lines out of an event-stream fragment.

  The file is being appended to while it is read, so the last line is routinely
  incomplete. A trailing fragment is **skipped, not guessed at**: it will be
  complete on a later read.

  Args:
    text: The fragment read so far.

  Yields:
    One decoded event per complete line.
  """
  for line in text.split("\n"):
    line = line.strip()
    if not line:
      continue
    try:
      event = json.loads(line)
    except json.JSONDecodeError:
      continue  # a partial trailing line, or a line we cannot use
    if isinstance(event, dict):
      yield event


@dataclass
class SupervisorPump:
  """Feeds a running agent's events to a supervisor, and its answers back.

  Attributes:
    supervisor: Decides whether this moment deserves a correction.
    channel: Where a correction goes.
    events_path: The agent's live event stream, in the workspace.
    failure: The exception that stopped the pump, if one did. **Not** raised
      onward: the agent is mid-run and killing it would convert a supervision
      failure into a lost rollout. Recorded instead, and read afterwards.
    interventions: Every correction the supervisor delivered, in order. The
      supervisor writes them through the channel's sink; this is the record of
      what it did, not a second delivery path.
    saw_events: Whether one usable actor event was ever handed to the
      supervisor. **Not** whether the stream file appeared: a file that exists
      and yields nothing decodable is the same fact as no file at all — a
      supervisor that watched nothing — and it is indistinguishable from a
      healthy one unless it is recorded, since ``poll`` returning 0 says the
      same thing for "no new events", "nothing I could decode" and "no file,
      ever". ``stream_events`` skips a line it cannot parse rather than
      raising, so the existence of bytes is not evidence that any of them
      reached a judgement.
    at_rest: Whether the last event consumed was the actor finishing a turn
      with the supervisor having nothing to add. Under a live stdin channel the
      actor does not exit when it finishes answering — it waits for more input
      (task 16 §8.2) — so this is the moment, and the only one, at which the
      run is over.
  """

  supervisor: Supervisor
  channel: CorrectionChannel
  events_path: epath.Path
  failure: Exception | None = None
  interventions: list[Intervention] = field(default_factory=list)
  saw_events: bool = False
  at_rest: bool = False
  _offset: int = 0

  @property
  def healthy(self) -> bool:
    """Whether the pump is still supervising.

    An unhealthy pump means the run continued **unsupervised** from some point
    on. That is not a detail of the report; it decides whether the run is
    evidence about supervision at all.
    """
    return self.failure is None

  def poll(self) -> int:
    """Read whatever the agent has written since the last call, and act.

    Returns:
      The number of corrections delivered by this call.
    """
    if self.failure is not None:
      return 0
    try:
      if not self.events_path.exists():
        return 0
      text = self.events_path.read_text()[self._offset :]
      # Only whole lines are consumed, so a fragment is re-read next time
      # rather than dropped.
      consumed = text.rfind("\n") + 1
      if consumed <= 0:
        return 0
      self._offset += consumed
      spoken = 0
      for event in stream_events(text[:consumed]):
        # Here, not at the file check: what makes a run supervised is an event
        # reaching a judgement, and a file full of lines nobody could decode
        # gets no further than the parser.
        self.saw_events = True
        # `observe` writes through the sink itself and returns only what it
        # actually delivered, so this records rather than re-sends.
        intervention = self.supervisor.observe(event)
        if intervention is not None:
          self.interventions.append(intervention)
          spoken += 1
        self.at_rest = event.get("type") == "result" and intervention is None
      return spoken
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      self.failure = error
      return 0


@runtime_checkable
class BindMounted(Protocol):
  """A sandbox whose workspace is a host directory the run can write into.

  The correction channel has no transport of its own: it writes a file the
  container already sees. That is a property of the *backend*, not of every
  sandbox, so it is asked for rather than assumed — a remote sandbox would need
  a transport this module deliberately does not have.
  """

  workspace: epath.Path


@final
@dataclass
class SupervisedRun(SandboxObserver):
  """Runs a supervisor for exactly as long as the actor runs.

  The seam is the sandbox lifecycle rather than a wrapper around the action:
  ``after_create`` fires before the body and ``before_destroy`` after it, which
  is the bracket a host-side component needs around a blocked ``run()`` (task
  16 §2). Nothing about the harness's own call path changes, so a supervised
  run and an unsupervised one execute the same script.

  It also **ends the run**. Under the live channel the actor waits for more
  input after it finishes answering, so closing the channel is the termination
  mechanism and somebody has to decide when: this closes at the first turn
  boundary the supervisor lets pass in silence, which for a control policy that
  never speaks is the actor's first result.

  Attributes:
    policy: When to speak, already built. A policy carries per-run state
      (budget, cooldown, what it has said), so one of these belongs to one
      attempt — which is what :func:`supervision` produces, and what makes the
      criterion's digest check happen before the sandbox exists rather than
      inside it.
    task: What the actor was asked to do, for the supervisor's observation.
    poll_interval: Seconds between reads of the actor's event stream.
    join_timeout: Seconds teardown waits for the supervising thread to stop.
      A thread still running after it is not slow, it is stuck — in a model
      call, a sink, a read that does not return — and the run is treated as
      having lost its supervisor.
    pump: The pump, once the run has started.
    channel: The channel it writes through, once the run has started.
  """

  policy: SpeakPolicy
  task: str
  poll_interval: float = 0.5
  join_timeout: float = JOIN_TIMEOUT_SECONDS
  pump: SupervisorPump | None = None
  channel: CorrectionChannel | None = None
  _rows: list[Mapping[str, Any]] = field(default_factory=list)
  _gap: bool = False
  _lapses: int = 0
  _stuck: bool = False
  _stop: threading.Event = field(default_factory=threading.Event)
  _thread: threading.Thread | None = None

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the supervisor's own account of the run.

    Returns:
      One artifact, :data:`SUPERVISOR_LOG_NAME` — the log the supervisor
      writes a row to for every event it consumes.
    """
    return (
        ArtifactSchema(
            SUPERVISOR_LOG_NAME,
            description=(
                "one JSON object per event the supervisor consumed: what it"
                " said, or why it stayed silent"
            ),
        ),
    )

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Build the channel and start feeding the supervisor.

    Args:
      sb: The live sandbox, which must expose the host side of its workspace.

    Raises:
      SandboxError: The backend does not bind-mount its workspace, so a
        correction written on the host would never be visible to the actor.
    """
    if not isinstance(sb, BindMounted):
      raise SandboxError(
          "a supervised run needs a bind-mounted workspace; this sandbox"
          f" ({type(sb).__name__}) does not expose one, so a correction would"
          " never reach the actor"
      )
    self.channel = CorrectionChannel(workspace=sb.workspace)
    self.pump = SupervisorPump(
        supervisor=Supervisor(
            policy=self.policy,
            task=self.task,
            sink=self.channel.sink,
            log=self._record,
        ),
        channel=self.channel,
        events_path=sb.workspace / EVENT_STREAM_NAME,
    )
    self._thread = threading.Thread(target=self._feed, daemon=True)
    self._thread.start()

  def _record(self, row: Mapping[str, Any]) -> None:
    """Keep the supervisor's row, and notice a boundary it could not cover.

    A gap and a silence are both ``None`` back from ``observe``, and they mean
    opposite things: one is a decision, the other is a boundary that went
    unsupervised or a correction that was never delivered. Read here because
    the log is where the supervisor already tells them apart — as it does the
    third ``None``, a boundary the policy took no decision at, which is neither
    a decision nor a failure and is counted as neither.

    A lapse is the third thing, and it is counted rather than latched: the
    policy named the boundary it could not cover and asserted the next one is
    unaffected, so the run keeps going and keeps its evidence value. Nothing
    here re-derives that scope — the classification is the policy's, made where
    the failure happened.

    Args:
      row: One row of the supervisor's account.
    """
    self._rows.append(row)
    if row.get("kind") == LOG_KIND_GAP:
      self._gap = True
    elif row.get("kind") == LOG_KIND_LAPSE:
      self._lapses += 1

  @property
  def supervision_accounted_for(self) -> bool:
    """Whether every boundary of this run is accounted for.

    Not the same claim as "every boundary was covered", and the difference is
    the point: a boundary the policy could name and bound (a lapse) leaves the
    run accounted for, because a reader can say exactly which boundary went
    unsupervised and why. What breaks the account is a hole of unknown reach.

    Five ways it stops being true, and they are one fact reached five ways:
    no usable actor event ever reached the supervisor (whether because the
    stream never appeared or because nothing in it could be decoded), the pump
    stopped reading, the supervisor hit a boundary it could not judge or could
    not speak at *and could not bound*, the supervising thread never stopped,
    or the channel ended without being told to.

    Returns:
      Whether the run is evidence about supervision at all.
    """
    return (
        self.pump is not None
        and self.pump.saw_events
        and self.pump.healthy
        and not self._gap
        and not self._stuck
        and self.channel is not None
        and not self.channel.closed_uncleanly
    )

  def _feed(self) -> None:
    """Poll until the run is over, then end it."""
    assert self.pump is not None and self.channel is not None
    while not self._stop.is_set():
      _ = self.pump.poll()
      # Anything that ends supervision ends the run. Leaving the channel open
      # would burn the wall clock and reach the outside as the actor's timeout
      # (ADR-0015), charging our breakage to it; the metric below is what keeps
      # the deliberate close from reading as an ordinary early finish.
      # `_gap` and not the lapse count: a bounded failure costs one boundary,
      # and ending the run over it would throw away every boundary after it to
      # avoid losing one.
      if self.pump.at_rest or self._gap or not self.pump.healthy:
        try:
          self.channel.close()
        except OSError as error:
          # The drop directory is how the run is ended; if it cannot be
          # written the actor will sit until the wall clock kills it, and the
          # only thing that keeps that ending ours is this.
          self.pump.failure = error
        return
      _ = self._stop.wait(self.poll_interval)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Stop supervising, persist the account, and report what it cost.

    Two different facts, and they are reported as two:
    :attr:`supervision_accounted_for` negated is
    :data:`~swe_lab.rollout.SUPERVISION_METRIC`, which
    :func:`~swe_lab.rollout.rollout_outcome` turns into ``SUPERVISION_FAILED``
    — the run is not evidence about supervision. The lapse count is
    :data:`~swe_lab.rollout.SUPERVISION_LAPSE_METRIC`, which changes no outcome
    word: the run *is* evidence, and this is how many of its boundaries went
    unsupervised for a named reason. Reported here rather than left in the log
    because a run whose denominator includes unsupervised boundaries has to say
    so where the outcome is read, not only where the account is stored — it
    reaches the run's metrics, which ``run_task`` copies verbatim into
    ``AttemptRecord.metrics``, the path ``SUPERVISION_METRIC`` already takes.

    Args:
      sb: Unused — every fact here is already on the host.

    Returns:
      The account, the two counts a supervised run always carries, and two
      events: the metric that changes the run's outcome word when it lost its
      supervisor, and the lapse count when any boundary went unsupervised for a
      named reason. The counts are measurements and are always present; the
      events leave no key rather than a zero.
    """
    del sb
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=self.join_timeout)
      # A thread still running owns the pump and the supervisor. Reading them
      # here would race it, and polling would put a second `consider` call
      # into a policy that has not returned from its first — so teardown takes
      # the liveness as the answer and touches nothing else.
      self._stuck = self._thread.is_alive()
    if self.pump is not None and not self._stuck:
      _ = self.pump.poll()  # whatever the actor wrote after the last tick
    lost = not self.supervision_accounted_for
    # Read off the account rather than plumbed through the policy: a row is
    # written for every event the supervisor was consulted about, and a
    # `spoke` row is exactly one delivered correction.
    counts: dict[str, float] = {
        BOUNDARIES_METRIC: float(len(self._rows)),
        CORRECTIONS_METRIC: float(
            sum(1 for row in self._rows if row.get("kind") == LOG_KIND_SPOKE)
        ),
    }
    account = "".join(json.dumps(row) + "\n" for row in self._rows)
    events: dict[str, float] = {}
    if lost:
      events[SUPERVISION_METRIC] = 1.0
    if self._lapses:
      events[SUPERVISION_LAPSE_METRIC] = float(self._lapses)
    return Contribution(
        inline_artifacts={SUPERVISOR_LOG_NAME: account.encode()},
        metrics=counts | events,
    )


def supervision(
    *,
    model: str,
    transport: Transport,
    budget: int,
    cooldown: int = 4,
    window: int = 8,
    gold_patch: str | None = None,
    criterion_path: pathlib.Path | None = None,
) -> Callable[[str], SupervisedRun]:
  """Return the supervision a rollout composes, given how to reach a model.

  The :data:`~swe_lab.rollout.SupervisionFactory` the rollout task takes. It
  builds one policy per attempt, because a policy carries per-run state, and it
  builds it **while the observers are being assembled** — before the sandbox is
  created. That ordering is what turns the criterion's digest check into a
  refusal to start the run: a forged artifact raises here, and no container has
  been paid for.

  The arguments are ``supervising_policy``'s, forwarded. This is the seam
  rather than a convenience: the policy has to be constructed by whoever
  constructs the run, and there is no other point in a rollout that is both
  after the instance is known and before the sandbox comes up.

  Args:
    model: The model for the judge and the writer.
    transport: How requests are sent; injected, so a caller with no network
      still composes.
    budget: How many interventions one run may carry.
    cooldown: Boundaries required between interventions.
    window: How many of the actor's records the judge sees.
    gold_patch: This instance's gold patch, when recorded, for the criterion's
      redundant overlap check.
    criterion_path: The artifact to load; production leaves it unset.

  Returns:
    A callable taking the task text and returning the run's supervision.
  """

  def build(task: str) -> SupervisedRun:
    return SupervisedRun(
        policy=supervising_policy(
            model=model,
            transport=transport,
            budget=budget,
            cooldown=cooldown,
            window=window,
            gold_patch=gold_patch,
            criterion_path=criterion_path,
        ),
        task=task,
    )

  return build
