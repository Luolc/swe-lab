"""The shared observer that records a run's conversation + native byproducts.

Given a :class:`ConversationProducer` (a harness), the observer converts the
producer's primary output into the canonical :class:`Conversation` in
``before_destroy``, persists it as ``conversation.json``, and registers the
conversation plus every native byproduct the producer declares as artifacts (so
the persist observer uploads them). Only the injected producer is
harness-specific — nothing about a particular agent's format lives here. The
conversation package depends only on the ``ConversationProducer`` contract,
never on ``harnesses``, so the dependency runs one way (no import cycle).

Single-run (it holds the converted conversation as state): construct a fresh one
per run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import override

from swe_lab.sandbox import Contribution, Sandbox, SandboxObserver

from .model import Conversation

CONVERSATION_NAME = "conversation.json"
"""Workspace filename for the canonical conversation record."""


class ConversationProducer(ABC):
  """A run output that yields a `Conversation` and names its native byproducts.

  A behavior interface (ABC, per ADR-0002); a harness implements it. The
  observer depends only on this contract, never on a concrete harness.
  """

  @abstractmethod
  def to_conversation(self, workspace: Path) -> Conversation:
    """Read this producer's own output from the workspace into a `Conversation`.

    Args:
      workspace: The run's workspace directory; the producer reads its own
        native output files from it, by names only it knows.

    Returns:
      The converted conversation; an empty ``Conversation(messages=[])`` when
      there is nothing to convert.
    """
    ...

  @abstractmethod
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct this producer writes during a run.

    Returns:
      Artifact name → workspace-relative filename, for each file (the primary
      output and any logs) — registered as artifacts when it exists.
    """
    ...


@dataclass
class ConversationObserver(SandboxObserver):
  """Persist a producer's conversation and register all its native byproducts.

  Attributes:
    producer: The run's conversation producer (a harness).
    conversation: The converted conversation, set in ``before_destroy``
      (single-run state; ``None`` until then).
  """

  producer: ConversationProducer
  conversation: Conversation | None = None

  @override
  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Convert the primary output, persist it, and register every byproduct.

    Args:
      sb: The sandbox being torn down; only its workspace is read.

    Returns:
      A contribution referencing ``conversation.json`` plus every native
      byproduct the producer declared that actually landed.
    """
    self.conversation = self.producer.to_conversation(sb.workspace)
    destination = sb.workspace / CONVERSATION_NAME
    _ = destination.write_text(self.conversation.model_dump_json(indent=2))
    artifacts = {"conversation": destination}
    for name, filename in self.producer.native_outputs().items():
      path = sb.workspace / filename
      if path.is_file():  # only register what actually landed
        artifacts[name] = path
    return Contribution(artifacts=artifacts)
