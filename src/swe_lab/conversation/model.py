"""The canonical, provider-neutral conversation model.

One well-typed record every harness converts its native agent output into, so
nothing downstream has to parse a harness-specific shape. Modeled on the
sibling ``locode-core``'s ``locode-protocol`` (its ADR-0013) and the Anthropic
Python SDK's ``types`` — a uniform stream of role-tagged messages carrying
``type``-discriminated content blocks. We keep our own set (rather than import
the SDK's) so we control the surface and are not boxed in where upstream can't
reach a case we need.

The v0 block set is deliberately minimal (``text`` / ``reasoning`` /
``tool_use`` / ``tool_result``) — the shapes a coding agent emits. Adding a
block class later is non-breaking for consumers that switch on ``type``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Role(StrEnum):
  """Who a message is from.

  There is no separate ``system`` field: a ``SYSTEM`` message *is* the base
  prompt, keeping one uniform stream (as in ``locode-protocol``).
  """

  SYSTEM = "system"
  DEVELOPER = "developer"
  USER = "user"
  ASSISTANT = "assistant"


class TextBlock(BaseModel):
  """Plain text content."""

  type: Literal["text"] = "text"
  text: str


class ReasoningBlock(BaseModel):
  """Assistant reasoning (Anthropic "thinking").

  ``text`` is empty for redacted thinking; ``signature`` carries Anthropic's
  validator over ``text`` when the wire provides one.
  """

  type: Literal["reasoning"] = "reasoning"
  text: str
  signature: str | None = None


class ToolUseBlock(BaseModel):
  """A tool call emitted by the assistant.

  ``id`` is the provider-assigned id paired with a later ``ToolResultBlock``;
  ``name`` is the tool name; ``input`` is the arguments as arbitrary JSON.
  """

  type: Literal["tool_use"] = "tool_use"
  id: str
  name: str
  input: dict[str, Any]


class ToolResultBlock(BaseModel):
  """The result of a tool call, carried in a ``USER`` message.

  ``tool_use_id`` references the ``ToolUseBlock`` this answers; ``content`` is
  the result flattened to text for v0 (structured/image chunks are deferred);
  ``is_error`` marks a soft failure the model can recover from.
  """

  type: Literal["tool_result"] = "tool_result"
  tool_use_id: str
  content: str
  is_error: bool = False


type ContentBlock = Annotated[
    TextBlock | ReasoningBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]
"""One typed piece of message content, discriminated by its ``type`` tag."""


class Message(BaseModel):
  """One message: a role plus an ordered list of content blocks."""

  role: Role
  content: list[ContentBlock]


class Conversation(BaseModel):
  """A full conversation: one uniform stream of role-tagged messages."""

  messages: list[Message]
