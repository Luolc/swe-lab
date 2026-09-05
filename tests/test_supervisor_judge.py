"""The judge and writer, and the attack that must fail for each guarantee.

Every "cannot" in :mod:`swe_lab.trace_synthesis.judge` is written here as the
attack that would break it: a guard that has not been run against its own
defect is not a guard.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import pathlib
from typing import Any
from unittest import mock

import pytest

from swe_lab.conversation import Message, Role, TextBlock
from swe_lab.trace_synthesis.criterion import (
    Criterion,
    CRITERION_PATH,
    CriterionRejectedError,
    load_criterion,
)
from swe_lab.trace_synthesis.judge import (
    Call,
    JUDGE_INSTRUCTIONS,
    JUDGE_TOOL_NAME,
    JudgeAnswerError,
    LOCATE_DEVIATION_INSTRUCTION,
    messages_transport,
    ModelJudge,
    ModelWriter,
    SAMPLING_KEYS,
    supervising_policy,
)
from swe_lab.trace_synthesis.supervisor import (
    Intervention,
    InterventionTooLongError,
    MAX_INTERVENTION_CHARS,
    Observation,
    PolicyLapseError,
    Supervisor,
    Verdict,
    WriterOutputRejectedError,
)

OFF_TRACK_JSON = (
    '{"off_track": true, "self_correcting": false, "reason": "guessing"}'
)
ON_TRACK_JSON = (
    '{"off_track": false, "self_correcting": false, "reason": "fine"}'
)


def observation(
    cursor: int = 1,
    *,
    task: str = "make the test pass",
    guidebook: str | None = None,
) -> Observation:
  """Build an observation.

  Args:
    cursor: How many events have been consumed.
    task: The brief handed to the supervisor.
    guidebook: The phase-B artifact, when this is a guided run.

  Returns:
    An observation a judge can be handed.
  """
  return Observation(
      task=task,
      evidence=(
          Message(
              role=Role.ASSISTANT, content=[TextBlock(text="editing blind")]
          ),
      ),
      cursor=cursor,
      said=(),
      guidebook=guidebook,
  )


@dataclasses.dataclass
class RecordingTransport:
  """A transport that answers from a script and keeps every payload.

  Attributes:
    answers: The contents to return, in order; the last repeats.
    model: The model the response reports, which need not be the one asked for.
    finish_reason: The Anthropic ``stop_reason`` to report, or ``None`` to
      omit it — matching a provider response that carries none.
    payloads: Every request body sent.
  """

  answers: list[str | list[dict[str, Any]]]
  model: str = "served/model-x"
  finish_reason: str | None = None
  payloads: list[dict[str, Any]] = dataclasses.field(default_factory=list)

  def __call__(self, payload: Any) -> dict[str, Any]:
    """Record the request and answer from the script.

    Args:
      payload: The request body.

    Returns:
      A response in the provider's shape.
    """
    self.payloads.append(dict(payload))
    index = min(len(self.payloads) - 1, len(self.answers) - 1)
    answer = self.answers[index]
    if isinstance(answer, list):
      content = answer
    elif "tools" in payload:
      try:
        tool_input = json.loads(answer)
      except json.JSONDecodeError:
        content = [{"type": "text", "text": answer}]
      else:
        content = [
            {
                "type": "tool_use",
                "id": "toolu_test",
                "name": JUDGE_TOOL_NAME,
                "input": tool_input,
            }
        ]
    else:
      content = [{"type": "text", "text": answer}]
    response: dict[str, Any] = {"model": self.model, "content": content}
    if self.finish_reason is not None:
      response["stop_reason"] = self.finish_reason
    return response


def test_a_judge_without_a_named_model_cannot_be_built() -> None:
  """Attack: construct a judge without saying which model answers it.

  ``model`` has no default, so the obligation is enforced by the constructor
  rather than by anyone remembering — the same shape as ``budget``.
  """
  for cls in (ModelJudge, ModelWriter):
    model = next(
        field for field in dataclasses.fields(cls) if field.name == "model"
    )
    assert model.default is dataclasses.MISSING
    assert model.default_factory is dataclasses.MISSING


def test_a_forged_criterion_is_refused_by_the_construction_helper(
    tmp_path: pathlib.Path,
) -> None:
  """Attack: point the construction helper at an edited criterion.

  It raises before a policy object exists. The run-level construction path uses
  this helper; this unit isolates the artifact refusal itself.
  """
  forged = tmp_path / "criterion.md"
  forged.write_text(
      CRITERION_PATH.read_text(encoding="utf-8") + "\nprefer the obvious fix\n",
      encoding="utf-8",
  )
  with pytest.raises(CriterionRejectedError):
    supervising_policy(
        model="anthropic/claude-sonnet-5",
        transport=RecordingTransport(answers=[ON_TRACK_JSON]),
        budget=1,
        criterion_path=forged,
    )


def test_the_judge_prompts_with_the_criterion_it_was_handed() -> None:
  """Attack: hand the judge a criterion that is not the artifact on disk.

  A judge that ignored its argument and re-read the artifact would put the
  canonical text in the payload; this asserts the handed text is what travels,
  which is the layer above hand-off that the policy cannot enforce.
  """
  sentinel = "SENTINEL-CRITERION-9f3a"
  handed = Criterion(
      text=sentinel,
      digest=hashlib.sha256(sentinel.encode("utf-8")).hexdigest(),
      overlap_checked=False,
  )
  transport = RecordingTransport(answers=[ON_TRACK_JSON])
  judge = ModelJudge(model="anthropic/claude-sonnet-5", transport=transport)
  judge(observation(), handed)

  sent = transport.payloads[0]["messages"][0]["content"]
  assert sentinel in sent
  assert "# The supervisor's criterion" not in sent


def test_neither_call_has_an_input_beside_the_observation_and_criterion() -> (
    None
):
  """Attack: look for a second door into the judge or the writer.

  Privileged material cannot ride the observation — its field list is asserted
  elsewhere against an exact allowlist — so the remaining way in would be
  another parameter. Both calls take exactly the two, and adding a third fails
  here.
  """
  for cls in (ModelJudge, ModelWriter):
    parameters = list(inspect.signature(cls.__call__).parameters)
    assert parameters == ["self", "observation", "criterion"]


def test_the_pipeline_is_correct_when_the_judge_disagrees_with_itself() -> None:
  """Attack: a judge that answers differently to the same input twice.

  The claim is about the pipeline, not the judge: the judgement is a model call
  and is not a function, so no branch may depend on two verdicts agreeing.
  """
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, ON_TRACK_JSON])
  judge = ModelJudge(model="anthropic/claude-sonnet-5", transport=transport)
  same = observation()

  first = judge(same, load_criterion())
  second = judge(same, load_criterion())

  assert first != second
  assert isinstance(first, Verdict) and isinstance(second, Verdict)
  assert len(judge.calls) == 2


def test_every_call_records_what_answered_it_and_what_was_not_sent() -> None:
  """The response's own model id, and every sampling key including the unset.

  Without the first, "the same model disagreed with itself" and "the alias
  moved" cannot be told apart; without the second, disagreement cannot be told
  from ordinary sampling. `ModelWriter` shares this provenance with
  `ModelJudge` — the same `Call` record, filled in the same way — so both are
  exercised here: a test that only drove `ModelJudge` would stay green after
  `ModelWriter` silently stopped recording its half.
  """
  transport = RecordingTransport(
      answers=[ON_TRACK_JSON], model="served/actual", finish_reason="stop"
  )
  judge = ModelJudge(model="requested/alias", transport=transport)
  judge(observation(), load_criterion())

  call = judge.calls[0]
  assert isinstance(call, Call)
  assert call.requested_model == "requested/alias"
  assert call.response_model == "served/actual"
  assert set(call.sampling_sent) == set(SAMPLING_KEYS)
  assert call.sampling_sent["temperature"] is None
  assert call.sampling_sent["max_tokens"] == judge.max_tokens
  assert call.finish_reason == "stop"

  writer_transport = RecordingTransport(
      answers=["look at the error"],
      model="served/actual",
      finish_reason="stop",
  )
  writer = ModelWriter(model="requested/alias", transport=writer_transport)
  writer(observation(), load_criterion())

  writer_call = writer.calls[0]
  assert isinstance(writer_call, Call)
  assert writer_call.finish_reason == "stop"


def test_model_calls_use_the_anthropic_messages_wire_shape() -> None:
  """The judge sends the exact native tool contract and forces its use."""
  transport = RecordingTransport(answers=[ON_TRACK_JSON])
  judge = ModelJudge(model="claude-sonnet-5", transport=transport)

  _ = judge(observation(), load_criterion())

  payload = transport.payloads[0]
  assert payload == {
      "model": "claude-sonnet-5",
      "max_tokens": judge.max_tokens,
      "system": JUDGE_INSTRUCTIONS,
      "messages": [
          {
              "role": "user",
              "content": payload["messages"][0]["content"],
          }
      ],
      "tools": [
          {
              "name": "submit_supervision_verdict",
              "description": "Submit the supervision verdict.",
              "input_schema": {
                  "type": "object",
                  "properties": {
                      "off_track": {"type": "boolean"},
                      "self_correcting": {"type": "boolean"},
                      "reason": {"type": "string"},
                      "deviation_started_steps_ago": {
                          "anyOf": [{"type": "integer"}, {"type": "null"}]
                      },
                  },
                  "required": ["off_track", "self_correcting", "reason"],
                  "additionalProperties": False,
              },
          }
      ],
      "tool_choice": {
          "type": "tool",
          "name": "submit_supervision_verdict",
      },
  }


def test_one_matching_tool_use_constructs_a_verdict() -> None:
  """A valid matching tool call is the judge's only usable answer shape."""
  judge = ModelJudge(
      model="m", transport=RecordingTransport(answers=[OFF_TRACK_JSON])
  )

  verdict = judge(observation(), load_criterion())

  assert verdict.off_track is True
  assert verdict.self_correcting is False
  assert verdict.reason == "guessing"
  assert judge.calls[0].raw == [
      {
          "type": "tool_use",
          "id": "toolu_test",
          "name": "submit_supervision_verdict",
          "input": {
              "off_track": True,
              "self_correcting": False,
              "reason": "guessing",
          },
      }
  ]


