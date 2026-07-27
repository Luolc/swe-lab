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
from swe_lab.sandbox import SandboxFs, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

EVENT_STREAM = "event_stream.jsonl"
STDERR = "agent.stderr"


@final
class _StubProducer(ConversationProducer):

  def __init__(self, conversation: Conversation) -> None:
    self._conversation = conversation
    self.seen: SandboxFs | None = None

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    self.seen = sb
    return self._conversation

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"event_stream": EVENT_STREAM, "agent_stderr": STDERR}


def _sandbox(workspace: Path) -> FakeSandbox:
  return FakeSandbox(
      spec=SandboxSpec("acme__widget-1", "img:tag", "/app", "abc"),
      workspace=workspace,
  )


def test_writes_conversation_and_registers_every_byproduct(tmp_path: Path):
  _ = (tmp_path / EVENT_STREAM).write_text('{"type":"x"}\n')
  _ = (tmp_path / STDERR).write_text("some stderr\n")
  conv = Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hi")])]
  )
  producer = _StubProducer(conv)
  observer = ConversationObserver(producer=producer)
  sb = _sandbox(tmp_path)

  contribution = observer.before_destroy(sb)

  assert producer.seen is sb  # the producer reads from the sandbox
  assert observer.conversation == conv
  written = tmp_path / CONVERSATION_NAME
  assert Conversation.model_validate_json(written.read_text()) == conv
  assert contribution is not None
  # Artifacts are now in-sandbox filenames, not host paths.
  assert contribution.artifacts["conversation"] == CONVERSATION_NAME
  assert contribution.artifacts["event_stream"] == EVENT_STREAM
  assert contribution.artifacts["agent_stderr"] == STDERR


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
