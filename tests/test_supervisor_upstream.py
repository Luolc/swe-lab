"""The supervisor's upstream: two strings the caller owns, and on the record.

The property these tests hold up is that **a consumer with their own endpoint
can use this repo without forking it**. That is what a closed provider registry
took away (0.3.6) and what
[`v0.3.2`](../docs/releases/v0.3.2.md) had already decided against once: an
upstream is a base URL plus the name of the variable holding its key, both
passed at call time, and no allow-list stands between a caller and either.

Every assertion here has a **control arm** — the same assertion against the
shipped default, which must give the *other* answer. An assertion green under
both would say nothing about the choice being honoured.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
from typing import Any
from unittest import mock
import urllib.request

import pytest

from swe_lab.cli.overrides import apply_overrides, parse_overrides
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import ANTHROPIC_API
from swe_lab.rollout import CodingAgentTask
from swe_lab.trace_synthesis.judge import (
    ANTHROPIC_BASE_URL_ENV,
    DEFAULT_API_KEY_ENV,
    messages_transport,
    ModelJudge,
)
from swe_lab.trace_synthesis.segmented_loop import SegmentedSupervision
from swe_lab.trace_synthesis.supervisor import NeverSpeak, SpeakWhenOffTrack
from swe_lab.workflow.definitions import SEGMENTED_ROLLOUT, SUPERVISOR_MODEL

# Somebody else's gateway. Deliberately a host this repo has never heard of:
# that is the whole point, and a registry of blessed names cannot express it.
_THIRD_PARTY = "https://llm.gateway.example.internal/anthropic"

# Not a credential: a variable name nobody but this test uses.
_KEY_VARIABLE = "SWE_LAB_TEST_SUPERVISOR_KEY"
_KEY_VALUE = "not-a-real-key"


def _supervision(*overrides: str) -> SegmentedSupervision:
  """Return the shipped segmented plan, with the overrides applied.

  Args:
    *overrides: Command-line overrides, as typed.

  Returns:
    The plan the run would use.
  """
  (entry,) = apply_overrides(
      SEGMENTED_ROLLOUT, parse_overrides(list(overrides))
  )
  assert isinstance(entry.task, CodingAgentTask)
  harness = entry.task.harness
  assert isinstance(harness, ClaudeCodeHarness)
  assert harness.segmented is not None
  return harness.segmented


def _judge_of(supervision: SegmentedSupervision) -> ModelJudge:
  """Return the judge the run's policy would ask.

  Args:
    supervision: The plan, as the invocation left it.

  Returns:
    The judge.
  """
  policy = supervision.policy_factory(
      supervision.cooldown, supervision.base_url, supervision.api_key_env
  )
  assert isinstance(policy, SpeakWhenOffTrack)
  judge = policy.judge
  assert isinstance(judge, ModelJudge)
  return judge


def _upstream_of(judge: ModelJudge) -> tuple[object, object]:
  """Return the base URL and key variable the judge's transport was built on."""
  keywords = getattr(judge.transport, "keywords", {})
  return keywords.get("base_url"), keywords.get("api_key_env")


def _built() -> SegmentedSupervision:
  """Return a minimal plan, constructed now so its defaults are read now.

  Returns:
    The plan.
  """
  return SegmentedSupervision(
      policy_factory=lambda cooldown, url, variable: NeverSpeak()
  )


def test_an_arbitrary_third_party_base_url_reaches_the_supervisor() -> None:
  """A consumer's own endpoint works, with nothing to add to this repo.

  This is the assertion the provider registry made impossible: the only way to
  reach an upstream it did not enumerate was to fork. It takes an override and
  a URL that appears nowhere in this codebase.
  """
  judge = _judge_of(
      _supervision(f"--rollout.harness.segmented.base_url={_THIRD_PARTY}")
  )

  assert _upstream_of(judge)[0] == _THIRD_PARTY


def test_the_default_upstream_does_not_move() -> None:
  """The control arm: without the override, nothing changes.

  This is what makes the test above a check rather than an observation — the
  two arms give different answers, so the assertion reads the choice and not a
  constant.
  """
  judge = _judge_of(_supervision())

  assert _upstream_of(judge) == (ANTHROPIC_API, DEFAULT_API_KEY_ENV)


def test_the_key_variable_is_the_callers_choice_too() -> None:
  """An upstream is *two* strings, and the second one travels as well.

  A deployment whose key lives under its own variable name is as ordinary as
  one with its own endpoint; its control arm is the default above.
  """
  judge = _judge_of(
      _supervision(f"--rollout.harness.segmented.api_key_env={_KEY_VARIABLE}")
  )

  assert _upstream_of(judge)[1] == _KEY_VARIABLE


