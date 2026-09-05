"""The supervisor's two model calls: the judge, and the writer.

The policy in :mod:`swe_lab.trace_synthesis.supervisor` decides *whether* the
moment has come; these decide *what is true* and *what to say*. Both take the
observation — including the phase-B guidebook when the workflow supplies one —
and the general-practice criterion, and both record what answered them.

Every guarantee stated here has an attack test that must fail — a claim without
one is downgraded rather than written, because a guarantee that has not been
attacked is a wish (see the experiment playbook's entry on guards).

- **Startup refuses unusable artifacts.** :func:`supervising_policy` loads the
  pinned criterion while the run is assembled, and the guidebook-guided harness
  validates its phase-B artifact before the first actor script is launched.
- **The judge prompts with the criterion it was handed**, not one it fetches
  for itself — the layer above hand-off, which the policy cannot enforce.
  ``test_the_judge_prompts_with_the_criterion_it_was_handed``.
- **Neither call has a side door**: both take the observation and the criterion
  and nothing else, and the policy passes the criterion to both — a writer that
  took only the observation could carry its own standard in a closure, which no
  signature would show.
  ``test_neither_call_has_an_input_beside_the_observation_and_criterion`` and
  ``test_the_built_policy_hands_its_criterion_to_both_model_calls``.
- **The model is named explicitly or construction fails** — no default, for the
  same reason ``budget`` has none: an obligation enforced by the absence of a
  default rather than by anyone remembering.
  ``test_a_judge_without_a_named_model_cannot_be_built``.
- **A verdict field that is not a JSON boolean is unusable, never coerced** —
  ``bool("false")`` is ``True``, so coercion turns a verdict of *no* into a
  correction. ``test_a_non_boolean_verdict_field_is_unusable_not_coerced``.
- **No branch depends on two verdicts agreeing.** The judgement is a model
  call and is not a function; the pipeline is what must be correct under that.
  ``test_the_pipeline_is_correct_when_the_judge_disagrees_with_itself``.

What each call records is fixed here rather than left to a caller: the model
the **response** reports (never the requested alias, which stays correct when
an alias is re-pointed) and the sampling actually sent, **including the
parameters that were not sent**, since an unset parameter is invisible unless
its absence is written down.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import dataclasses
import json
import os
import pathlib
from typing import Any, cast
import urllib.request

from swe_lab.conversation import Message, TextBlock
from swe_lab.trace_synthesis.criterion import Criterion, load_criterion
from swe_lab.trace_synthesis.supervisor import (
    MAX_INTERVENTION_CHARS,
    Observation,
    SpeakWhenOffTrack,
    Verdict,
)

#: Every sampling parameter we may send. Recorded as ``None`` when not sent, so
#: absence is readable rather than merely missing.
SAMPLING_KEYS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "seed",
    "stop",
)

#: Where a request goes and what comes back. Injected so tests make no call.
Transport = Callable[[Mapping[str, Any]], Mapping[str, Any]]

#: How long one judge or writer call may take. A supervisor that blocks forever
#: is a lost supervisor rather than a slow one — the run's own teardown treats
#: it that way — so the call has a bound of its own rather than relying on it.
CALL_TIMEOUT_SECONDS = 180.0

#: The sole tool a judge may call. The provider schema narrows ordinary
#: responses; :class:`ModelJudge` validates the same contract locally because
#: a caller-configured gateway need not enforce it.
JUDGE_TOOL_NAME = "submit_supervision_verdict"
JUDGE_TOOL: Mapping[str, Any] = {
    "name": JUDGE_TOOL_NAME,
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


def messages_transport(
    payload: Mapping[str, Any], *, base_url: str, api_key_env: str
) -> Mapping[str, Any]:
  """Send one Anthropic Messages request and return the decoded answer.

  Deployment choices are arguments rather than provider-named constants: the
  caller chooses the upstream and the environment variable holding its
  credential. The credential is read at call time and never reaches a command
  line. This deliberately does not retry — a retried judgement would be a
  function of how many times we asked.

  Args:
    payload: The request body, already shaped by the caller.
    base_url: Upstream base URL; ``/v1/messages`` is appended.
    api_key_env: Environment variable containing the API key.

  Returns:
    The decoded response.

  Raises:
    RuntimeError: No key is present in the environment.
  """
  api_key = os.environ.get(api_key_env, "")
  if not api_key:
    raise RuntimeError(
        f"no provider key: {api_key_env} is unset or empty in this"
        " shell, so the supervisor cannot reach a model. This is a missing"
        " credential, not a broken instance or image — see docs/conventions.md"
        " (Secrets) for the op:// reference that fills it."
    )
  request = urllib.request.Request(
      f"{base_url.rstrip('/')}/v1/messages",
      data=json.dumps(dict(payload)).encode(),
      headers={
          "x-api-key": api_key,
          "anthropic-version": "2023-06-01",
          "Content-Type": "application/json",
      },
  )
  with urllib.request.urlopen(
      request, timeout=CALL_TIMEOUT_SECONDS
  ) as response:
    decoded: Mapping[str, Any] = json.loads(response.read())
  return decoded


JUDGE_INSTRUCTIONS = """\
You are watching an engineer work. Decide two things about the moment shown.

