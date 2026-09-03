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
    PolicyLapseError,
    Supervisor,
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
    finish_reason: ``choices[0].finish_reason`` to report, or ``None`` to omit
      it — matching a provider response that carries none.
    payloads: Every request body sent.
  """

  answers: list[str]
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
    choice: dict[str, Any] = {"message": {"content": self.answers[index]}}
    if self.finish_reason is not None:
      choice["finish_reason"] = self.finish_reason
    return {"model": self.model, "choices": [choice]}


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

  It raises before a policy object exists. This is a **helper-level** refusal,
  not a run-level one: nothing in a rollout path calls this yet.
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


def test_an_unusable_judge_answer_is_never_retried() -> None:
  """A second ask would make the verdict a function of how often we asked."""
  transport = RecordingTransport(answers=["not json at all"])
  judge = ModelJudge(model="anthropic/claude-sonnet-5", transport=transport)

  with pytest.raises(JudgeAnswerError):
    judge(observation(), load_criterion())
  assert len(transport.payloads) == 1


def test_a_token_budget_lapse_is_recorded_differently_from_a_bad_answer() -> (
    None
):
  """`supervisor.jsonl` must not fold these two failures into one lapse.

  Both are `JudgeAnswerError`s the policy bounds to a `PolicyLapseError`, but
  they call for different fixes: `finish_reason == "length"` means our own
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
      RecordingTransport(answers=['{"off_track": tru'], finish_reason="length")
  )
  bad_answer_row = lapse_row(
      RecordingTransport(answers=["not json at all"], finish_reason="stop")
  )

  assert budget_row["kind"] == "lapse"
  assert bad_answer_row["kind"] == "lapse"
  assert budget_row["finish_reason"] == "length"
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


def test_a_non_boolean_verdict_field_is_unusable_not_coerced() -> None:
  """Attack: answer with the string "false" where a boolean is required.

  Coercion would read it as True, turning a verdict of *no* into a correction.
  One request, then rejection.
  """
  transport = RecordingTransport(
      answers=['{"off_track": "false", "self_correcting": false}']
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
    assert criterion_text in payload["messages"][1]["content"]


def test_the_provider_key_travels_only_in_the_header_it_belongs_in():
  """A credential must reach the wire and nothing else.

  The key has to be sent — that is its job — so the property is not "absent"
  but "in exactly one place": the ``Authorization`` header. A URL or a body
  carrying it is what ends up in a proxy log, an exception message or a
  captured request, and this repo has already had to rotate a key because a
  line meant to report a *status* printed the value. Checked by capturing the
  request the transport actually builds rather than by reading the code.
  """
  import io
  import urllib.request

  from swe_lab.trace_synthesis import judge as judge_module

  sentinel = "not-a-real-key-0000000000000000"
  captured: dict[str, Any] = {}

  def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
    del timeout
    captured["url"] = request.full_url
    captured["headers"] = dict(request.header_items())
    captured["body"] = request.data.decode()
    return io.BytesIO(
        json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
    )

  with (
      mock.patch.dict(os.environ, {judge_module.OPENROUTER_KEYS_ENV: sentinel}),
      mock.patch.object(urllib.request, "urlopen", fake_urlopen),
  ):
    answer = judge_module.openrouter_transport({"model": "m", "messages": []})

  header_values = [
      value
      for name, value in captured["headers"].items()
      if name.lower() == "authorization"
  ]
  assert header_values == [f"Bearer {sentinel}"]
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
  from swe_lab.trace_synthesis import judge as judge_module

  with (
      mock.patch.dict(os.environ, {judge_module.OPENROUTER_KEYS_ENV: ""}),
      pytest.raises(RuntimeError, match="missing credential") as caught,
  ):
    _ = judge_module.openrouter_transport({"model": "m"})
  assert judge_module.OPENROUTER_KEYS_ENV in str(caught.value)
