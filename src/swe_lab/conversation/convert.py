"""The converter seam: harness-native agent output → a canonical `Conversation`.

Each harness emits its output in its own native shape (Claude Code's is an
``event_stream`` of ``stream-json`` lines; Codex/Grok Build differ). A
``ConversationConverter`` maps one such shape into the shared
:class:`~swe_lab.conversation.model.Conversation`. One implementation per
harness; the shared :class:`~swe_lab.conversation.observer.ConversationObserver`
runs whichever it is given.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .model import Conversation


class ConversationConverter(ABC):
  """Convert a harness's native output file into a typed `Conversation`.

  A behavior interface (ABC, per ADR-0002): the engine and the shared observer
  depend on this contract, never on a concrete harness's format.
  """

  @abstractmethod
  def to_conversation(self, raw: Path) -> Conversation:
    """Read a harness-native output file and return a typed `Conversation`.

    Args:
      raw: Path to the harness's native output (may be absent — an agent that
        never started leaves no file).

    Returns:
      The converted conversation; an empty ``Conversation(messages=[])`` when
      there is nothing to convert.
    """
    ...
