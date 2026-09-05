"""Reusable selection, rendering, and prompt assembly for supervision."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
import dataclasses
import json
from typing import override, TYPE_CHECKING

from swe_lab.conversation import (
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

if TYPE_CHECKING:
  from swe_lab.trace_synthesis.criterion import Criterion
  from swe_lab.trace_synthesis.supervisor import Observation

#: A screenful of one tool input or result, matching the native supervisor's
#: per-record budget. The durable conversation remains complete.
DEFAULT_TOOL_VALUE_CHARS = 4_000

#: Visible prose is context around the observable tool evidence, so it gets a
#: smaller independent allowance rather than competing with tool output.
DEFAULT_VISIBLE_TEXT_CHARS = 1_000


class EvidenceSelector(ABC):
  """Select records for one model call without changing durable evidence."""

  @abstractmethod
  def select(
      self, records: Sequence[Message], *, limit: int
  ) -> tuple[Message, ...]:
    """Select a bounded recent view.

    Args:
      records: Actor evidence in chronological order.
      limit: Component-defined selection limit.

    Returns:
      Selected records in chronological order.
    """


@dataclasses.dataclass(frozen=True)
class CompleteAssistantTurnSelector(EvidenceSelector):
  """Select recent assistant turns together with their following results."""

  @override
  def select(
      self, records: Sequence[Message], *, limit: int
  ) -> tuple[Message, ...]:
    """Select the newest complete assistant-turn groups.

    A group starts at an assistant message and ends before the next assistant
    message. A zero limit retains the previous raw-slice behavior of selecting
    the complete input.

    Args:
      records: Actor evidence in chronological order.
      limit: Number of assistant turns to retain.

    Returns:
      Selected records in chronological order.
    """
    if not records or limit == 0:
      return tuple(records)
    assistant_starts = [
        index
        for index, record in enumerate(records)
        if record.role == Role.ASSISTANT
    ]
    if not assistant_starts:
      return tuple(records[-limit:])
    if limit >= len(assistant_starts):
      return tuple(records)
    return tuple(records[assistant_starts[-limit] :])


class EvidenceRenderer(ABC):
  """Render selected typed evidence for a model prompt."""

  @abstractmethod
  def render(self, records: Sequence[Message]) -> str:
    """Render records in chronological order."""


def _clip(text: str, keep: int) -> str:
  """Clip text with a marker that distinguishes truncation from short input."""
  if len(text) <= keep:
    return text
  return f"{text[:keep]} […{len(text) - keep} more characters not shown]"


@dataclasses.dataclass(frozen=True)
class _AssistantTurn:
  """One assistant record and the tool results before the next one."""

  assistant: Message | None
  results: tuple[ToolResultBlock, ...]


def _assistant_turns(records: Sequence[Message]) -> tuple[_AssistantTurn, ...]:
  """Group already-selected records for rendering."""
  turns: list[_AssistantTurn] = []
  assistant: Message | None = None
  results: list[ToolResultBlock] = []
  for record in records:
    if record.role == Role.ASSISTANT:
      if assistant is not None or results:
        turns.append(_AssistantTurn(assistant, tuple(results)))
      assistant = record
      results = [
          block
          for block in record.content
          if isinstance(block, ToolResultBlock)
      ]
      continue
    results.extend(
        block for block in record.content if isinstance(block, ToolResultBlock)
    )
  if assistant is not None or results:
    turns.append(_AssistantTurn(assistant, tuple(results)))
  return tuple(turns)


@dataclasses.dataclass(frozen=True)
class PairedToolEvidenceRenderer(EvidenceRenderer):
  """Render assistant turns with tool calls paired to results by stable ID.

  Attributes:
    include_visible_text: Whether assistant prose reaches the prompt.
    max_visible_text_chars: Character budget for one turn's visible prose.
    max_tool_input_chars: Character budget for one serialized tool input.
    max_tool_result_chars: Character budget for one tool result.
  """

  include_visible_text: bool = True
  max_visible_text_chars: int = DEFAULT_VISIBLE_TEXT_CHARS
  max_tool_input_chars: int = DEFAULT_TOOL_VALUE_CHARS
  max_tool_result_chars: int = DEFAULT_TOOL_VALUE_CHARS

  @override
  def render(self, records: Sequence[Message]) -> str:
    """Render complete pairs while omitting reasoning blocks.

    Args:
      records: Complete selected assistant-turn records.

    Returns:
      One prompt block, oldest turn first.
    """
    sections: list[str] = []
    for index, turn in enumerate(_assistant_turns(records), 1):
      lines = [f"## Assistant turn {index}"]
      assistant = turn.assistant
      tool_uses = (
          [
              block
              for block in assistant.content
              if isinstance(block, ToolUseBlock)
          ]
          if assistant is not None
          else []
      )
      if self.include_visible_text and assistant is not None:
        visible_text = "\n".join(
            block.text
            for block in assistant.content
            if isinstance(block, TextBlock)
        )
        if visible_text:
          lines.append(
              "Visible text: "
              + _clip(visible_text, self.max_visible_text_chars)
          )

      matched_result_indexes: set[int] = set()
      for tool_use in tool_uses:
        serialized_input = json.dumps(
            tool_use.input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        lines.append(
            f"Tool call {tool_use.id}: {tool_use.name} "
            f"{_clip(serialized_input, self.max_tool_input_chars)}"
        )
        matches = [
            (result_index, result)
            for result_index, result in enumerate(turn.results)
            if result.tool_use_id == tool_use.id
        ]
        if not matches:
          lines.append(f"Tool result {tool_use.id}: missing")
        for result_index, result in matches:
          matched_result_indexes.add(result_index)
          status = "error" if result.is_error else "success"
          lines.append(
              f"Tool result {result.tool_use_id}: {status} "
              f"{_clip(result.content, self.max_tool_result_chars)}"
          )

      for result_index, result in enumerate(turn.results):
        if result_index in matched_result_indexes:
          continue
        status = "error" if result.is_error else "success"
        lines.append(
            f"Tool result {result.tool_use_id} (unmatched): {status} "
            f"{_clip(result.content, self.max_tool_result_chars)}"
        )
      sections.append("\n".join(lines))
    return "\n\n".join(sections)


class PromptBuilder(ABC):
  """Assemble one model-facing prompt from bounded context components."""

  @abstractmethod
  def build(self, observation: Observation, criterion: Criterion) -> str:
    """Build a prompt from the policy-visible inputs."""


@dataclasses.dataclass(frozen=True)
class SupervisorPromptBuilder(PromptBuilder):
  """Assemble the established prompt sections with a replaceable renderer."""

  renderer: EvidenceRenderer = dataclasses.field(
      default_factory=PairedToolEvidenceRenderer
  )

  @override
  def build(self, observation: Observation, criterion: Criterion) -> str:
    """Build the user half of a judge or writer request.

    Args:
      observation: What the actor was asked and the policy-selected evidence.
      criterion: The standard handed in by the policy.

    Returns:
      The prompt text.
    """
    said = "\n".join(one.text for one in observation.said) or "(nothing yet)"
    done = self.renderer.render(observation.evidence)
    guidebook = (
        f"# Guidebook\n\n{observation.guidebook}\n\n"
        if observation.guidebook is not None
        else ""
    )
    return (
        f"# Criterion\n\n{criterion.text}\n\n"
        f"{guidebook}"
        f"# The task the engineer was given\n\n{observation.task}\n\n"
        f"# What they have done, most recent last\n\n{done}\n\n"
        f"# What you have already said to them\n\n{said}\n"
    )
