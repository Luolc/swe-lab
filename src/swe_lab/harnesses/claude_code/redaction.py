"""What is sensitive in a captured HTTP exchange, and how to mask and check it.

**This module is the one home for that.** The sets below, the placeholder that
replaces a value, and both the producer and the reader live here; everything
that redacts or checks a capture imports them rather than restating them.

Proxy capture records whole HTTP exchanges, so unlike a stream trace it has an
*envelope*: credentials on the request, the operator's account identity on the
response. Those must never reach a stored artifact — a log is the thing that
gets copied into an issue, pushed to a dataset repo, or handed to another tool,
and a credential in a file travels much further than the same credential in an
environment variable.

Three jobs, deliberately together:

- :func:`redact_record` masks a record we produce.
- :func:`unredacted_fields` checks a record somebody else produced —
  `cc-reverse-proxy` masks at write time (ADR-0012 §4), and it is an external,
  separately versioned binary, so "the build we ran redacts" is exactly the
  assumption that stops holding without anyone noticing.
- :func:`unclassified_fields` reports fields nobody has judged either way,
  because redaction is a deny-list and silence about a new field is not
  safety.

The checks return findings rather than a bool: "which field, in which record"
is what a person needs in order to act.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from typing import Literal

# What cc-reverse-proxy writes in place of a masked value, and what everything
# in this repo writes when it redacts a record itself.
REDACTED = "[REDACTED]"

# The placeholder an earlier redactor wrote, accepted on read and written by
# nothing. **Closed and dated:** it appears in the 37 injection-shape captures
# committed up to 2026-09-01 (under
# `experiments/trace_synthesis/injection_shape/runs/`) and in W1 exchange
# records written before the same date. Those files are a
# record and must not be rewritten, so a reader has to know this string.
#
# A legacy alias is not a second source of truth. The difference is that this
# one is *closed* — no code path can produce it, so the set of files containing
# it can only shrink — whereas two live constants keep diverging, which is
# exactly what happened here: a scanner that knew only `[REDACTED]` called
# every one of those 37 properly-redacted captures dirty, 790 findings, all
# false.
LEGACY_REDACTED = "<redacted>"

# Every spelling of "this value was masked" that a reader must accept.
_REDACTED_MARKERS = frozenset({REDACTED, LEGACY_REDACTED})

# Headers whose *value* is a secret or an account identifier, lowercased for
# case-insensitive matching (HTTP header names are case-insensitive and the
# recorded casing is whatever the client or server sent).
#
# The canonical set. Every redactor and every checker in this repo reads it
# from here, so there is nothing for it to drift against.
SENSITIVE_HEADERS = frozenset(
    {
        # request — credentials
        "authorization",
        "x-api-key",
        "cookie",
        "proxy-authorization",
        # response — operator identity
        "anthropic-organization-id",
        "anthropic-workspace-id",
        # The counterpart of the request's `cookie` above. Recording what the
        # server sets while masking what the client sends protects nothing:
        # this is the value that later becomes that cookie. Observed on the
        # real OpenRouter path as a Cloudflare `__cf_bm`; the Anthropic path
        # sends no cookie at all.
        "set-cookie",
        # Identity despite the prefix: it names the account a limit is
        # claimed against. Deliberately kept out of any "Ratelimit-* is
        # telemetry" shortcut — that shortcut is how it was missed the
        # first time.
        "anthropic-ratelimit-unified-representative-claim",
    }
)

# ── Classification ───────────────────────────────────────────────────────
#
# Redaction is a **deny-list**: by construction a header nobody enumerated is
# recorded verbatim, so "unseen fields are redacted by default" is not what the
# code does (ADR-0012 §4). These sets are the reading-side answer — every header
# is *must-mask*, *deliberately kept*, or **unclassified**, and the third state
# is reported rather than published in silence.
#
# Names are listed one at a time, never as a prefix or a family. A pattern
# promises safety for names nobody has seen: keeping `Anthropic-Ratelimit-*`
# wholesale is what published `…-Representative-Claim`, which is identity.
#
# **The trigger for re-classification is the upstream, not the calendar.**
# Measured 2026-09-01: across 37 captures (158 records) the Anthropic response
# header space holds 26 names, and a fresh real response returned 25 of them
# with zero new. The only unseen field of that day arrived by switching
# upstream — `Set-Cookie`, from OpenRouter. So a stable upstream should stay
# quiet, and a new one should re-open the whole set.
Upstream = Literal["anthropic", "openrouter"]

# Protocol, transport, CDN and client-SDK headers — carry no account identity
# and no credential, whoever is serving.
_KEPT_ANY_UPSTREAM = frozenset(
    {
        # request — protocol and client SDK telemetry
        "accept",
        "anthropic-beta",
        "anthropic-dangerous-direct-browser-access",
        "anthropic-version",
        "connection",
        "content-length",
        "content-type",
        "user-agent",
        "x-app",
        # Run identifiers, deliberately kept: they are how a run is reconciled
        # against its trace, and masking them breaks that silently.
        "x-claude-code-agent-id",
        "x-claude-code-session-id",
        "x-stainless-arch",
        "x-stainless-lang",
        "x-stainless-os",
        "x-stainless-package-version",
        "x-stainless-retry-count",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
        "x-stainless-timeout",
        # response — transport, caching and CDN
        "cache-control",
        "cf-cache-status",
        "cf-ray",
        "content-security-policy",
        "date",
        "request-id",
        "server",
        "strict-transport-security",
        "traceresponse",
        "vary",
        "x-robots-tag",
    }
)

# Upstream-specific vocabulary. Splitting these out is what makes a change of
# upstream re-open the classification instead of inheriting the old answer.
_KEPT_BY_UPSTREAM: dict[str, frozenset[str]] = {
    # Rate-limit telemetry: quantities and timestamps, not identity. The one
    # member of this family that *is* identity is in SENSITIVE_HEADERS above,
    # which is why the family is spelled out rather than matched.
    "anthropic": frozenset(
        {
            "anthropic-ratelimit-unified-5h-reset",
            "anthropic-ratelimit-unified-5h-status",
            "anthropic-ratelimit-unified-5h-utilization",
            "anthropic-ratelimit-unified-7d-reset",
            "anthropic-ratelimit-unified-7d-status",
            "anthropic-ratelimit-unified-7d-utilization",
            "anthropic-ratelimit-unified-fallback-percentage",
            # Account *state*, not an account *identifier*: a status word
            # and a reason string. Kept deliberately — widening the mask
            # costs telemetry and buys nothing here.
            "anthropic-ratelimit-unified-overage-disabled-reason",
            "anthropic-ratelimit-unified-overage-status",
            "anthropic-ratelimit-unified-reset",
            "anthropic-ratelimit-unified-status",
        }
    ),
    "openrouter": frozenset(
        {
            "access-control-allow-origin",
            "access-control-expose-headers",
            "permissions-policy",
            "referrer-policy",
            "server-timing",
            "x-content-type-options",
            "x-generation-id",
        }
    ),
}


# Identity does not travel only in headers: Claude Code sends a per-account
# identifier in the request body. A header-only check calls such a capture
# clean while an operator identifier sits in it — which is what this line
# exists to stop.
BODY_IDENTITY_PATH = ("metadata", "user_id")


def redact_record(record: dict[str, object]) -> dict[str, object]:
  """Mask every sensitive field in one capture record, in place.

  The producer half of this module, so that whatever redacts and whatever
  checks agree by construction rather than by two people keeping two lists in
  step. Masks rather than drops: a missing field cannot be told apart from one
  that was never sent, and that distinction is most of debugging an auth
  failure.

  Args:
    record: One decoded capture record (``request`` / ``response`` objects).

  Returns:
    The same record, mutated in place and returned for convenience.
  """
  for side in ("request", "response"):
    half = record.get(side)
    if not isinstance(half, dict):
      continue
    headers = half.get("headers")
    if isinstance(headers, dict):
      for name in headers:
        if str(name).lower() in SENSITIVE_HEADERS:
          headers[name] = REDACTED
  body = record.get("request")
  body = body.get("body") if isinstance(body, dict) else None
  for key in BODY_IDENTITY_PATH[:-1]:
    body = body.get(key) if isinstance(body, dict) else None
  if isinstance(body, dict) and BODY_IDENTITY_PATH[-1] in body:
    body[BODY_IDENTITY_PATH[-1]] = REDACTED
  return record


def unredacted_fields(proxy_log: str) -> list[str]:
  """Find every sensitive header or body field recorded with a real value.

  Args:
    proxy_log: A proxy capture as written by ``cc-reverse-proxy`` — one JSON
      record per line. Unparseable lines are skipped rather than raised on: a
      capture truncated by a killed run is a normal artifact (records are
      appended whole, so a partial trailing line is expected), and this check
      must still be able to report on the records that did land.

  Returns:
    One finding per offending field, as ``"record <n> <side> <field>"`` with
    ``n`` counted from 1 over parsed records. Empty means the capture is clean,
    which is the only acceptable state for a stored artifact.
  """
  findings: list[str] = []
  for index, record in enumerate(_records(proxy_log), start=1):
    for side in ("request", "response"):
      findings += [
          f"record {index} {side} {name}"
          for name, value in _headers(record, side).items()
          if name.lower() in SENSITIVE_HEADERS
          and value not in _REDACTED_MARKERS
      ]
    if _body_identity(record) not in (None, *_REDACTED_MARKERS):
      findings.append(
          f"record {index} request body.{'.'.join(BODY_IDENTITY_PATH)}"
      )
  return findings


def _body_identity(record: dict[str, object]) -> object | None:
  """Return the request body's account identifier, or ``None`` if absent."""
  value: object = record.get("request")
  for key in ("body", *BODY_IDENTITY_PATH):
    if not isinstance(value, dict):
      return None
    value = value.get(key)
  return value


