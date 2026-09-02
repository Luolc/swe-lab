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

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
import json
from typing import Any, final, override

from etils import epath

from swe_lab.harnesses.claude_code.constants import (
    CORRECTION_DONE_NAME,
    CORRECTION_DROP_NAME,
    CORRECTION_UNCLEAN_NAME,
)
from swe_lab.harnesses.claude_code.harness import user_event_line
from swe_lab.rollout import SUPERVISION_METRIC
from swe_lab.sandbox import Contribution, SandboxFs, SandboxObserver

from .supervisor import Intervention, Supervisor


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
  """

  supervisor: Supervisor
  channel: CorrectionChannel
  events_path: epath.Path
  failure: Exception | None = None
  interventions: list[Intervention] = field(default_factory=list)
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
      return spoken
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
      self.failure = error
      return 0


@final
@dataclass
class CorrectionChannelObserver(SandboxObserver):
  """Reports whether the run was supervised for the whole of its length.

  The producer for ``supervision.unhealthy``, which
  :func:`~swe_lab.rollout.rollout_outcome` reads to classify the run as
  ``SUPERVISION_FAILED``. Without it the metric would have a consumer and no
  producer, which is the same defect as a metric with a producer and no
  consumer, seen from the other side.

  Two conditions, one signal, because they are the same fact reached two ways:
  the pump stopped feeding the supervisor, or the channel closed without being
  told to. Either means the actor finished part of its work unsupervised.

  Attributes:
    pump: The supervisor pump for this run.
    channel: The correction channel it wrote through.
  """

  pump: SupervisorPump
  channel: CorrectionChannel

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Report the supervision failure, if there was one.

    Args:
      sb: Unused — both facts are already on the host.

    Returns:
      The metric when the run lost its supervisor, otherwise ``None`` — the
      metric is an event, so a healthy run leaves no key rather than a zero.
    """
    del sb
    lost = not self.pump.healthy or self.channel.closed_uncleanly
    return Contribution(metrics={SUPERVISION_METRIC: 1.0}) if lost else None