def test_the_default_base_url_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  """``ANTHROPIC_BASE_URL`` points the supervisor, as it points the agent.

  The consumer's sandbox runs ``claude -p``, which reads this variable and
  ``ANTHROPIC_API_KEY``; a supervisor that ignored them would be pointed
  somewhere else than the run it is watching.
  """
  monkeypatch.setenv(ANTHROPIC_BASE_URL_ENV, _THIRD_PARTY)

  assert _built().base_url == _THIRD_PARTY


def test_the_fallback_is_the_anthropic_root_when_nothing_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  """The control arm for the environment: unset means the shipped default."""
  monkeypatch.delenv(ANTHROPIC_BASE_URL_ENV, raising=False)

  assert _built().base_url == ANTHROPIC_API


def test_an_empty_key_variable_name_is_refused_where_it_is_chosen() -> None:
  """The one refusal: a name that names nothing.

  Structural rather than a taste — the empty string cannot be an environment
  variable, so the request it would produce carries no key at all. Refused
  while the command line is read, so it costs a construction rather than a
  container.
  """
  with pytest.raises(ValueError, match="api_key_env"):
    _ = _supervision("--rollout.harness.segmented.api_key_env=")


def test_an_unheard_of_upstream_is_not_refused() -> None:
  """The control arm for the refusal, and the reason this PR exists.

  Without it, "an empty name is rejected" would be indistinguishable from "an
  upstream we do not recognise is rejected", which is the behaviour being
  removed. Neither string is one this repo has ever seen.
  """
  supervision = dataclasses.replace(
      _built(), base_url=_THIRD_PARTY, api_key_env=_KEY_VARIABLE
  )

  assert (supervision.base_url, supervision.api_key_env) == (
      _THIRD_PARTY,
      _KEY_VARIABLE,
  )


def test_the_pinned_model_is_the_same_name_wherever_the_run_is_pointed() -> (
    None
):
  """Repointing the endpoint is a change of endpoint and nothing else.

  OpenRouter's Messages endpoint takes the bare Anthropic name and namespaces
  it itself (measured 2026-09-07; the arms are in ``docs/conventions.md``), and
  a gateway that did not would be the caller's business. So there is no
  per-upstream model name to keep in step, and the comparability the pinned
  model buys survives the move. This is the arm that would go red if someone
  reintroduced a translation.
  """
  elsewhere = _judge_of(
      _supervision(f"--rollout.harness.segmented.base_url={_THIRD_PARTY}")
  )
  default = _judge_of(_supervision())

  assert elsewhere.model == SUPERVISOR_MODEL
  assert default.model == SUPERVISOR_MODEL


def test_the_transport_asks_the_named_upstream_with_the_named_variable() -> (
    None
):
  """The two strings are what the request is actually built from.

  The key is read from the environment at call time — never a command line,
  never an argument — and the wire is the Anthropic Messages one whoever is
  serving it.
  """
  captured: dict[str, Any] = {}

  def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
    del timeout
    captured["url"] = request.full_url
    captured["headers"] = dict(request.header_items())
    return io.BytesIO(json.dumps({"content": []}).encode())

  with (
      mock.patch.dict(os.environ, {_KEY_VARIABLE: _KEY_VALUE}),
      mock.patch.object(urllib.request, "urlopen", fake_urlopen),
  ):
    _ = messages_transport(
        {"model": "m", "messages": []},
        base_url=_THIRD_PARTY,
        api_key_env=_KEY_VARIABLE,
    )

  sent = {name.lower(): value for name, value in captured["headers"].items()}
  assert captured["url"] == f"{_THIRD_PARTY}/v1/messages"
  assert sent["x-api-key"] == _KEY_VALUE
  assert "anthropic-version" in sent


def test_an_unset_key_variable_reads_as_a_missing_credential() -> None:
  """The control arm for the call above: no key, no request.

  A supervisor that cannot reach a model is a missing credential, not a broken
  instance, and the refusal names the variable so the reader can fill it.
  """
  with (
      mock.patch.dict(os.environ, {_KEY_VARIABLE: ""}),
      pytest.raises(RuntimeError, match="missing credential") as caught,
  ):
    _ = messages_transport(
        {"model": "m", "messages": []},
        base_url=_THIRD_PARTY,
        api_key_env=_KEY_VARIABLE,
    )

  assert _KEY_VARIABLE in str(caught.value)
  assert _KEY_VALUE not in str(caught.value)