def test_a_text_only_judge_answer_is_unusable() -> None:
  """Free-form text is never parsed as a fallback verdict."""
  transport = RecordingTransport(answers=["not a tool call"])

  with pytest.raises(JudgeAnswerError, match="exactly one"):
    ModelJudge(model="m", transport=transport)(observation(), load_criterion())


def test_a_missing_tool_call_is_unusable() -> None:
  """A completed response with no content cannot become a verdict."""
  transport = RecordingTransport(answers=[[]])

  with pytest.raises(JudgeAnswerError, match="exactly one"):
    ModelJudge(model="m", transport=transport)(observation(), load_criterion())


def test_duplicate_matching_tool_calls_are_unusable() -> None:
  """Taking the first matching call would silently weaken exactly-one."""
  tool_use = {
      "type": "tool_use",
      "id": "toolu_one",
      "name": "submit_supervision_verdict",
      "input": {
          "off_track": False,
          "self_correcting": False,
          "reason": "fine",
      },
  }
  transport = RecordingTransport(answers=[[tool_use, dict(tool_use)]])

  with pytest.raises(JudgeAnswerError, match="exactly one"):
    ModelJudge(model="m", transport=transport)(observation(), load_criterion())


def test_a_tool_call_with_the_wrong_name_is_unusable() -> None:
  """Only the tool declared by the judge may carry its verdict."""
  transport = RecordingTransport(
      answers=[
          [
              {
                  "type": "tool_use",
                  "id": "toolu_wrong",
                  "name": "other_tool",
                  "input": {
                      "off_track": False,
                      "self_correcting": False,
                      "reason": "fine",
                  },
              }
          ]
      ]
  )

  with pytest.raises(JudgeAnswerError, match="exactly one"):
    ModelJudge(model="m", transport=transport)(observation(), load_criterion())