Judge only against the criterion given below. Do not use any other standard,
and do not reason about what the correct fix would be.

Answer with one JSON object and nothing else:
{"off_track": bool, "self_correcting": bool, "reason": "<one short sentence>"}

off_track: the work shown is off the criterion's path.
self_correcting: left alone, the engineer is already returning to it.
"""

#: The judge contract for a run whose observation carries a guidebook.
GUIDED_JUDGE_INSTRUCTIONS = """\
You are watching an engineer work. Decide two things about the moment shown.

Judge against the general-practice criterion and, when present, the guidebook
given below. The guidebook says which instance-specific route is on track.

Answer with one JSON object and nothing else:
{"off_track": bool, "self_correcting": bool, "reason": "<one short sentence>"}

off_track: the work shown is off the criterion's path.
self_correcting: left alone, the engineer is already returning to it.
"""

#: Appended to the selected judge instructions only when a judge is built with
#: ``locate_deviation``. Kept as a separate constant so the default
#: instructions are byte-identical to what every A′ run has sent —
#: ``test_unguided_model_prompts_are_byte_identical_to_the_prior_path`` is what
#: makes that a check.
LOCATE_DEVIATION_INSTRUCTION = """
Also answer "deviation_started_steps_ago": if off_track, how many of the steps
shown above the deviation began — 0 for the most recent step. Omit it or use
null when you cannot tell.
"""

WRITER_INSTRUCTIONS = f"""\
Write one short line to the engineer, as someone watching over their shoulder.

Hedged and offhand, pointing at what to look at — never what to do. Do not
name a fix, a function, a file to edit, or a solution. No code, no diff.
At most {MAX_INTERVENTION_CHARS} characters. Answer with the line and nothing
else.
"""

#: The writer contract for a run whose observation carries a guidebook.
GUIDED_WRITER_INSTRUCTIONS = f"""\
Write one short line to the engineer, as someone watching over their shoulder.

