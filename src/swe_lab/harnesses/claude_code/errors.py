"""Failure taxonomy and classification for annotation / aggregation runs.

We do not yet know every way a headless Claude Code run can fail, so the policy
is deliberately conservative:

- **Retry only clearly-transient failures** (network blips, rate limits,
  overload, timeouts).
- **Never retry a usage/quota exhaustion** — it will keep failing until the
  subscription window refreshes, so the batch should stop and wait instead.
- **Preserve raw diagnostics** for every failure, so unknown error shapes can be
  understood and this classifier refined.

Classification is keyword-based over whatever text we can get (stderr, the CLI
result message, the API error status). The keyword lists are best guesses and
are meant to be tightened as real failures are observed.
"""

from __future__ import annotations


class AnnotationError(RuntimeError):
  """A single annotation run failed."""


class RetryableError(AnnotationError):
  """A transient failure (network, rate limit, overload, timeout) — retry."""


class UsageLimitError(AnnotationError):
  """Subscription/credit quota exhausted — do NOT retry; wait for refresh.

  Signals the batch to stop: every subsequent run would fail the same way until
  the usage window resets.
  """


class MissingOutputError(AnnotationError):
  """The session ended but wrote no (or unreadable) output file."""


# Quota/credit exhaustion — fatal, wait for the window to refresh.
_USAGE_LIMIT_MARKERS: tuple[str, ...] = (
    "usage limit",
    "limit reached",
    "credit balance",
    "out of credit",
    "insufficient credit",
    "quota",
    "resets at",
    "reset at",
    "upgrade to",
)

# Transient — safe to retry after a short backoff.
_RETRYABLE_MARKERS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "429",
    "overloaded",
    "overload",
    "529",
    "timeout",
    "timed out",
    "temporarily",
    "connection",
    "network",
    "reset by peer",
    "econnreset",
    "502",
    "503",
    "504",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    # A 401 mid-session is transient here: OAuth subscription tokens can expire
    # or race a refresh during a long run. A truly bad credential fails on the
    # first turn, not after minutes of work; a fresh invocation gets a fresh
    # token. Retries are bounded, so a persistent 401 still stops after a few.
    "401",
    "invalid authentication credentials",
    "authentication_error",
)


def classify_error_text(text: str) -> type[AnnotationError]:
  """Map failure text to an error class.

  Usage-limit markers win over retryable ones (a subscription-window message
  may mention "limit"). A failure that carries **no diagnostic text at all**
  (the CLI exited nonzero but printed nothing) is treated as transient and
  retried: a genuinely deterministic error almost always emits *something* (a
  stack trace, an API status, a quota message), so an empty signal is much more
  likely a blip (a dropped stream, a crashed child) than a reproducible fault.
  Anything recognized-but-non-transient stays a plain ``AnnotationError``.
  """
  low = text.lower()
  if not low.strip():
    return RetryableError
  if any(marker in low for marker in _USAGE_LIMIT_MARKERS):
    return UsageLimitError
  if any(marker in low for marker in _RETRYABLE_MARKERS):
    return RetryableError
  return AnnotationError


def cli_failure(
    *, stderr: str = "", result_text: str = "", api_error_status: object = None
) -> AnnotationError:
  """Build the appropriate error from a failed CLI invocation's signals."""
  text = " ".join(
      part
      for part in (stderr, result_text, str(api_error_status or ""))
      if part
  )
  cls = classify_error_text(text)
  message = (result_text or stderr or text or "unknown failure").strip()
  return cls(message[:500])