@pytest.mark.parametrize(
    "tool_input",
    [
        {"off_track": False, "self_correcting": False},
        {
            "off_track": False,
            "self_correcting": False,
            "reason": "fine",
            "unexpected": "field",
        },
        {"off_track": 0, "self_correcting": False, "reason": "fine"},
        {"off_track": False, "self_correcting": 0, "reason": "fine"},
        {"off_track": False, "self_correcting": False, "reason": 1},
        {
            "off_track": False,
            "self_correcting": False,
            "reason": "fine",
            "deviation_started_steps_ago": "3",
        },
        [False, False, "fine"],
    ],
)
def test_malformed_tool_input_is_unusable(tool_input: Any) -> None:
  """Local validation rejects malformed input even if a gateway does not."""
  transport = RecordingTransport(
      answers=[
          [
              {
                  "type": "tool_use",
                  "id": "toolu_bad_input",
                  "name": "submit_supervision_verdict",
                  "input": tool_input,
              }
          ]
      ]
  )

  with pytest.raises(JudgeAnswerError, match="unusable judge answer"):
    ModelJudge(model="m", transport=transport)(observation(), load_criterion())


def test_boolean_deviation_start_is_unusable_not_an_integer() -> None:
  """A boolean cannot wear an integer measurement's clothes."""
  answer = (
      '{"off_track": true, "self_correcting": false, "reason": "guessing",'
      ' "deviation_started_steps_ago": true}'
  )

  with pytest.raises(JudgeAnswerError, match="integer or null"):
    ModelJudge(model="m", transport=RecordingTransport(answers=[answer]))(
        observation(), load_criterion()
    )


