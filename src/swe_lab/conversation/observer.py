"""The shared observer that records a run's conversation.

Harness-agnostic: given a :class:`ConversationConverter` plus the native output
filename the harness writes, it converts that output to the canonical
:class:`Conversation` in ``before_destroy``, persists it as
``conversation.json``, and registers both the conversation and the raw output
as artifacts. Only the injected converter is harness-specific — nothing about a
particular agent's format lives here. Single-run (it holds the converted
conversation as state): construct a fresh one per run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from swe_lab.sandbox import Contribution, Sandbox, SandboxObserver

from .convert import ConversationConverter
from .model import Conversation

CONVERSATION_NAME = "conversation.json"
"""Workspace filename for the canonical conversation record."""


@dataclass
class ConversationObserver(SandboxObserver):
  """Convert a harness's native output into ``conversation.json``.

  Attributes:
    converter: The harness's native-output → ``Conversation`` converter.
    raw_name: The workspace filename the harness writes its native output to.
    conversation: The converted conversation, set in ``before_destroy``
      (single-run state; ``None`` until then).
  """

  converter: ConversationConverter
  raw_name: str
  conversation: Conversation | None = None

  @override
  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Convert the native output, persist it, and register the artifacts.

    Args:
      sb: The sandbox being torn down; only its workspace is read.

    Returns:
      A contribution referencing ``conversation.json`` (and the raw output when
      the harness actually produced one).
    """
    raw = sb.workspace / self.raw_name
    self.conversation = self.converter.to_conversation(raw)
    destination = sb.workspace / CONVERSATION_NAME
    _ = destination.write_text(self.conversation.model_dump_json(indent=2))
    artifacts = {"conversation": destination}
    if raw.is_file():
      artifacts["raw_output"] = raw
    return Contribution(artifacts=artifacts)
