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
# This set is the one already accepted in this repo — ``SECRET_HEADERS`` in the
# injection-shape experiment's redactor, which task 09 names as the shape to
# start from — and `test_proxy_redaction.py` asserts it stays a superset of
# that one, so the two cannot drift apart again. (They are two copies of one
# fact, which is a defect in itself; converging them onto a single home is
# task 09's, and the assertion is a splint until then.)
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

# Identity does not travel only in headers: Claude Code sends a per-account
# identifier in the request body. A header-only check calls such a capture
# clean while an operator identifier sits in it — which is what this line
# exists to stop.
BODY_IDENTITY_PATH = ("metadata", "user_id")


def unredacted_headers(proxy_log: str) -> list[str]:
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
          if name.lower() in SENSITIVE_HEADERS and value != REDACTED
      ]
    if _body_identity(record) not in (None, REDACTED):
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
