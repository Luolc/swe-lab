"""Tests for the canonical conversation model (round-trip + discrimination)."""

from swe_lab.conversation import (
    Conversation,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _sample() -> Conversation:
  return Conversation(
      messages=[
          Message(role=Role.SYSTEM, content=[TextBlock(text="be terse")]),
          Message(
              role=Role.ASSISTANT,
              content=[
                  ReasoningBlock(text="think", signature="sig"),
                  TextBlock(text="running a tool"),
                  ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"}),
              ],
          ),
          Message(
              role=Role.USER,
              content=[ToolResultBlock(tool_use_id="t1", content="a.py b.py")],
          ),
      ]
  )


def test_round_trips_through_json():
  original = _sample()
  restored = Conversation.model_validate_json(original.model_dump_json())
  assert restored == original


def test_blocks_discriminate_on_type():
  restored = Conversation.model_validate_json(_sample().model_dump_json())
  assistant = restored.messages[1].content
  assert isinstance(assistant[0], ReasoningBlock)
  assert isinstance(assistant[2], ToolUseBlock)
  assert assistant[2].input == {"cmd": "ls"}
  result = restored.messages[2].content[0]
  assert isinstance(result, ToolResultBlock)
  assert result.tool_use_id == "t1"
  assert result.is_error is False


def test_tool_result_error_flag_survives():
  conv = Conversation(
      messages=[
          Message(
              role=Role.USER,
              content=[
                  ToolResultBlock(
                      tool_use_id="x", content="boom", is_error=True
                  )
              ],
          )
      ]
  )
  restored = Conversation.model_validate_json(conv.model_dump_json())
  block = restored.messages[0].content[0]
  assert isinstance(block, ToolResultBlock)
  assert block.is_error is True


def test_empty_conversation_round_trips():
  conv = Conversation(messages=[])
  assert Conversation.model_validate_json(conv.model_dump_json()) == conv
