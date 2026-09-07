"""The supervisor's provider: selectable per invocation, and on the record.

The rule these tests hold up is in ``AGENTS.md`` (Boundaries): a paid run
spends the OpenRouter key pool. It was a wish until a run could be pointed at
OpenRouter without editing code, and a wish again if a later reader cannot tell
off the record which account answered.

Every assertion here has a **control arm** — the same assertion against the
default provider, which must give the *other* answer. An assertion that is
green under both providers would say nothing about the selection.
"""

from __future__ import annotations

import io
import json
import os
from typing import Any
from unittest import mock
import urllib.request

import pytest

from swe_lab.cli.overrides import apply_overrides, parse_overrides
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.rollout import CodingAgentTask
from swe_lab.trace_synthesis.judge import messages_transport, ModelJudge
from swe_lab.trace_synthesis.provider import (
    build_provider,
    live_key,
    OPENROUTER_API,
    ProviderError,
    transport_for,
)
from swe_lab.trace_synthesis.segmented_loop import SegmentedSupervision
from swe_lab.trace_synthesis.supervisor import SpeakWhenOffTrack
from swe_lab.workflow.definitions import (
    SEGMENTED_ROLLOUT,
    SUPERVISOR_MODEL,
)

# Not a credential: two strings that are not keys, used to prove the pool is
# split here and that no member's text reaches a message.
_DEAD = "pool-member-that-does-not-answer"
_LIVE = "pool-member-that-answers"


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
      supervision.cooldown, build_provider(supervision.provider)
  )
  assert isinstance(policy, SpeakWhenOffTrack)
  judge = policy.judge
  assert isinstance(judge, ModelJudge)
  return judge


def _base_url_of(judge: ModelJudge) -> object:
  """Return the upstream the judge's transport was built against."""
  return getattr(judge.transport, "keywords", {}).get("base_url")


def test_an_invocation_points_the_supervisor_at_openrouter() -> None:
  """The endpoint follows the selected provider."""
  judge = _judge_of(
      _supervision("--rollout.harness.segmented.provider=openrouter")
  )

  assert _base_url_of(judge) == OPENROUTER_API


def test_the_default_still_goes_to_anthropic() -> None:
  """The control arm: without the override, nothing moves.

  This is what makes the test above a check rather than an observation — the
  two arms give different answers, so the assertion is reading the selection
  and not a constant.
  """
  judge = _judge_of(_supervision())

  assert _base_url_of(judge) == "https://api.anthropic.com"


def test_the_pinned_model_is_the_same_name_under_either_provider() -> None:
  """A provider is a change of endpoint and nothing else.

  OpenRouter's Messages endpoint takes the bare Anthropic name and namespaces
  it itself (measured 2026-09-07; the arms are in ``docs/conventions.md``), so
  there is no per-provider model name to keep in step — and the comparability
  the pinned model buys survives repointing the endpoint. This is the arm that
  would go red if someone reintroduced a translation.
  """
  under_openrouter = _judge_of(
      _supervision("--rollout.harness.segmented.provider=openrouter")
  )
  under_anthropic = _judge_of(_supervision())

  assert under_openrouter.model == SUPERVISOR_MODEL
  assert under_anthropic.model == SUPERVISOR_MODEL


def test_an_unknown_provider_is_refused_where_it_is_chosen() -> None:
  """A typo costs a construction, not a container.

  The refusal happens while the command line is read — before a sandbox
  exists — and names what it would have accepted.
  """
  with pytest.raises(ProviderError) as caught:
    _ = _supervision("--rollout.harness.segmented.provider=openrouterr")

  assert "openrouter" in str(caught.value)
  assert "anthropic" in str(caught.value)


def test_the_selected_provider_lands_on_the_run_record() -> None:
  """The plan carries the name a decision row is written from.

  A run whose provider the record cannot show is one a later reader cannot
  place, however the two happened to spell their model that day.
  """
  assert _supervision().provider == "anthropic"
  assert (
      _supervision("--rollout.harness.segmented.provider=openrouter").provider
      == "openrouter"
  )


