"""A proxy capture must carry no credential and no operator identity.

The check covers **both directions**, because the two sides fail differently and
a capture is only safe if neither leaks: a credential arrives on the *request*
(`Authorization` / `X-Api-Key`) and the operator's account identity comes back
on the *response* (organization / workspace ids).
"""

from __future__ import annotations

import json

from swe_lab.harnesses.claude_code.redaction import (
    REDACTED,
    SENSITIVE_HEADERS,
    unredacted_headers,
)


def _record(
    *, authorization: str, organization: str, extra_request: str = ""
) -> str:
  """One capture record, shaped as cc-reverse-proxy writes it."""
  return json.dumps(
      {
          "request": {
              "headers": {
                  "Authorization": authorization,
                  "X-Api-Key": extra_request or authorization,
                  "Anthropic-Beta": "interleaved-thinking-2025-05-14",
                  "X-Claude-Code-Session-Id": "3f2b1c4d-session",
              },
              "body": {"messages": []},
          },
          "response": {
              "status": 200,
              "headers": {
                  "Anthropic-Organization-Id": organization,
                  "Anthropic-Workspace-Id": organization,
                  "Request-Id": "req_011Cabcd",
                  "Anthropic-Ratelimit-Unified-Status": "allowed",
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
      "record 1 response Anthropic-Organization-Id",
      "record 1 response Anthropic-Workspace-Id",
  ]


def test_identifiers_and_telemetry_are_not_treated_as_secrets() -> None:
  # The other direction of the same judgement, and the one that silently
  # costs signal if it drifts: a session id reconciles a run against its
  # trace, and the rate-limit family is telemetry. Neither is a credential,
  # and the redacted capture above keeps both verbatim without a finding.
  for name in (
      "x-claude-code-session-id",
      "request-id",
      "anthropic-beta",
      "anthropic-ratelimit-unified-status",
  ):
    assert name not in SENSITIVE_HEADERS


def test_findings_name_the_offending_record() -> None:
  # A capture is many records and only some may be bad, so a finding has to
  # say which one — "somewhere in this file" is not actionable.
  clean = _record(authorization=REDACTED, organization=REDACTED)
  dirty = _record(authorization="Bearer live", organization=REDACTED)
  findings = unredacted_headers(f"{clean}\n{dirty}\n")
  assert findings == [
      "record 2 request Authorization",
      "record 2 request X-Api-Key",
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
  ]
