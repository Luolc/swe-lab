"""One home for *which* fields are sensitive, and *what* replaces them.

The same fact used to be written down three times — the rollout checker, the
injection-shape experiment's post-run redactor, and W1's exchange builder — and
the copies drifted twice, in membership and in representation. These tests pin
the direction of the dependency: `src` owns the fact, everyone else imports it.
"""

from __future__ import annotations

import json
from typing import Any

from swe_lab.harnesses.claude_code.redaction import (
    LEGACY_REDACTED,
    redact_record,
    REDACTED,
    SENSITIVE_HEADERS,
    unredacted_fields,
)


def _raw_record() -> dict[str, Any]:
  """Build a capture record as the proxy writes it with redaction off."""
  return {
      "request": {
          "headers": {
              "Authorization": "Bearer sk-ant-secret",
              "X-Api-Key": "sk-ant-key",
              "Cookie": "session=secret",
              "Proxy-Authorization": "Basic secret",
              "X-Claude-Code-Session-Id": "session-kept",
          },
          "body": {"metadata": {"user_id": "account-id"}, "messages": []},
      },
      "response": {
          "headers": {
              "Anthropic-Organization-Id": "org_secret",
              "Anthropic-Workspace-Id": "wrkspc_secret",
              "Anthropic-Ratelimit-Unified-Representative-Claim": "claim",
              "Set-Cookie": "__cf_bm=state",
              "Request-Id": "req_kept",
          }
      },
  }


def test_redacting_a_record_leaves_nothing_for_the_checker_to_find() -> None:
  # The producer and the checker share one definition, so a record this
  # module redacts is a record this module calls clean. When they were two
  # definitions, that round trip did not hold — which is the whole defect.
  redacted: dict[str, Any] = redact_record(_raw_record())
  assert unredacted_fields(json.dumps(redacted)) == []


def test_redaction_masks_rather_than_drops() -> None:
  # A dropped field is indistinguishable from one that was never sent.
  redacted: dict[str, Any] = redact_record(_raw_record())
  headers = redacted["request"]["headers"]
  assert headers["Authorization"] == REDACTED
  assert redacted["request"]["body"]["metadata"]["user_id"] == REDACTED


def test_deliberately_kept_fields_survive_redaction() -> None:
  # Over-redaction is the failure that costs signal instead of safety.
  redacted: dict[str, Any] = redact_record(_raw_record())
  assert (
      redacted["request"]["headers"]["X-Claude-Code-Session-Id"]
      == "session-kept"
  )
  assert redacted["response"]["headers"]["Request-Id"] == "req_kept"


def test_the_legacy_placeholder_is_accepted_but_never_written() -> None:
  # The 37 committed captures are a record and must not be rewritten, so the
  # checker has to read `<redacted>`. That is a closed, dated alias — not a
  # second live constant — so nothing new writes it.
  legacy: dict[str, Any] = _raw_record()
  legacy["request"]["headers"]["Authorization"] = LEGACY_REDACTED
  legacy["request"]["body"]["metadata"]["user_id"] = LEGACY_REDACTED
  for name in ("X-Api-Key", "Cookie", "Proxy-Authorization"):
    legacy["request"]["headers"][name] = LEGACY_REDACTED
  for name in (
      "Anthropic-Organization-Id",
      "Anthropic-Workspace-Id",
      "Anthropic-Ratelimit-Unified-Representative-Claim",
      "Set-Cookie",
  ):
    legacy["response"]["headers"][name] = LEGACY_REDACTED
  assert unredacted_fields(json.dumps(legacy)) == []
  assert REDACTED != LEGACY_REDACTED
  fresh: dict[str, Any] = redact_record(_raw_record())
  assert fresh["request"]["headers"]["Authorization"] == REDACTED


def test_every_sensitive_header_is_actually_redacted_by_the_producer() -> None:
  # Guards the seam between the set and the code that applies it: a name added
  # to SENSITIVE_HEADERS that redact_record never touches would be a set that
  # lies.
  record: dict[str, Any] = _raw_record()
  request_headers = record["request"]["headers"]
  response_headers = record["response"]["headers"]
  for name in SENSITIVE_HEADERS:
    if name not in {h.lower() for h in request_headers}:
      response_headers[name] = "planted-value"
  redacted: dict[str, Any] = redact_record(record)
  remaining = {
      name: value
      for side in ("request", "response")
      for name, value in redacted[side]["headers"].items()
      if name.lower() in SENSITIVE_HEADERS and value != REDACTED
  }
  assert remaining == {}