def test_an_unusable_judge_answer_is_never_retried() -> None:
  """A second ask would make the verdict a function of how often we asked."""
  transport = RecordingTransport(answers=["not json at all"])
  judge = ModelJudge(model="anthropic/claude-sonnet-5", transport=transport)

  with pytest.raises(JudgeAnswerError):
    judge(observation(), load_criterion())
  assert len(transport.payloads) == 1


def test_the_writer_ignores_a_leading_non_text_block() -> None:
  """A usable text block need not be the response's first block."""
  content = [
      {"type": "thinking", "thinking": "consider the evidence"},
      {"type": "text", "text": "Check the failed assertion before editing."},
  ]
  writer = ModelWriter(
      model="m", transport=RecordingTransport(answers=[content])
  )

  line = writer(observation(), load_criterion())

  assert line == "Check the failed assertion before editing."


def test_a_missing_writer_text_block_is_one_lapse_and_is_never_retried() -> (
    None
):
  """A completed response without text is bounded to this one boundary."""
  transport = RecordingTransport(
      answers=[
          OFF_TRACK_JSON,
          [{"type": "thinking", "thinking": "no final answer"}],
      ]
  )
  policy = supervising_policy(model="m", transport=transport, budget=1)

  with pytest.raises(PolicyLapseError, match="writer produced no usable line"):
    policy.consider(observation())

  assert len(transport.payloads) == 2


def test_duplicate_writer_text_blocks_are_unusable() -> None:
  """Taking the first text block would silently weaken exactly-one."""
  transport = RecordingTransport(
      answers=[
          [
              {"type": "text", "text": "first"},
              {"type": "text", "text": "second"},
          ]
      ]
  )

  with pytest.raises(ValueError, match="expected exactly one text block"):
    ModelWriter(model="m", transport=transport)(observation(), load_criterion())


def test_a_non_string_writer_text_block_is_unusable() -> None:
  """A typed text block must carry a string rather than a coerced value."""
  transport = RecordingTransport(answers=[[{"type": "text", "text": 7}]])

  with pytest.raises(ValueError, match="text must be a string, got int"):
    ModelWriter(model="m", transport=transport)(observation(), load_criterion())


def test_writer_content_must_be_a_list() -> None:
  """The writer validates the response container before selecting a block."""
  writer = ModelWriter(
      model="m",
      transport=lambda _: {"content": {"type": "text", "text": "line"}},
  )

  with pytest.raises(ValueError, match="expected a content list"):
    writer(observation(), load_criterion())


def test_an_unusable_writer_answer_keeps_raw_response_provenance() -> None:
  """Extraction failure cannot erase the response that explains the lapse."""
  content = [
      {"type": "thinking", "thinking": "considering"},
      {"type": "text", "text": 7},
  ]
  writer = ModelWriter(
      model="m", transport=RecordingTransport(answers=[content])
  )

  with pytest.raises(ValueError):
    writer(observation(), load_criterion())

  assert writer.calls[0].raw == content


def test_an_unusable_writer_answer_keeps_the_stop_reason() -> None:
  """The response ending remains readable when text extraction fails."""
  writer = ModelWriter(
      model="m",
      transport=RecordingTransport(
          answers=[[{"type": "text", "text": 7}]],
          finish_reason="max_tokens",
      ),
  )

  with pytest.raises(ValueError):
    writer(observation(), load_criterion())

  assert writer.calls[0].finish_reason == "max_tokens"


