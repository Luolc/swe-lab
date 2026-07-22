"""The canonical conversation model + the converter seam every harness targets.

One provider-neutral, well-typed :class:`Conversation` (role-tagged messages of
``type``-discriminated content blocks) that harnesses convert their native agent
output into via a :class:`ConversationConverter`; the shared
:class:`ConversationObserver` runs the conversion and persists the result. See
``docs/horizontal/plans/task-06a-conversation-protocol.md``.
"""

from .convert import ConversationConverter
from .model import (
    ContentBlock,
    Conversation,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from .observer import CONVERSATION_NAME, ConversationObserver

__all__ = [
    "CONVERSATION_NAME",
    "ContentBlock",
    "Conversation",
    "ConversationConverter",
    "ConversationObserver",
    "Message",
    "ReasoningBlock",
    "Role",
    "TextBlock",
    "ToolResultBlock",
    "ToolUseBlock",
]
