"""A proxy capture must carry no credential and no operator identity.

The check covers **both directions**, because the two sides fail differently and
a capture is only safe if neither leaks: a credential arrives on the *request*
(`Authorization` / `X-Api-Key`) and the operator's account identity comes back
on the *response* (organization / workspace ids).
"""

from __future__ import annotations

import json

from swe_lab.harnesses.claude_code.redaction import (
    BODY_IDENTITY_PATH,
    REDACTED,
    SENSITIVE_HEADERS,
    unredacted_headers,
)


def _record(
    *, authorization: str, organization: str, user_id: str = REDACTED
) -> str:
  """One capture record, shaped as cc-reverse-proxy writes it."""
  return json.dumps(
      {
          "request": {
              "headers": {
                  "Authorization": authorization,
                  "X-Api-Key": authorization,
                  "Cookie": authorization,
                  "Proxy-Authorization": authorization,
                  "Anthropic-Beta": "interleaved-thinking-2025-05-14",
                  "X-Claude-Code-Session-Id": "3f2b1c4d-session",
              },
              "body": {"messages": [], "metadata": {"user_id": user_id}},
          },
          "response": {
              "status": 200,
              "headers": {
                  "Anthropic-Organization-Id": organization,
                  "Anthropic-Workspace-Id": organization,
                  "Anthropic-Ratelimit-Unified-Representative-Claim": (
                      organization
                  ),
                  "Request-Id": "req_011Cabcd",
                  "Anthropic-Ratelimit-Unified-Status": "allowed",
                  "Anthropic-Ratelimit-Unified-Reset": "2026-09-01T12:00:00Z",
              },
          },
      }
  )


def test_a_redacted_capture_is_clean() -> None:
  # What the proxy writes by default: the header names survive, the values
  # do not. This is the state a stored artifact must be in.
  capture = _record(authorization=REDACTED, organization=REDACTED) + "\n"
  assert unredacted_headers(capture) == []


def test_a_raw_capture_is_caught_on_both_sides() -> None:
  # The failure this exists to prevent, asserted in both directions at once:
  # a credential on the request and the operator's identity on the response.
  capture = (
      _record(
          authorization="Bearer sk-ant-live-value",
          organization="org_realvalue",
      )
      + "\n"
  )
  findings = unredacted_headers(capture)
  assert findings == [
      "record 1 request Authorization",
      "record 1 request X-Api-Key",
      "record 1 request Cookie",
      "record 1 request Proxy-Authorization",
      "record 1 response Anthropic-Organization-Id",
      "record 1 response Anthropic-Workspace-Id",
      "record 1 response Anthropic-Ratelimit-Unified-Representative-Claim",
  ]


def test_an_account_id_in_the_request_body_is_caught() -> None:
  # Identity does not travel only in headers, and a header-only check would
  # call this capture clean. That is the defect this test exists for: every
  # header is masked and the record is still not safe to keep.
  capture = (
      _record(
          authorization=REDACTED,
          organization=REDACTED,
          user_id="account-identifier",
      )
      + "\n"
  )
  assert unredacted_headers(capture) == [
      "record 1 request body.metadata.user_id"
  ]


def test_the_scanner_covers_the_set_this_repo_already_accepted() -> None:
  # The root cause of the miss above: two copies of one fact. The experiment's
  # redactor is the accepted set (task 09 names it as the shape to start from),
  # and this scanner was written narrower without consulting it.
  #
  # Asserting coverage stops the drift from recurring. It is a splint, not the
  # cure — it only proves this set is no *smaller*, so a gap in the accepted
  # set would propagate. Converging them onto one home is task 09's.
  accepted = _accepted_secret_headers()
  assert accepted <= SENSITIVE_HEADERS, sorted(accepted - SENSITIVE_HEADERS)
  assert BODY_IDENTITY_PATH == ("metadata", "user_id")


def _accepted_secret_headers() -> frozenset[str]:
  """Load ``SECRET_HEADERS`` from the injection-shape experiment's redactor.

  Through the loader that already exists for it, rather than a second copy of
  the by-path import dance — writing that twice would be the same duplication
  this test is here to catch.
  """
  from .test_injection_shape_redaction import _load_driver

  return frozenset(_load_driver().SECRET_HEADERS)


def test_identifiers_and_telemetry_are_not_treated_as_secrets() -> None:
  # The other direction of the same judgement, and the one that silently
  # costs signal if it drifts: a session id reconciles a run against its
  # trace, and the rate-limit family is telemetry. Neither is a credential,
  # and the redacted capture above keeps both verbatim without a finding.
  # Enumerated one by one on purpose. The retained rate-limit fields are named
  # individually rather than exempted as a family, because the family also
  # holds `…-Representative-Claim`, which is identity — and a wildcard keep-rule
  # is exactly what let that one through the first time. A keep-list is a list.
  for name in (
      "x-claude-code-session-id",
      "request-id",
      "anthropic-beta",
      "anthropic-ratelimit-unified-status",
      "anthropic-ratelimit-unified-reset",
  ):
    assert name not in SENSITIVE_HEADERS
  assert "anthropic-ratelimit-unified-representative-claim" in SENSITIVE_HEADERS


def test_findings_name_the_offending_record() -> None:
  # A capture is many records and only some may be bad, so a finding has to
  # say which one — "somewhere in this file" is not actionable.
  clean = _record(authorization=REDACTED, organization=REDACTED)
  dirty = _record(authorization="Bearer live", organization=REDACTED)
  findings = unredacted_headers(f"{clean}\n{dirty}\n")
  assert findings == [
      "record 2 request Authorization",
      "record 2 request X-Api-Key",
      "record 2 request Cookie",
      "record 2 request Proxy-Authorization",
  ]


def test_a_truncated_capture_is_still_checkable() -> None:
  # A run killed at its deadline leaves a partial trailing line (records are
  # appended whole). That is a normal artifact, and the check must report on
  # the records that did land rather than refuse the file.
  clean = _record(authorization=REDACTED, organization=REDACTED)
  dirty = _record(authorization="Bearer live", organization=REDACTED)
  truncated = f"{clean}\n{dirty}\n" + '{"request": {"headers": {"Auth'
  assert unredacted_headers(truncated) == [
      "record 2 request Authorization",
      "record 2 request X-Api-Key",
      "record 2 request Cookie",
      "record 2 request Proxy-Authorization",
  ]
