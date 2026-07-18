"""Tests for the aggregator prompt (pure; no network)."""

from __future__ import annotations

from swe_lab.pipelines.related_files.aggregator import (
    build_aggregator_prompt,
    CANDIDATES_FILE,
    DEFAULT_AGG_BASE_PORT,
)
from swe_lab.pipelines.related_files.workspace import (
    ANNOTATION_OUTPUT,
    VALIDATOR_SCRIPT,
)


def test_agg_base_port_above_annotation_range() -> None:
  # Aggregator proxies must not collide with annotation proxies (20000+).
  assert DEFAULT_AGG_BASE_PORT >= 25000


def test_build_aggregator_prompt_mentions_key_pieces() -> None:
  prompt = build_aggregator_prompt("acme/widget", 3)
  assert "acme/widget" in prompt
  assert "3 independent candidate" in prompt
  assert CANDIDATES_FILE in prompt
  assert ANNOTATION_OUTPUT in prompt
  assert VALIDATOR_SCRIPT in prompt
  # No unrendered f-string braces leaked through.
  assert "{" not in prompt and "}" not in prompt


def test_build_aggregator_prompt_allows_judgment_not_vote() -> None:
  prompt = build_aggregator_prompt("acme/widget", 3).lower()
  # Improved wording: judgment over majority; "most appropriate" not tightest.
  assert "judgment" in prompt
  assert "most appropriate" in prompt
  assert "majority" in prompt
