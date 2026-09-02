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
import threading
from typing import Any, final, override, Protocol, runtime_checkable

from etils import epath

from swe_lab.harnesses.claude_code.constants import (
    CORRECTION_DONE_NAME,
    CORRECTION_DROP_NAME,
    CORRECTION_UNCLEAN_NAME,
    EVENT_STREAM_NAME,
)
from swe_lab.harnesses.claude_code.harness import user_event_line
from swe_lab.rollout import SUPERVISION_METRIC
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    SandboxError,
    SandboxFs,
    SandboxObserver,
)

from .supervisor import Intervention, SpeakPolicy, Supervisor

#: The supervisor's own account of a run, one JSON object per event consumed.
#: Named here because this is what persists it; a reader checking that a run
#: was supervised at all reads this artifact.
SUPERVISOR_LOG_NAME = "supervisor.jsonl"

# How long `before_destroy` waits for the polling thread to notice it should
# stop. It is a poll interval plus one read, never a computation.
_JOIN_TIMEOUT_SECONDS = 10.0


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
    policy_factory: Builds the policy for one run. A factory because a policy
      carries per-run state (budget, cooldown, what it has said) while a task
      is a declaration that may be executed any number of times.
    task: What the actor was asked to do, for the supervisor's observation.
    poll_interval: Seconds between reads of the actor's event stream.
    pump: The pump, once the run has started.
    channel: The channel it writes through, once the run has started.
  """

  policy_factory: Callable[[], SpeakPolicy]
  task: str
  poll_interval: float = 0.5
  pump: SupervisorPump | None = None
  channel: CorrectionChannel | None = None
  _rows: list[Mapping[str, Any]] = field(default_factory=list)
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
            policy=self.policy_factory(),
            task=self.task,
            sink=self.channel.sink,
            log=self._rows.append,
        ),
        channel=self.channel,
        events_path=sb.workspace / EVENT_STREAM_NAME,
    )
    self._thread = threading.Thread(target=self._feed, daemon=True)
    self._thread.start()

  def _feed(self) -> None:
    """Poll until the run is over, then end it."""
    assert self.pump is not None and self.channel is not None
    while not self._stop.is_set():
      _ = self.pump.poll()
      # A dead pump ends the run too. Leaving the channel open would burn the
      # wall clock and reach the outside as the actor's timeout (ADR-0015),
      # charging our breakage to it; the metric below is what keeps the
      # deliberate close from reading as an ordinary early finish.
      if self.pump.at_rest or not self.pump.healthy:
        self.channel.close()
        return
      _ = self._stop.wait(self.poll_interval)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Stop supervising, persist the account, and report a lost supervisor.

    Two conditions raise the metric, because they are the same fact reached
    two ways: the pump stopped feeding the supervisor, or the channel closed
    without being told to. Either means the actor finished part of its work
    unsupervised, which is what
    :func:`~swe_lab.rollout.rollout_outcome` turns into
    ``SUPERVISION_FAILED``.

    Args:
      sb: Unused — every fact here is already on the host.

    Returns:
      The account, plus the metric when the run lost its supervisor. The
      metric is an event, so a healthy run leaves no key rather than a zero.
    """
    del sb
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    if self.pump is not None:
      _ = self.pump.poll()  # whatever the actor wrote after the last tick
    lost = (
        self.pump is None
        or not self.pump.healthy
        or self.channel is None
        or self.channel.closed_uncleanly
    )
    account = "".join(json.dumps(row) + "\n" for row in self._rows)
    return Contribution(
        inline_artifacts={SUPERVISOR_LOG_NAME: account.encode()},
        metrics={SUPERVISION_METRIC: 1.0} if lost else {},
    )