def test_a_token_budget_lapse_is_recorded_differently_from_a_bad_answer() -> (
    None
):
  """`supervisor.jsonl` must not fold these two failures into one lapse.

  Both are `JudgeAnswerError`s the policy bounds to a `PolicyLapseError`, but
  they call for different fixes: `finish_reason == "max_tokens"` means our own
  `max_tokens` ran out before the model could finish, while any other value
  means the model finished normally and still produced an answer this could
  not use. Before issue #383, `supervisor.jsonl` recorded both the same way —
  which is how 85/85 lapses in a 902-call replay all turned out to be the
  former without anyone noticing from the log alone.
  """

  def lapse_row(transport: RecordingTransport) -> dict[str, object]:
    policy = supervising_policy(
        model="anthropic/claude-sonnet-5", transport=transport, budget=1
    )
    rows: list[dict[str, object]] = []
    supervisor = Supervisor(
        policy=policy,
        task="make the test pass",
        sink=lambda _: None,
        log=lambda row: rows.append(dict(row)),
    )
    supervisor.observe(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "editing blind"}],
            },
        }
    )
    assert len(rows) == 1
    return rows[0]

  budget_row = lapse_row(
      RecordingTransport(
          answers=['{"off_track": tru'], finish_reason="max_tokens"
      )
  )
  bad_answer_row = lapse_row(
      RecordingTransport(answers=["not json at all"], finish_reason="stop")
  )

  assert budget_row["kind"] == "lapse"
  assert bad_answer_row["kind"] == "lapse"
  assert budget_row["finish_reason"] == "max_tokens"
  assert bad_answer_row["finish_reason"] == "stop"
  assert budget_row["finish_reason"] != bad_answer_row["finish_reason"]


def test_an_over_long_line_from_the_writer_is_rejected_not_truncated() -> None:
  """The writer may produce anything; the intervention is what refuses it.

  The refusal reaches the supervisor bounded to this boundary, and the cause
  travels with it — so the record still says the line was rejected for its
  length rather than trimmed to fit.
  """
  transport = RecordingTransport(answers=["x" * (MAX_INTERVENTION_CHARS + 1)])
  policy = supervising_policy(
      model="anthropic/claude-sonnet-5",
      transport=transport,
      budget=1,
      cooldown=0,
  )
  policy.judge = ModelJudge(
      model="anthropic/claude-sonnet-5",
      transport=RecordingTransport(answers=[OFF_TRACK_JSON]),
  )

  with pytest.raises(PolicyLapseError) as raised:
    policy.consider(observation())
  assert isinstance(raised.value.__cause__, InterventionTooLongError)
  assert _accepted_writer_line("x" * MAX_INTERVENTION_CHARS)


def _writer_lapse(text: str, *, guidebook: str | None = None) -> Exception:
  """Return the bounded cause produced by one off-track writer answer."""
  policy = supervising_policy(
      model="model",
      transport=RecordingTransport(answers=[OFF_TRACK_JSON, text]),
      budget=1,
      cooldown=0,
  )
  with pytest.raises(PolicyLapseError) as raised:
    policy.consider(observation(guidebook=guidebook))
  assert isinstance(raised.value.__cause__, Exception)
  return raised.value.__cause__


def test_a_fenced_code_block_from_the_writer_is_rejected() -> None:
  """A code fence is independently rejected after a usable judge answer."""
  cause = _writer_lapse("Maybe compare this:\n```python\nreturn 1\n```")
  assert isinstance(cause, WriterOutputRejectedError)
  assert "fenced code" in str(cause)
  assert _accepted_writer_line("Maybe inspect `value = parse(raw)` again.")


def test_a_blockquoted_fenced_code_block_is_still_rejected() -> None:
  """Markdown quote prefixes do not disguise a fenced answer."""
  cause = _writer_lapse("> context\n> ```python\n> return 1\n> ```")
  assert isinstance(cause, WriterOutputRejectedError)
  assert "fenced code" in str(cause)


def test_a_diff_hunk_from_the_writer_is_rejected() -> None:
  """A diff hunk is independently rejected without relying on a code fence."""
  cause = _writer_lapse("Look here:\n@@ -12,2 +12,2 @@\n reconsider it")
  assert isinstance(cause, WriterOutputRejectedError)
  assert "diff hunk" in str(cause)
  assert _accepted_writer_line("Could the diff point to an earlier assumption?")


