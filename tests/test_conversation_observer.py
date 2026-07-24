"""Tests for the shared ConversationObserver (producer injected, no Docker)."""

from pathlib import Path
from typing import final, override

from swe_lab.conversation import (
    Conversation,
    CONVERSATION_NAME,
    ConversationObserver,
    ConversationProducer,
    Message,
    Role,
    TextBlock,
)
from swe_lab.sandbox import Sandbox, SandboxSpec
from swe_lab.sandbox.testing import FakeBackend

EVENT_STREAM = "event_stream.jsonl"
STDERR = "agent.stderr"


@final
class _StubProducer(ConversationProducer):

  def __init__(self, conversation: Conversation) -> None:
    self._conversation = conversation
    self.seen: Path | None = None

  @override
  def to_conversation(self, workspace: Path) -> Conversation:
    self.seen = workspace
    return self._conversation

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"event_stream": EVENT_STREAM, "agent_stderr": STDERR}


def _sandbox(workspace: Path) -> Sandbox:
  return Sandbox(
      label="acme__widget-1",
      spec=SandboxSpec("acme__widget-1", "img:tag", "/app", "abc"),
      workspace=workspace,
      backend=FakeBackend(),
      handle="fake",
  )


def test_writes_conversation_and_registers_every_byproduct(tmp_path: Path):
  _ = (tmp_path / EVENT_STREAM).write_text('{"type":"x"}\n')
  _ = (tmp_path / STDERR).write_text("some stderr\n")
  conv = Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hi")])]
  )
  producer = _StubProducer(conv)
  observer = ConversationObserver(producer=producer)

  contribution = observer.before_destroy(_sandbox(tmp_path))

  assert producer.seen == tmp_path  # the producer reads from the workspace
  assert observer.conversation == conv
  written = tmp_path / CONVERSATION_NAME
  assert Conversation.model_validate_json(written.read_text()) == conv
  assert contribution is not None
  assert contribution.artifacts["conversation"] == written
  assert contribution.artifacts["event_stream"] == tmp_path / EVENT_STREAM
  assert contribution.artifacts["agent_stderr"] == tmp_path / STDERR


def test_absent_byproducts_are_not_registered(tmp_path: Path):
  _ = (tmp_path / EVENT_STREAM).write_text('{"type":"x"}\n')  # stderr missing
  observer = ConversationObserver(
      producer=_StubProducer(Conversation(messages=[]))
  )

  contribution = observer.before_destroy(_sandbox(tmp_path))

  assert contribution is not None
  assert "event_stream" in contribution.artifacts
  assert "agent_stderr" not in contribution.artifacts  # missing file skipped
  assert (tmp_path / CONVERSATION_NAME).is_file()
