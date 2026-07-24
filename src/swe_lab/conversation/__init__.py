"""The canonical conversation model + the producer seam every harness targets.

One provider-neutral, well-typed :class:`Conversation` (role-tagged messages of
``type``-discriminated content blocks) that harnesses convert their native agent
output into. A harness is a :class:`ConversationProducer` (it yields a
`Conversation` and names its native byproducts); the shared
:class:`ConversationObserver` runs the conversion and persists the result. See
``docs/horizontal/plans/task-06a-conversation-protocol.md``.
"""

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
from .observer import (
    CONVERSATION_NAME,
    ConversationObserver,
    ConversationProducer,
)

__all__ = [
    "CONVERSATION_NAME",
    "ContentBlock",
    "Conversation",
    "ConversationObserver",
    "ConversationProducer",
    "Message",
    "ReasoningBlock",
    "Role",
    "TextBlock",
    "ToolResultBlock",
    "ToolUseBlock",
]
