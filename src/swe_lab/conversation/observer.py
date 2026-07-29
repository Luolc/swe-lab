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
from typing import override

from swe_lab.sandbox import Contribution, SandboxFs, SandboxObserver

from .model import Conversation

CONVERSATION_NAME = "conversation.json"
"""Workspace filename for the canonical conversation record."""


class ConversationProducer(ABC):
  """A run output that can be read back as a `Conversation`.

  A behavior interface (ABC, per ADR-0002); a harness implements it. The
  observer depends only on this contract, never on a concrete harness. Purely
  about *conversion* — collecting the run's raw byproducts is the harness's own
  concern (``Harness.native_outputs``, via ``HarnessOutcomeObserver``).
  """

  @abstractmethod
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    """Read this producer's own output from the sandbox into a `Conversation`.

    Args:
      sb: The live sandbox; the producer reads its own native output files
        from it, by names only it knows.

    Returns:
      The converted conversation; an empty ``Conversation(messages=[])`` when
      there is nothing to convert.
    """
    ...


@dataclass
class ConversationObserver(SandboxObserver):
  """Convert a producer's output into the canonical ``Conversation``.

  Scoped to the *conversion*: the producer's raw byproducts (its trace file, its
  logs) are collected by ``HarnessOutcomeObserver`` instead, so exactly one
  observer claims each artifact name.

  Attributes:
    producer: The run's conversation producer (a harness).
    conversation: The converted conversation, set in ``before_destroy``
      (single-run state; ``None`` until then).
  """

  producer: ConversationProducer
  conversation: Conversation | None = None

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Convert the producer's output, persist it, and register the result.

    Args:
      sb: The sandbox being torn down; its files are read/written.

    Returns:
      A contribution referencing the written ``conversation.json``.
    """
    self.conversation = self.producer.to_conversation(sb)
    sb.write(
        CONVERSATION_NAME,
        self.conversation.model_dump_json(indent=2).encode("utf-8"),
    )
    return Contribution(artifacts={"conversation": CONVERSATION_NAME})
