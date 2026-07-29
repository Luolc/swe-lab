"""Tests for the shared ConversationObserver (producer injected, no Docker)."""

from pathlib import Path
from typing import final, override

from etils import epath

from swe_lab.conversation import (
    Conversation,
    CONVERSATION_NAME,
    ConversationObserver,
    ConversationProducer,
    Message,
    Role,
    TextBlock,
)
from swe_lab.sandbox import SandboxFs, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox


@final
class _StubProducer(ConversationProducer):

  def __init__(self, conversation: Conversation) -> None:
    self._conversation = conversation
    self.seen: SandboxFs | None = None

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    self.seen = sb
    return self._conversation


def _sandbox(workspace: Path) -> FakeSandbox:
  return FakeSandbox(
      spec=SandboxSpec("acme__widget-1", "img:tag", "/app", "abc"),
      workspace=epath.Path(workspace),
  )


def test_writes_the_converted_conversation_and_registers_it(tmp_path: Path):
  conv = Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hi")])]
  )
  producer = _StubProducer(conv)
  observer = ConversationObserver(producer=producer)
  sb = _sandbox(tmp_path)

  contribution = observer.before_destroy(sb)

  assert producer.seen is sb  # the producer reads from the sandbox
  assert observer.conversation == conv
  assert contribution is not None
  # The JSON travels inline: this observer already parsed it, so writing it back
  # into the sandbox for the manager to fetch out would be two wasted transfers.
  inline = contribution.inline_artifacts["conversation"]
  assert inline.filename == CONVERSATION_NAME
  assert Conversation.model_validate_json(inline.content.decode()) == conv
  assert not (tmp_path / CONVERSATION_NAME).exists()  # no sandbox round trip
  # Only the conversion is this observer's: the producer's raw byproducts belong
  # to the harness-outcome observer, so one observer claims each artifact name.
  assert contribution.artifacts == {}