def _records(proxy_log: str) -> Iterator[dict[str, object]]:
  """Yield each parseable JSON object in a JSONL capture."""
  for line in proxy_log.splitlines():
    line = line.strip()
    if not line:
      continue
    try:
      record = json.loads(line)
    except json.JSONDecodeError:
      continue
    if isinstance(record, dict):
      yield record


def _headers(record: dict[str, object], side: str) -> dict[str, str]:
  """Return one side's recorded headers, or empty when shaped otherwise."""
  half = record.get(side)
  if not isinstance(half, dict):
    return {}
  headers = half.get("headers")
  if not isinstance(headers, dict):
    return {}
  return {
      str(name): value
      for name, value in headers.items()
      if isinstance(value, str)
  }


def unclassified_fields(proxy_log: str, *, upstream: Upstream) -> list[str]:
  """Find every header nobody has classified as sensitive or as safe to keep.

  The point is not that an unclassified header is dangerous — most are
  transport noise. It is that nothing else notices a new one. A deny-list
  publishes what it does not recognize, so without this the first sign of a
  new field is somebody finding it in a published artifact.

  Args:
    proxy_log: A proxy capture, one JSON record per line.
    upstream: Which server produced it. Classification is per-upstream, so
      reading a capture as the wrong one reports its whole vocabulary — that
      is the intended behaviour, not a false positive: a different server is a
      different header space and deserves a fresh look.

  Returns:
    One finding per unclassified header, as ``"record <n> <side> <name>"``.
  """
  kept = _KEPT_ANY_UPSTREAM | _KEPT_BY_UPSTREAM.get(upstream, frozenset())
  known = kept | SENSITIVE_HEADERS
  return [
      f"record {index} {side} {name}"
      for index, record in enumerate(_records(proxy_log), start=1)
      for side in ("request", "response")
      for name in _headers(record, side)
      if name.lower() not in known
  ]


def publication_blockers(proxy_log: str, *, upstream: Upstream) -> list[str]:
  """Return every reason this capture must not be published, empty if none.

  The gate between a scanned capture and anywhere it would be shared. Both
  checks have to be one decision, because they fail in ways that look alike
  from the outside and neither alone is enough: a capture can be perfectly
  redacted and still carry a field nobody has looked at.

  Args:
    proxy_log: A proxy capture, one JSON record per line.
    upstream: Which server produced it (see :func:`unclassified_fields`).

  Returns:
    Findings from both checks; empty means publishable as far as this can
    tell. **Necessary, not sufficient** — it reasons about the envelope, not
    about what the bodies contain.
  """
  return unredacted_fields(proxy_log) + unclassified_fields(
      proxy_log, upstream=upstream
  )