def test_a_combined_diff_hunk_from_the_writer_is_rejected() -> None:
  """A combined diff's three-at-sign header is still a hunk header."""
  cause = _writer_lapse("Look here:\n@@@ -12,2 -20,2 +12,2 @@@\nreconsider it")
  assert isinstance(cause, WriterOutputRejectedError)
  assert "diff hunk" in str(cause)


def test_an_eight_word_guidebook_copy_from_the_writer_is_rejected() -> None:
  """Verbatim guidebook copying is rejected without parsing its sections."""
  copied = "compare the parsed value against the original request boundary"
  cause = _writer_lapse(
      f"Perhaps {copied} before continuing.",
      guidebook=f"**Justification.** You can {copied} to explain the failure.",
  )
  assert isinstance(cause, WriterOutputRejectedError)
  assert "eight-word guidebook shingle" in str(cause)
  assert _accepted_writer_line(
      "compare the parsed value against the original",
      guidebook=f"You can {copied} to explain the failure.",
  )


def _accepted_writer_line(text: str, *, guidebook: str | None = None) -> bool:
  """Return whether a single off-track call emits the candidate line."""
  policy = supervising_policy(
      model="model",
      transport=RecordingTransport(answers=[OFF_TRACK_JSON, text]),
      budget=1,
      cooldown=0,
  )
  intervention = policy.consider(observation(guidebook=guidebook))
  return isinstance(intervention, Intervention) and intervention.text == text


@pytest.mark.parametrize(
    "line",
    [
        "Could the observed branch mean the assumption deserves another look?",
        "Maybe compare the caller with `src/parser/request.py` once more.",
    ],
)
def test_shallow_writer_gates_allow_directional_prose_and_inline_paths(
    line: str,
) -> None:
  """The controls: useful short prose is not rejected wholesale."""
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, line])
  policy = supervising_policy(
      model="model", transport=transport, budget=1, cooldown=0
  )

  intervention = policy.consider(
      observation(guidebook="A different guidebook phrase is present here.")
  )

  assert isinstance(intervention, Intervention)
  assert intervention.text == line


def test_a_non_boolean_verdict_field_is_unusable_not_coerced() -> None:
  """Attack: answer with the string "false" where a boolean is required.

  Coercion would read it as True, turning a verdict of *no* into a correction.
  One request, then rejection.
  """
  transport = RecordingTransport(
      answers=[
          '{"off_track": "false", "self_correcting": false, "reason": "x"}'
      ]
  )
  judge = ModelJudge(model="anthropic/claude-sonnet-5", transport=transport)

  with pytest.raises(JudgeAnswerError, match="boolean"):
    judge(observation(), load_criterion())
  assert len(transport.payloads) == 1


def test_the_built_policy_hands_its_criterion_to_both_model_calls() -> None:
  """The seam is the policy, so the assertion is made there, not on the classes.

  Inspecting ``ModelWriter`` alone would pass while the policy called a closure
  carrying some other standard.
  """
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, "look at the error"])
  built = supervising_policy(
      model="anthropic/claude-sonnet-5",
      transport=transport,
      budget=1,
      cooldown=0,
  )
  built.consider(observation())

  criterion_text = load_criterion().text
  assert len(transport.payloads) == 2
  for payload in transport.payloads:
    assert criterion_text in payload["messages"][0]["content"]


def test_the_guidebook_reaches_both_model_calls() -> None:
  """The judge and writer steer from the same complete phase-B artifact."""
  guidebook = "GUIDEBOOK-SENTINEL-4d68\n\n## Stage 1\n\nComplete text."
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, "look again"])
  built = supervising_policy(
      model="anthropic/claude-sonnet-5",
      transport=transport,
      budget=1,
      cooldown=0,
  )
  built.consider(observation(guidebook=guidebook))

  assert len(transport.payloads) == 2
  for payload in transport.payloads:
    assert (
        f"# Guidebook\n\n{guidebook}\n\n" in payload["messages"][0]["content"]
    )


