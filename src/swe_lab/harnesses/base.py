"""The harness contract: the agent plug a solving run composes over the engine.

A ``Harness`` is a ``ConversationProducer`` (it yields a ``Conversation`` and
names its native output files) that also supplies the run's **mounts** (its own
staged files, including any fixed-path read-only asset like the pinned binary)
and the **main action** (``run``). The engine never imports a concrete harness —
the rollout composition calls these and wires them into a manager + sandbox.
Nothing dataset-specific lives here: a harness is agnostic to the task (the
prompt is the dataset's).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from swe_lab.conversation import ConversationProducer
from swe_lab.sandbox import Mounts, SandboxFs


class Harness(ConversationProducer, ABC):
  """An off-the-shelf agent CLI plugged into the sandbox engine as a run body.

  A behavior interface (ABC, per ADR-0002). It also inherits ``to_conversation``
  and ``native_outputs`` from ``ConversationProducer``.
  """

  @abstractmethod
  def mounts(self, workdir: str) -> Mounts:
    """Return the harness's own files to stage (including any read-only asset).

    An asset (e.g. the pinned binary at a fixed absolute path) is just a
    read-only mount — there is no separate assets seam (ADR-0003).
    """
    ...

  @abstractmethod
  def run(self, sb: SandboxFs, *, timeout: float) -> None:
    """Run the main action (the agent) in the live sandbox."""
    ...
