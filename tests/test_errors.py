"""Tests for the failure classifier."""

from __future__ import annotations

import pytest

from swebench_related_files_annotation.annotate.errors import (
    AnnotationError,
    classify_error_text,
    cli_failure,
    RetryableError,
    UsageLimitError,
)


@pytest.mark.parametrize(
    "text",
    [
        "You have reached your usage limit. It resets at 3pm.",
        "Your credit balance is too low to run this request.",
        "quota exceeded for this window",
    ],
)
def test_usage_limit_is_fatal(text: str) -> None:
  assert classify_error_text(text) is UsageLimitError


@pytest.mark.parametrize(
    "text",
    [
        "429 Too Many Requests",
        "Error: server overloaded, please try again",
        "connection reset by peer",
        "request timed out",
        "503 Service Unavailable",
    ],
)
def test_transient_is_retryable(text: str) -> None:
  assert classify_error_text(text) is RetryableError


def test_unknown_is_plain_error() -> None:
  assert classify_error_text("some unexpected explosion") is AnnotationError


def test_usage_limit_wins_over_rate_limit_wording() -> None:
  # A message mentioning both should be treated as the fatal case.
  text = "usage limit reached; rate limit also applies"
  assert classify_error_text(text) is UsageLimitError


def test_cli_failure_builds_typed_exception() -> None:
  exc = cli_failure(result_text="usage limit reached", api_error_status=None)
  assert isinstance(exc, UsageLimitError)
  assert "usage limit" in str(exc)

  transient = cli_failure(stderr="", api_error_status=429)
  assert isinstance(transient, RetryableError)