Hedged and offhand, pointing at what to look at — never what to do. Do not
name a fix, a function, a file to edit, or a solution. No code, no diff.
Use the guidebook's justification to give a reason the engineer can follow.
At most {MAX_INTERVENTION_CHARS} characters. Answer with the line and nothing
else.
"""


class JudgeAnswerError(ValueError):
  """Raised when a model's answer is not the shape the caller requires.

  Never retried: a second ask would make the verdict a function of how many
  times we asked.

  Attributes:
    finish_reason: What the provider's ``stop_reason`` said ended the response,
      or ``None`` when the response carried none. ``"max_tokens"``
      means the answer was never finished — the token budget ran out before
      the model could write it — which is a configuration problem, not a
      judgment-quality one; anything else (typically ``"stop"``) means the
      model finished and still produced something this class could not use.
      A caller that only sees "the judge call failed" cannot tell those apart
      (issue #383); this is how it can.
  """

  finish_reason: str | None

  def __init__(self, message: str, *, finish_reason: str | None) -> None:
    """Record the answer shape failure together with why the call ended.

    Args:
      message: What was wrong with the answer.
      finish_reason: See the class attribute.
    """
    super().__init__(message)
    self.finish_reason = finish_reason


@dataclasses.dataclass(frozen=True)
class Call:
  """What answered one request, and how it was asked.

  Attributes:
    requested_model: The alias sent.
    response_model: The model the **response** reports, or ``None`` when the
      provider sent none. An alias re-pointed upstream leaves the request
      looking correct, so the requested name cannot stand in for this.
    sampling_sent: Every key in :data:`SAMPLING_KEYS` mapped to the value sent,
      or ``None`` where the request left it to the provider.
    raw: The native content blocks before parsing.
    finish_reason: The provider's ``stop_reason``, or ``None`` when absent.
      ``"max_tokens"`` means the answer was truncated by
      ``max_tokens`` rather than completed.
  """

  requested_model: str
  response_model: str | None
  sampling_sent: Mapping[str, Any]
  raw: object
  finish_reason: str | None


def _sampling_sent(payload: Mapping[str, Any]) -> dict[str, Any]:
  """Report which sampling parameters a request actually carried.

  Args:
    payload: The request body about to be sent.

  Returns:
    Every sampling key mapped to the value sent, or ``None`` when unset.
  """
  return {key: payload.get(key) for key in SAMPLING_KEYS}


def _render(records: Sequence[Message]) -> str:
  """Render the actor's records for a prompt.

  Args:
    records: The evidence window, in order.

  Returns:
    One block of text, oldest first.
  """
  lines: list[str] = []
  for record in records:
    body = " ".join(
        block.text for block in record.content if isinstance(block, TextBlock)
    )
    lines.append(f"[{record.role.value}] {body}")
  return "\n".join(lines)


def _prompt(observation: Observation, criterion: Criterion) -> str:
  """Build the user half of a request.

  Args:
    observation: What the actor was asked to do and what it has produced.
    criterion: The standard handed in by the policy — used verbatim, never
      re-read from disk, which is what the judge's attack test pins.

  Returns:
    The prompt text.
  """
  said = "\n".join(one.text for one in observation.said) or "(nothing yet)"
  done = _render(observation.evidence)
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


@dataclasses.dataclass
class ModelJudge:
  """Answers the two questions with one model call, and records what answered.

  Attributes:
    model: The model to ask. **No default**: a judge must name its model, for
      the same reason a speaking policy must name its budget. Pinning it to the
      actor's own model is what keeps a positive result from being readable
      both as "supervision worked" and as "a stronger model's reasoning was
      smuggled in"; that choice belongs to the caller, and this class only
      refuses to let it go unstated.
    transport: How a request is sent.
    max_tokens: The reasoning-plus-answer budget for one call — the sampling
      parameter that decides whether the model gets to finish. Measured
      against 902 replayed calls (issue #383): every lapse (85/902, 9.4%) had
      ``finish_reason == "max_tokens"`` with reasoning tokens at the previous
      cap of 512 (median 511, max 512), while every call that produced a usable
      answer needed at most 441 reasoning tokens for a distribution whose
      median was 89. The old 512 sat inside that distribution rather than past
      its tail, so a call needing an ordinary amount of reasoning could still
      run out before writing its answer; the 85 failing calls were themselves
      cut off at 512, so their true demand is censored and may be higher still.
      4096 clears the entire observed distribution — including its successful
      tail — with roughly 8x margin over the old cap, and costs nothing on the
      common case: the model stops once it has an answer, so a call needing
      the median's ~89 reasoning tokens spends the same either way. See issue
      #383 for the recompute.
    locate_deviation: Ask, in addition, how far back the deviation started.
      **Off by default, and the default prompt is byte-identical to the one
      every A′ run has sent** — pinned by
      ``test_unguided_model_prompts_are_byte_identical_to_the_prior_path``,
      because a supervision arm whose judge prompt quietly changed would move
      the thing it measures. On only for the segmented loop, which records the
      answer so that how many turns late its corrections were is a measured
      distribution rather than a recollection.
    calls: What answered each request, in order.
    instructions: Optional system instructions for evaluation prompt variants.
      ``None`` preserves the guided or unguided default.
  """

  model: str
  transport: Transport
  max_tokens: int = 4096
  locate_deviation: bool = False
  calls: list[Call] = dataclasses.field(default_factory=list)
  instructions: str | None = None

  def __call__(self, observation: Observation, criterion: Criterion) -> Verdict:
    """Judge one moment against the criterion handed in.

    Args:
      observation: The evidence window and the task.
      criterion: The standard, used verbatim.

    Returns:
      The verdict.

    Raises:
      JudgeAnswerError: The answer was not exactly one matching tool call with
        valid input. Not retried.
    """
    instructions = self.instructions
    if instructions is None:
      instructions = (
          GUIDED_JUDGE_INSTRUCTIONS
          if observation.guidebook is not None
          else JUDGE_INSTRUCTIONS
      ) + (LOCATE_DEVIATION_INSTRUCTION if self.locate_deviation else "")
    payload = {
        "model": self.model,
        "max_tokens": self.max_tokens,
        "system": instructions,
        "messages": [
            {"role": "user", "content": _prompt(observation, criterion)}
        ],
        "tools": [JUDGE_TOOL],
        "tool_choice": {"type": "tool", "name": JUDGE_TOOL_NAME},
    }
    response = self.transport(payload)
    finish_reason = response.get("stop_reason")
    content = response.get("content")
    self.calls.append(
        Call(
            requested_model=self.model,
            response_model=response.get("model"),
            sampling_sent=_sampling_sent(payload),
            raw=content,
            finish_reason=finish_reason,
        )
    )
    if not isinstance(content, list):
      raise JudgeAnswerError(
          "unusable judge answer: expected a content list",
          finish_reason=finish_reason,
      )
    matching_tool_uses = [
        block
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "tool_use"
        and block.get("name") == JUDGE_TOOL_NAME
    ]
    if len(matching_tool_uses) != 1:
      raise JudgeAnswerError(
          f"unusable judge answer: expected exactly one {JUDGE_TOOL_NAME}"
          f" tool call, got {len(matching_tool_uses)}",
          finish_reason=finish_reason,
      )

    answer = matching_tool_uses[0].get("input")
    if not isinstance(answer, Mapping):
      raise JudgeAnswerError(
          "unusable judge answer: tool input must be an object",
          finish_reason=finish_reason,
      )
    expected_fields = {"off_track", "self_correcting", "reason"}
    allowed_fields = expected_fields | {"deviation_started_steps_ago"}
    if (
        not expected_fields <= answer.keys()
        or not answer.keys() <= allowed_fields
    ):
      raise JudgeAnswerError(
          "unusable judge answer: tool input fields do not match the schema",
          finish_reason=finish_reason,
      )

    off_track = answer["off_track"]
    self_correcting = answer["self_correcting"]

    # Coercion here would read "false" as True — a verdict of *no* becoming a
    # correction — so the shape is required rather than converted.
    for name, value in (
        ("off_track", off_track),
        ("self_correcting", self_correcting),
    ):
      if type(value) is not bool:
        raise JudgeAnswerError(
            "unusable judge answer:"
            f" {name} must be a JSON boolean, got {type(value).__name__}",
            finish_reason=finish_reason,
        )
    off_track = cast(bool, off_track)
    self_correcting = cast(bool, self_correcting)

    reason = answer["reason"]
    if type(reason) is not str:
      raise JudgeAnswerError(
          "unusable judge answer: reason must be a JSON string, got"
          f" {type(reason).__name__}",
          finish_reason=finish_reason,
      )

    # Read with `.get` and type-checked rather than coerced: an answer that
    # omits it is not an error, and a string "3" is not an integer this record
    # may claim was measured.
    started = answer.get("deviation_started_steps_ago")
    # `type(...) is int`, deliberately **not** `isinstance`: `bool` is a
    # subclass of `int` in Python, so `isinstance(True, int)` is True and a
    # judge answering `true` here would be recorded as "1 step ago" — a
    # boolean wearing a measurement's clothes, with nothing to catch it.
    # A lint pass "correcting" this to `isinstance` turns no light red.
    if started is not None and type(started) is not int:
      raise JudgeAnswerError(
          "unusable judge answer: deviation_started_steps_ago must be a JSON"
          f" integer or null, got {type(started).__name__}",
          finish_reason=finish_reason,
      )
    return Verdict(
        off_track=off_track,
        self_correcting=self_correcting,
        reason=reason,
        deviation_started_steps_ago=started,
        judge_input=dict(payload),
    )


@dataclasses.dataclass
class ModelWriter:
  """Writes the line, with the same provenance record as the judge.

  Attributes:
    model: The model to ask. **No default**, as for :class:`ModelJudge`.
    transport: How a request is sent.
    max_tokens: The one sampling parameter we set.
    calls: What answered each request, in order.
  """

  model: str
  transport: Transport
  max_tokens: int = 256
  calls: list[Call] = dataclasses.field(default_factory=list)

  def __call__(self, observation: Observation, criterion: Criterion) -> str:
    """Write one line for this moment.

    Args:
      observation: The evidence window and the task.
      criterion: The standard, used verbatim.

    Returns:
      The model's line. :class:`SpeakWhenOffTrack` applies the shallow
      answer-form checks and :class:`Intervention` applies the non-empty and
      length bounds; the policy records either rejection as one bounded lapse.

    Raises:
      ValueError: The answer was not exactly one text block carrying a string.
        Not retried.
    """
    payload = {
        "model": self.model,
        "max_tokens": self.max_tokens,
        "system": (
            GUIDED_WRITER_INSTRUCTIONS
            if observation.guidebook is not None
            else WRITER_INSTRUCTIONS
        ),
        "messages": [
            {"role": "user", "content": _prompt(observation, criterion)},
        ],
    }
    response = self.transport(payload)
    finish_reason = response.get("stop_reason")
    content = response.get("content")
    self.calls.append(
        Call(
            requested_model=self.model,
            response_model=response.get("model"),
            sampling_sent=_sampling_sent(payload),
            raw=content,
            finish_reason=finish_reason,
        )
    )
    if not isinstance(content, list):
      raise ValueError("unusable writer answer: expected a content list")
    text_blocks = [
        block
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text"
    ]
    if len(text_blocks) != 1:
      raise ValueError(
          "unusable writer answer: expected exactly one text block, got"
          f" {len(text_blocks)}"
      )

    text = text_blocks[0].get("text")
    if type(text) is not str:
      raise ValueError(
          "unusable writer answer: text must be a string, got"
          f" {type(text).__name__}"
      )
    return text


def supervising_policy(
    *,
    model: str,
    transport: Transport,
    budget: int,
    cooldown: int = 4,
    window: int = 8,
    gold_patch: str | None = None,
    criterion_path: pathlib.Path | None = None,
    locate_deviation: bool = False,
    instructions: str | None = None,
) -> SpeakWhenOffTrack:
  """Build the judging policy, or reject the artifact.

  Called by :func:`~swe_lab.trace_synthesis.channel.supervision` while a
  rollout assembles its observers — before the sandbox is created — so a forged
  artifact stops the run rather than only this call, which is what acceptance
  point 2b asks for. Pinned by
  ``test_a_forged_criterion_stops_the_run_before_a_sandbox_exists``.

  Args:
    model: The model for both calls; named explicitly, never defaulted.
    transport: How requests are sent.
    budget: How many interventions a run may carry.
    cooldown: Boundaries required between interventions.
    window: How many of the actor's records the judge sees.
    gold_patch: This instance's gold patch, when recorded, so the redundant
      overlap half of the criterion check can run.
    criterion_path: The artifact to load; production leaves it unset.
    locate_deviation: Ask the judge how far back the deviation started. Off by
      default, which leaves the A′ arms' prompt byte-identical; see
      :class:`ModelJudge`.
    instructions: Optional judge system instructions. ``None`` preserves the
      guided or unguided default.

  Returns:
    The policy, holding a criterion whose digest is the pinned one.
  """
  criterion = (
      load_criterion(gold_patch=gold_patch, path=criterion_path)
      if criterion_path is not None
      else load_criterion(gold_patch=gold_patch)
  )
  return SpeakWhenOffTrack(
      judge=ModelJudge(
          model=model,
          transport=transport,
          locate_deviation=locate_deviation,
          instructions=instructions,
      ),
      writer=ModelWriter(model=model, transport=transport),
      criterion=criterion,
      budget=budget,
      cooldown=cooldown,
      window=window,
  )
