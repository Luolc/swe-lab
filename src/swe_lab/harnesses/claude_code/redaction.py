"""Check that a proxy capture carries no credential and no account identity.

Proxy capture records whole HTTP exchanges, so unlike a stream trace it has an
*envelope*: the request's `Authorization` / `X-Api-Key` and the response's
Anthropic organization and workspace ids. Those must never reach a stored
artifact — a log is the thing that gets copied into an issue, pushed to a
dataset repo, or handed to another tool, and a credential in a file travels much
further than the same credential in an environment variable.

**Redaction itself happens upstream of this module, at write time**, inside
`cc-reverse-proxy`: it masks those four values as it records each exchange, so a
raw artifact never exists on disk. That is the fix; after-the-fact cleanup is
not equivalent, because it leaves a window in which the unredacted file is
there. ADR-0012 §4 carries the decision.

What lives *here* is the check that the fix is actually in force, which is a
different job and belongs on this side: the proxy is an external, separately
versioned binary, and "the build we ran redacts" is exactly the kind of
assumption that stops being true without anyone noticing. So this module answers
one question about a capture we already have — *does any record still hold a
real secret?* — and the answer is a list of findings rather than a bool, because
"which header, in which record" is what a person needs in order to act.
"""

from __future__ import annotations

from collections.abc import Iterator
import json

# What cc-reverse-proxy writes in place of a masked value.
REDACTED = "[REDACTED]"

# Headers whose *value* is a secret or an account identifier, lowercased for
# case-insensitive matching (HTTP header names are case-insensitive and the
# recorded casing is whatever the client or server sent).
#
# Deliberately short, and what is absent matters as much as what is present:
# `X-Claude-Code-Session-Id` is an identifier rather than a credential and is
# load-bearing for reconciling a run against its trace, and `Request-Id`,
# `Anthropic-Beta` and the rate-limit family are telemetry and protocol. None of
# them are secrets, and treating them as such would cost real signal.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "anthropic-organization-id",
        "anthropic-workspace-id",
    }
)


def unredacted_headers(proxy_log: str) -> list[str]:
  """Find every sensitive header recorded with a real value.

  Args:
    proxy_log: A proxy capture as written by ``cc-reverse-proxy`` — one JSON
      record per line. Unparseable lines are skipped rather than raised on: a
      capture truncated by a killed run is a normal artifact (records are
      appended whole, so a partial trailing line is expected), and this check
      must still be able to report on the records that did land.

  Returns:
    One finding per offending header, as ``"record <n> <side> <Header>"``
    with ``n`` counted from 1 over parsed records. Empty means the capture is
    clean, which is the only acceptable state for a stored artifact.
  """
  return [
      f"record {index} {side} {name}"
      for index, record in enumerate(_records(proxy_log), start=1)
      for side in ("request", "response")
      for name, value in _headers(record, side).items()
      if name.lower() in SENSITIVE_HEADERS and value != REDACTED
  ]


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