def test_anthropic_transport_sends_the_native_endpoint_headers_and_body():
  """A credential must reach the wire and nothing else.

  The key has to be sent — that is its job — so the property is not "absent"
  but "in exactly one place": the ``x-api-key`` header. A URL or a body
  carrying it is what ends up in a proxy log, an exception message or a
  captured request, and this repo has already had to rotate a key because a
  line meant to report a *status* printed the value. Checked by capturing the
  request the transport actually builds rather than by reading the code.
  """
  import io
  import urllib.request

  sentinel = "not-a-real-key-0000000000000000"
  captured: dict[str, Any] = {}

  def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
    del timeout
    captured["url"] = request.full_url
    captured["headers"] = dict(request.header_items())
    captured["body"] = request.data.decode()
    return io.BytesIO(json.dumps({"content": [{"text": "{}"}]}).encode())

  with (
      mock.patch.dict(os.environ, {"CUSTOM_ANTHROPIC_KEY": sentinel}),
      mock.patch.object(urllib.request, "urlopen", fake_urlopen),
  ):
    answer = messages_transport(
        {"model": "m", "system": "rules", "messages": []},
        base_url="https://gateway.example/anthropic",
        api_key_env="CUSTOM_ANTHROPIC_KEY",
    )

  header_values = [
      value
      for name, value in captured["headers"].items()
      if name.lower() == "x-api-key"
  ]
  assert header_values == [sentinel]
  version_values = [
      value
      for name, value in captured["headers"].items()
      if name.lower() == "anthropic-version"
  ]
  assert version_values == ["2023-06-01"]
  assert captured["url"] == "https://gateway.example/anthropic/v1/messages"
  assert json.loads(captured["body"]) == {
      "model": "m",
      "system": "rules",
      "messages": [],
  }
  assert sentinel not in captured["url"]
  assert sentinel not in captured["body"]
  assert sentinel not in json.dumps(answer)


def test_a_missing_provider_key_says_so_without_naming_a_value():
  """The refusal has to read as a missing credential, not a broken run.

  A supervised run with no key is a configuration state, and the message says
  which variable and where its value comes from — while quoting neither the
  variable's contents nor anything else in the environment, since "it is
  empty" is exactly the case where a helpful echo prints whatever was there.
  """
  with (
      mock.patch.dict(os.environ, {"CUSTOM_PROVIDER_KEY": ""}),
      pytest.raises(RuntimeError, match="missing credential") as caught,
  ):
    _ = messages_transport(
        {"model": "m"},
        base_url="https://api.example",
        api_key_env="CUSTOM_PROVIDER_KEY",
    )
  assert "CUSTOM_PROVIDER_KEY" in str(caught.value)


# --- locating a deviation, opt-in and only opt-in (task 22 §5) --------------


def test_unguided_model_prompts_are_byte_identical_to_the_prior_path() -> None:
  """The control arm on which guidebook-aware prompting rests.

  The expected bytes are literals rather than the production constants: a
  test that derives its expected value from the changed value stays green on
  the regression it is meant to catch. The writer is exercised too because an
  off-track unguided call must not mention a guidebook it was never given.
  """
  expected_judge = """\
You are watching an engineer work. Decide two things about the moment shown.

Judge only against the criterion given below. Do not use any other standard,
and do not reason about what the correct fix would be.

Answer with one JSON object and nothing else:
{"off_track": bool, "self_correcting": bool, "reason": "<one short sentence>"}

off_track: the work shown is off the criterion's path.
self_correcting: left alone, the engineer is already returning to it.
"""
  expected_writer = """\
Write one short line to the engineer, as someone watching over their shoulder.

Hedged and offhand, pointing at what to look at — never what to do. Do not
name a fix, a function, a file to edit, or a solution. No code, no diff.
At most 400 characters. Answer with the line and nothing
else.
"""
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, "look again"])
  built = supervising_policy(
      model="m", transport=transport, budget=1, cooldown=0
  )

  _ = built.consider(observation())

  assert len(transport.payloads) == 2
  assert transport.payloads[0]["system"] == expected_judge
  assert transport.payloads[1]["system"] == expected_writer
  assert "tools" in transport.payloads[0]
  assert "tool_choice" in transport.payloads[0]
  assert "tools" not in transport.payloads[1]
  assert "tool_choice" not in transport.payloads[1]


