"""The harness contract: the agent plug a solving run composes over the engine.

A ``Harness`` is a ``ConversationProducer`` (it yields a ``Conversation`` and
names its native output files) that also supplies the run's **mounts** (its own
staged files), its read-only **assets** (fixed-path resources like the pinned
binary), and the **main action** (``run``). The engine never imports a concrete
harness — the rollout composition (task 07) calls these and wires them into a
manager + backend. Nothing dataset-specific lives here: a harness is agnostic to
the task (the prompt is the dataset's).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from swe_lab.conversation import ConversationProducer
from swe_lab.sandbox import Assets, Mounts, Sandbox


class Harness(ConversationProducer, ABC):
  """An off-the-shelf agent CLI plugged into the sandbox engine as a run body.

  A behavior interface (ABC, per ADR-0002): claude_code now, codex / grok_build
  next. It also inherits ``to_conversation`` + ``native_outputs`` from
  ``ConversationProducer``.
  """

  @abstractmethod
  def mounts(self, workdir: str) -> Mounts:
    """Return the harness's own files to stage into the workspace."""
    ...

  @abstractmethod
  def assets(self) -> Assets:
    """Return the read-only resources to place at fixed container paths."""
    ...

  @abstractmethod
  def run(self, sb: Sandbox, *, timeout: float) -> None:
    """Run the main action (the agent) in the live sandbox."""
    ...