def test_a_pooled_credential_is_split_here_and_a_live_member_sampled() -> None:
  """The pool never leaves this process whole, and is not assumed live.

  ``OPENROUTER_API_KEYS`` holds a comma-separated pool whose members are
  separate accounts, not all of them live. The split happens inside the
  program — splitting it in a shell would put every key on a command line —
  and the member that is sent is one that answered a probe, so a rollout is
  not spent discovering a dead key.

  The probe is counted as well as stubbed: sampling once per run and reusing
  the answer is the difference between one extra request and one per
  judgement.
  """
  probed: list[str] = []
  captured: dict[str, Any] = {}

  def probe(key: str) -> bool:
    probed.append(key)
    return key == _LIVE

  def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
    del timeout
    captured["url"] = request.full_url
    captured["headers"] = dict(request.header_items())
    return io.BytesIO(json.dumps({"content": []}).encode())

  transport = transport_for(build_provider("openrouter"), probe=probe)
  with (
      mock.patch.dict(os.environ, {"OPENROUTER_API_KEYS": f"{_DEAD},{_LIVE}"}),
      mock.patch.object(urllib.request, "urlopen", fake_urlopen),
  ):
    _ = transport({"model": "m", "messages": []})
    _ = transport({"model": "m", "messages": []})

  sent = [
      value
      for name, value in captured["headers"].items()
      if name.lower() == "x-api-key"
  ]
  assert sent == [_LIVE]
  assert captured["url"] == f"{OPENROUTER_API}/v1/messages"
  assert probed == [_DEAD, _LIVE]


def test_a_single_key_provider_sends_its_variable_unsplit() -> None:
  """The control arm for the pool: splitting is the provider's property.

  A provider holding one credential sends the variable as it stands, commas
  and all. Without this arm, "the pool was split" would be indistinguishable
  from "every credential is split", which would corrupt a key that legitimately
  contains a comma.
  """
  whole = f"{_DEAD},{_LIVE}"
  captured: dict[str, Any] = {}

  def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
    del timeout
    captured["headers"] = dict(request.header_items())
    return io.BytesIO(json.dumps({"content": []}).encode())

  transport = transport_for(build_provider("anthropic"))
  with (
      mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": whole}),
      mock.patch.object(urllib.request, "urlopen", fake_urlopen),
  ):
    _ = transport({"model": "m", "messages": []})

  sent = [
      value
      for name, value in captured["headers"].items()
      if name.lower() == "x-api-key"
  ]
  assert sent == [whole]


def test_an_exhausted_pool_says_how_many_were_tried_and_nothing_else() -> None:
  """A failure names a count. No member's text may appear in the message.

  The message travels: into a log, a PR comment, a session transcript. This
  repo has already had to rotate a key because a line meant to report a
  *status* printed a value.
  """
  with pytest.raises(ProviderError) as caught:
    _ = live_key(f"{_DEAD},{_LIVE}", probe=lambda key: False)

  message = str(caught.value)
  assert "2" in message
  assert _DEAD not in message
  assert _LIVE not in message


def test_an_empty_pool_is_refused_rather_than_sent() -> None:
  """An unset variable is a missing credential, not an empty header."""
  with pytest.raises(ProviderError, match="empty"):
    _ = live_key("", probe=lambda key: True)


def test_a_pooled_variable_that_is_unset_reads_as_a_missing_credential() -> (
    None
):
  """The transport's own refusal survives a selector being in the way.

  The selector runs only on a value that exists, so an unset pool reaches the
  same "no provider key" refusal a single-key provider gets, rather than a
  ``ProviderError`` about splitting.
  """
  transport = transport_for(
      build_provider("openrouter"), probe=lambda key: True
  )
  with (
      mock.patch.dict(os.environ, {"OPENROUTER_API_KEYS": ""}),
      pytest.raises(RuntimeError, match="missing credential") as caught,
  ):
    _ = transport({"model": "m", "messages": []})

  assert "OPENROUTER_API_KEYS" in str(caught.value)


def test_the_transport_keeps_the_anthropic_wire_for_both_providers() -> None:
  """One transport, no provider branch — OpenRouter serves the same wire.

  Verified against the live endpoint on 2026-09-07 (``x-api-key`` plus
  ``anthropic-version``, an Anthropic-shaped answer back); this pins the
  consequence, which is that nothing here needs to know which provider it is
  talking to.
  """
  captured: dict[str, Any] = {}

  def fake_urlopen(request: Any, timeout: float | None = None) -> Any:
    del timeout
    captured["headers"] = dict(request.header_items())
    return io.BytesIO(json.dumps({"content": []}).encode())

  with (
      mock.patch.dict(os.environ, {"POOLED_KEY_VARIABLE": _LIVE}),
      mock.patch.object(urllib.request, "urlopen", fake_urlopen),
  ):
    _ = messages_transport(
        {"model": "m", "messages": []},
        base_url=OPENROUTER_API,
        api_key_env="POOLED_KEY_VARIABLE",
    )

  names = {name.lower() for name in captured["headers"]}
  assert "x-api-key" in names
  assert "anthropic-version" in names