def test_guided_model_prompts_are_byte_identical_to_the_prior_path() -> None:
  """Literal digests pin both guided system requests when overrides are None."""
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, "look again"])
  built = supervising_policy(
      model="m", transport=transport, budget=1, cooldown=0
  )

  _ = built.consider(observation(guidebook="guidebook"))

  assert len(transport.payloads) == 2
  assert hashlib.sha256(
      transport.payloads[0]["system"].encode()
  ).hexdigest() == (
      "99730202575e411497466ecce8304fe6b559e9d197fa222daafcd62196c24ef4"
  )
  assert hashlib.sha256(
      transport.payloads[1]["system"].encode()
  ).hexdigest() == (
      "56135a93e8bd72a72f2ec53fd4d4d6314cfb80dbae4115f946ed1699302c7613"
  )


def test_a_judge_asked_to_locate_a_deviation_says_so_in_its_prompt() -> None:
  """The positive arm: without it, the control above would pass on a no-op."""
  transport = RecordingTransport(answers=[ON_TRACK_JSON])
  judge = ModelJudge(model="m", transport=transport, locate_deviation=True)

  _ = judge(observation(), load_criterion())

  system = transport.payloads[0]["system"]
  assert system.startswith(JUDGE_INSTRUCTIONS)
  assert LOCATE_DEVIATION_INSTRUCTION in system


def test_a_judge_uses_override_instructions_verbatim() -> None:
  """An evaluation prompt variant occupies the system field byte for byte."""
  instructions = "JUDGE-OVERRIDE-sentinel\nKeep this exact trailing line.\n"
  transport = RecordingTransport(answers=[ON_TRACK_JSON])
  judge = ModelJudge(model="m", transport=transport, instructions=instructions)

  _ = judge(observation(guidebook="guidebook"), load_criterion())

  assert transport.payloads[0]["system"] == instructions


def test_a_writer_uses_override_instructions_verbatim() -> None:
  """A writing prompt variant occupies only its system field byte for byte."""
  instructions = "WRITER-OVERRIDE-sentinel\nKeep this exact trailing line.\n"
  transport = RecordingTransport(answers=["look again"])
  writer = ModelWriter(
      model="m", transport=transport, instructions=instructions
  )

  _ = writer(observation(guidebook="guidebook"), load_criterion())

  assert transport.payloads[0]["system"] == instructions


def test_supervising_policy_passes_override_instructions_to_the_judge() -> None:
  """Deleting the helper's pass-through changes the request under test."""
  instructions = "POLICY-OVERRIDE-sentinel"
  transport = RecordingTransport(answers=[ON_TRACK_JSON])
  policy = supervising_policy(
      model="m",
      transport=transport,
      budget=1,
      instructions=instructions,
  )

  _ = policy.consider(observation())

  assert transport.payloads[0]["system"] == instructions


def test_supervising_policy_routes_each_override_to_only_its_model_call() -> (
    None
):
  """Deleting either pass-through, or crossing them, changes the requests."""
  judge_instructions = "JUDGE-ONLY-sentinel"
  writer_instructions = "WRITER-ONLY-sentinel"
  transport = RecordingTransport(answers=[OFF_TRACK_JSON, "look again"])
  policy = supervising_policy(
      model="m",
      transport=transport,
      budget=1,
      cooldown=0,
      instructions=judge_instructions,
      writer_instructions=writer_instructions,
  )

  _ = policy.consider(observation())

  assert transport.payloads[0]["system"] == judge_instructions
  assert transport.payloads[1]["system"] == writer_instructions


def test_the_located_deviation_is_read_without_coercion() -> None:
  """An integer is carried directly as the optional measurement."""
  answered = (
      '{"off_track": true, "self_correcting": false, "reason": "guessing",'
      ' "deviation_started_steps_ago": 3}'
  )
  criterion = load_criterion()

  located = ModelJudge(
      model="m",
      transport=RecordingTransport(answers=[answered]),
      locate_deviation=True,
  )(observation(), criterion)
  assert located.deviation_started_steps_ago == 3


def test_a_default_judges_verdict_carries_no_located_deviation() -> None:
  """An A′ verdict reports absence, not a number nobody asked for."""
  verdict = ModelJudge(
      model="m", transport=RecordingTransport(answers=[OFF_TRACK_JSON])
  )(observation(), load_criterion())

  assert verdict.deviation_started_steps_ago is None
