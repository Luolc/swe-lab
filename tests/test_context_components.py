"""Complete, bounded, and replaceable supervisor context components."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
from typing import Any, override

from swe_lab.conversation import (
    ContentBlock,
    Message,
    ReasoningBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from swe_lab.trace_synthesis.context_components import (
    CompleteAssistantTurnSelector,
    EvidenceRenderer,
    EvidenceSelector,
    PairedToolEvidenceRenderer,
    PromptBuilder,
)
from swe_lab.trace_synthesis.criterion import Criterion, load_criterion
from swe_lab.trace_synthesis.judge import supervising_policy
from swe_lab.trace_synthesis.supervisor import (
    Observation,
    SpeakWhenOffTrack,
    Verdict,
)


def assistant_call(call_id: str, *, text: str = "") -> Message:
  """Build one assistant tool call with optional visible framing text."""
  content: list[ContentBlock] = []
  if text:
    content.append(TextBlock(text=text))
  content.append(
      ToolUseBlock(id=call_id, name="Read", input={"path": f"{call_id}.py"})
  )
  return Message(role=Role.ASSISTANT, content=content)


def tool_result(
    call_id: str, content: str, *, is_error: bool = False
) -> Message:
  """Build one result record for an assistant tool call."""
  return Message(
      role=Role.USER,
      content=[
          ToolResultBlock(
              tool_use_id=call_id, content=content, is_error=is_error
          )
      ],
  )


def test_a_raw_record_boundary_never_splits_a_tool_call_from_its_result() -> (
    None
):
  """The window counts whole turns even when its edge bisects a raw pair.

  With a raw-record slice of one, the result is retained without its call.
  Selecting one assistant turn must instead retain both records of the newest
  pair, which is the control arm that distinguishes semantic grouping from
  ordinary list slicing.
  """
  records = (
      assistant_call("call-old"),
      tool_result("call-old", "old result"),
      assistant_call("call-new"),
      tool_result("call-new", "new result"),
  )

  selected = CompleteAssistantTurnSelector().select(records, limit=1)

  assert selected == records[-2:]
  assert selected != records[-1:]


def test_tool_evidence_is_rendered_while_reasoning_is_omitted() -> None:
  """Dropping reasoning cannot pass by dropping every non-text block.

  The positive tool-call and result assertions make a text-only renderer fail
  this test even though it also omits the reasoning sentinel.
  """
  records = (
      Message(
          role=Role.ASSISTANT,
          content=[
              ReasoningBlock(text="PRIVATE-REASONING-SENTINEL"),
              ToolUseBlock(
                  id="call-1", name="Read", input={"path": "models.py"}
              ),
          ],
      ),
      tool_result("call-1", "class Edition:\n  pass"),
  )

  rendered = PairedToolEvidenceRenderer().render(records)

  assert "PRIVATE-REASONING-SENTINEL" not in rendered
  assert 'Tool call call-1: Read {"path":"models.py"}' in rendered
  assert "Tool result call-1: success class Edition:\n  pass" in rendered


def test_a_genuinely_missing_result_is_marked() -> None:
  """An unanswered call differs visibly from a call with an empty result."""
  rendered = PairedToolEvidenceRenderer().render((assistant_call("call-1"),))

  assert "Tool result call-1: missing" in rendered


def test_error_status_and_content_are_both_rendered() -> None:
  """A failed tool is evidence, not an output string with lost status."""
  rendered = PairedToolEvidenceRenderer().render(
      (
          assistant_call("call-1"),
          tool_result("call-1", "permission denied", is_error=True),
      )
  )

  assert "Tool result call-1: error permission denied" in rendered


def test_truncation_is_visible_and_short_values_are_unmarked() -> None:
  """Clipped evidence cannot look identical to evidence originally short."""
  renderer = PairedToolEvidenceRenderer(max_tool_result_chars=5)

  short = renderer.render(
      (assistant_call("call-1"), tool_result("call-1", "short"))
  )
  long = renderer.render(
      (assistant_call("call-1"), tool_result("call-1", "longer result"))
  )

  assert "not shown" not in short
  assert "longer" not in long
  assert "[…8 more characters not shown]" in long


def test_oversized_structured_input_has_its_own_visible_marker() -> None:
  """Tool input clipping is independent of result-output clipping."""
  records = (
      Message(
          role=Role.ASSISTANT,
          content=[
              ToolUseBlock(
                  id="call-1", name="Read", input={"path": "a-long-path.py"}
              )
          ],
      ),
      tool_result("call-1", "short"),
  )

  rendered = PairedToolEvidenceRenderer(max_tool_input_chars=10).render(records)

  expected = 'Tool call call-1: Read {"path":"a […15 more characters not shown]'
  assert expected in rendered
  assert "Tool result call-1: success short" in rendered


def test_visible_text_can_be_bounded_or_disabled_without_hiding_tools() -> None:
  """The prose control is independent of the positive tool evidence."""
  records = (
      assistant_call("call-1", text="explain this call"),
      tool_result("call-1", "result"),
  )

  bounded = PairedToolEvidenceRenderer(max_visible_text_chars=7).render(records)
  disabled = PairedToolEvidenceRenderer(include_visible_text=False).render(
      records
  )

  assert "Visible text: explain […10 more characters not shown]" in bounded
  assert "Visible text:" not in disabled
  assert "Tool call call-1" in disabled
  assert "Tool result call-1" in disabled


@dataclasses.dataclass(frozen=True)
class FirstRecordSelector(EvidenceSelector):
  """Select the oldest raw record to prove the policy uses the seam."""

  @override
  def select(
      self, records: Sequence[Message], *, limit: int
  ) -> tuple[Message, ...]:
    """Return the oldest record regardless of the configured limit."""
    del limit
    return tuple(records[:1])


@dataclasses.dataclass
class RecordingJudge:
  """Record the policy-selected observation and stay on track."""

  observations: list[Observation] = dataclasses.field(default_factory=list)

  def __call__(self, observation: Observation, criterion: Criterion) -> Verdict:
    """Record one observation and return an on-track verdict."""
    del criterion
    self.observations.append(observation)
    return Verdict(off_track=False, self_correcting=False)


def test_a_policy_can_replace_only_the_selector() -> None:
  """The standard policy state machine consumes an injected selector."""
  judge = RecordingJudge()
  records = (
      Message(role=Role.ASSISTANT, content=[TextBlock(text="oldest")]),
      Message(role=Role.ASSISTANT, content=[TextBlock(text="newest")]),
  )
  policy = SpeakWhenOffTrack(
      judge=judge,
      writer=lambda observation, criterion: "unused",
      criterion=load_criterion(),
      budget=0,
      window=1,
      selector=FirstRecordSelector(),
  )

  policy.consider(Observation(task="task", evidence=records, cursor=2, said=()))

  assert judge.observations[0].evidence == records[:1]


@dataclasses.dataclass(frozen=True)
class SentinelPromptBuilder(PromptBuilder):
  """Return a fixed prompt to prove model calls use the public seam."""

  @override
  def build(self, observation: Observation, criterion: Criterion) -> str:
    """Return a sentinel independent of the supplied values."""
    del observation, criterion
    return "CUSTOM-PROMPT-BUILDER"


@dataclasses.dataclass(frozen=True)
class SentinelRenderer(EvidenceRenderer):
  """Render one sentinel to prove the default builder keeps its seam."""

  @override
  def render(self, records: Sequence[Message]) -> str:
    """Return a sentinel independent of the selected evidence."""
    del records
    return "CUSTOM-RENDERER"


def test_the_standard_policy_can_replace_only_the_prompt_builder() -> None:
  """Prompt assembly is replaceable without replacing policy or decoding."""
  payloads: list[dict[str, Any]] = []

  def transport(payload: Mapping[str, Any]) -> dict[str, Any]:
    payloads.append(dict(payload))
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "submit_supervision_verdict",
                "input": {
                    "off_track": False,
                    "self_correcting": False,
                    "reason": "fine",
                },
            }
        ]
    }

  policy = supervising_policy(
      model="m",
      transport=transport,
      budget=0,
      prompt_builder=SentinelPromptBuilder(),
  )

  result = policy.consider(
      Observation(
          task="task",
          evidence=(
              Message(
                  role=Role.ASSISTANT,
                  content=[TextBlock(text="EVIDENCE-SENTINEL-9f31")],
              ),
          ),
          cursor=1,
          said=(),
      ),
  )

  assert result is None
  assert payloads[0]["messages"][0]["content"] == "CUSTOM-PROMPT-BUILDER"


def test_the_standard_policy_can_replace_only_the_renderer() -> None:
  """The default prompt builder accepts an independently injected renderer."""
  payloads: list[dict[str, Any]] = []

  def transport(payload: Mapping[str, Any]) -> dict[str, Any]:
    payloads.append(dict(payload))
    return {
        "content": [
            {
                "type": "tool_use",
                "name": "submit_supervision_verdict",
                "input": {
                    "off_track": False,
                    "self_correcting": False,
                    "reason": "fine",
                },
            }
        ]
    }

  policy = supervising_policy(
      model="m", transport=transport, budget=0, renderer=SentinelRenderer()
  )

  policy.consider(
      Observation(
          task="task",
          evidence=(
              Message(
                  role=Role.ASSISTANT,
                  content=[TextBlock(text="EVIDENCE-SENTINEL-9f31")],
              ),
          ),
          cursor=1,
          said=(),
      )
  )

  assert "CUSTOM-RENDERER" in payloads[0]["messages"][0]["content"]
  assert "EVIDENCE-SENTINEL-9f31" not in (payloads[0]["messages"][0]["content"])
