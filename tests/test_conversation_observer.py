"""Tests for the shared ConversationObserver (converter injected, no Docker)."""

from pathlib import Path
from typing import final, override

from swe_lab.conversation import (
    Conversation,
    CONVERSATION_NAME,
    ConversationConverter,
    ConversationObserver,
    Message,
    Role,
    TextBlock,
)
from swe_lab.sandbox import Sandbox, SandboxSpec
from swe_lab.sandbox.testing import FakeBackend

RAW_NAME = "event_stream.jsonl"


@final
class _StubConverter(ConversationConverter):

  def __init__(self, conversation: Conversation) -> None:
    self._conversation = conversation
    self.seen: Path | None = None

  @override
  def to_conversation(self, raw: Path) -> Conversation:
    self.seen = raw
    return self._conversation


def _sandbox(workspace: Path) -> Sandbox:
  return Sandbox(
      label="acme__widget-1",
      spec=SandboxSpec("acme__widget-1", "img:tag", "/app", "abc"),
      workspace=workspace,
      backend=FakeBackend(),
      handle="fake",
  )


def test_writes_conversation_and_registers_artifacts(tmp_path: Path):
  raw = tmp_path / RAW_NAME
  _ = raw.write_text('{"type":"x"}\n')
  conv = Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hi")])]
  )
  converter = _StubConverter(conv)
  observer = ConversationObserver(converter=converter, raw_name=RAW_NAME)

  contribution = observer.before_destroy(_sandbox(tmp_path))

  assert converter.seen == raw
  assert observer.conversation == conv
  written = tmp_path / CONVERSATION_NAME
  assert Conversation.model_validate_json(written.read_text()) == conv
  assert contribution is not None
  assert contribution.artifacts["conversation"] == written
  assert contribution.artifacts["raw_output"] == raw


def test_absent_raw_output_still_writes_conversation(tmp_path: Path):
  observer = ConversationObserver(
      converter=_StubConverter(Conversation(messages=[])), raw_name=RAW_NAME
  )

  contribution = observer.before_destroy(_sandbox(tmp_path))

  assert contribution is not None
  assert "raw_output" not in contribution.artifacts
  assert (tmp_path / CONVERSATION_NAME).is_file()
