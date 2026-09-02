"""The judge and writer, and the attack that must fail for each guarantee.

Every "cannot" in :mod:`swe_lab.trace_synthesis.judge` is written here as the
attack that would break it, because a guard that has not been run against its
own defect is not a guard.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import pathlib
from typing import Any

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
    JudgeAnswerError,
    ModelJudge,
    ModelWriter,
    SAMPLING_KEYS,
    supervising_policy,
)
from swe_lab.trace_synthesis.supervisor import (
    InterventionTooLongError,
    MAX_INTERVENTION_CHARS,
    Observation,
    Verdict,
)

OFF_TRACK_JSON = (
    '{"off_track": true, "self_correcting": false, "reason": "guessing"}'
)
ON_TRACK_JSON = (
    '{"off_track": false, "self_correcting": false, "reason": "fine"}'
)


def observation(
    cursor: int = 1, *, task: str = "make the test pass"
) -> Observation:
  """Build an observation.

  Args:
    cursor: How many events have been consumed.
    task: The brief handed to the supervisor.

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
  )


@dataclasses.dataclass
class RecordingTransport:
  """A transport that answers from a script and keeps every payload.

  Attributes:
    answers: The contents to return, in order; the last repeats.
    model: The model the response reports, which need not be the one asked for.
    payloads: Every request body sent.
  """

  answers: list[str]
  model: str = "served/model-x"
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
    return {
        "model": self.model,
        "choices": [{"message": {"content": self.answers[index]}}],
    }


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


def test_a_forged_criterion_prevents_the_run_from_starting(
    tmp_path: pathlib.Path,
) -> None:
  """Attack: point the production construction path at an edited criterion.

  It must raise before a policy exists — the startup gate, which #337 could
  not yet claim.
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

  sent = transport.payloads[0]["messages"][1]["content"]
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
  from ordinary sampling.
  """
  transport = RecordingTransport(answers=[ON_TRACK_JSON], model="served/actual")
  judge = ModelJudge(model="requested/alias", transport=transport)
  judge(observation(), load_criterion())

  call = judge.calls[0]
  assert isinstance(call, Call)
  assert call.requested_model == "requested/alias"
  assert call.response_model == "served/actual"
  assert set(call.sampling_sent) == set(SAMPLING_KEYS)
  assert call.sampling_sent["temperature"] is None
  assert call.sampling_sent["max_tokens"] == 512


def test_an_unusable_judge_answer_is_never_retried() -> None:
  """A second ask would make the verdict a function of how often we asked."""
  transport = RecordingTransport(answers=["not json at all"])
  judge = ModelJudge(model="anthropic/claude-sonnet-5", transport=transport)

  with pytest.raises(JudgeAnswerError):
    judge(observation(), load_criterion())
  assert len(transport.payloads) == 1


def test_an_over_long_line_from_the_writer_is_rejected_not_truncated() -> None:
  """The writer may produce anything; the intervention is what refuses it."""
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

  with pytest.raises(InterventionTooLongError):
    policy.consider(observation())
