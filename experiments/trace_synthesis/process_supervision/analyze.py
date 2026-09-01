#!/usr/bin/env python3
"""Regenerate every number FEASIBILITY-A.md quotes, from `runs/`.

    uv run python experiments/trace_synthesis/process_supervision/analyze.py

Reads only the checked-in artifacts — no network, no `claude` invocation. The
proxy captures were redacted with this repo's own
:mod:`swe_lab.harnesses.claude_code.redaction` before being committed; the
`--check-redaction` flag re-asserts that.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE / "runs"
LEDGER = pathlib.Path(
    "/home/ubuntu/dev/swe-lab-artifacts/honesty_scorer/pilot/ledger.jsonl"
)


def events(name: str) -> list[dict]:
  path = RUNS / name
  return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def turn_kinds(event: dict) -> list[str]:
  content = (event.get("message") or {}).get("content")
  if isinstance(content, list):
    return [block.get("type") for block in content]
  return ["STR"] if isinstance(content, str) else []


def stream_shape(name: str) -> dict:
  """What a `stream-json` capture of one segment actually carries."""
  evs = events(name)
  users = [e for e in evs if e.get("type") == "user"]
  return {
      "events": len(evs),
      "user_events": len(users),
      "user_text_turns": sum(1 for e in users if "text" in turn_kinds(e)),
      "tool_result_turns": sum(1 for e in users if "tool_result" in turn_kinds(e)),
      "assistant_events": sum(1 for e in evs if e.get("type") == "assistant"),
      "result_subtype": next(
          (e.get("subtype") for e in reversed(evs) if e.get("type") == "result"), None
      ),
      "result_stop_reason": next(
          (e.get("stop_reason") for e in reversed(evs) if e.get("type") == "result"),
          None,
      ),
  }


def proxy_requests(name: str) -> list[dict]:
  """One row per captured API request: prefix size and cache accounting."""
  rows = []
  for record in events(name):
    body = (record.get("request") or {}).get("body") or {}
    usage = ((record.get("response") or {}).get("message") or {}).get("usage") or {}
    rows.append({
        "n_messages": len(body.get("messages") or []),
        "input": usage.get("input_tokens"),
        "cache_read": usage.get("cache_read_input_tokens"),
        "cache_write": usage.get("cache_creation_input_tokens"),
        "output": usage.get("output_tokens"),
    })
  return rows


def texts(record: dict) -> list[str]:
  """Every string a captured request put in front of the model."""
  body = (record.get("request") or {}).get("body") or {}
  out = []
  for message in body.get("messages") or []:
    content = message["content"]
    if isinstance(content, str):
      out.append(content)
      continue
    for block in content:
      for key in ("text", "thinking", "content"):
        value = block.get(key)
        if isinstance(value, str):
          out.append(value)
  return out


def converter_survival() -> dict:
  """Does the injected user turn survive each of this repo's two converters?"""
  from swe_lab.harnesses.claude_code.convert import (
      event_stream_to_conversation,
      proxy_log_to_conversation,
  )

  stitched = (RUNS / "r5a.stream.jsonl").read_text() + (
      RUNS / "r5b.stream.jsonl"
  ).read_text()
  stream = event_stream_to_conversation(stitched)
  # One run's capture, not the whole shared log: `r5.proxy.jsonl` holds four
  # sessions because every probe pointed at the same proxy, and
  # `proxy_log_to_conversation` reads only the *last* record.
  proxy = proxy_log_to_conversation((RUNS / "r5.session130.proxy.jsonl").read_text())

  def carries(conversation, needle: str) -> bool:
    return any(
        needle in str(getattr(block, "text", ""))
        or needle in str(getattr(block, "content", ""))
        for message in conversation.messages
        for block in message.content
    )

  return {
      "stream_messages": len(stream.messages),
      "stream_has_hint": carries(stream, "oracle_hint"),
      "stream_has_repair_pair": carries(stream, "No response requested"),
      "proxy_messages": len(proxy.messages),
      "proxy_has_hint": carries(proxy, "oracle_hint"),
      "proxy_has_repair_pair": carries(proxy, "No response requested"),
      "proxy_has_stop_reminder": carries(proxy, "hook stopped continuation"),
  }


def ledger_scale() -> dict | None:
  """Calibrate against the 20 honesty-scorer pilot attempts, if present."""
  if not LEDGER.exists():
    return None
  rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
  out = {"n": len(rows)}
  for key in (
      "cache_read_input_tokens",
      "cache_creation_input_tokens",
      "total_cost_usd",
      "rollout_wall_seconds",
      "num_turns",
  ):
    values = [r[key] for r in rows if key in r]
    out[key] = {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
    }
  out["mean_prefix_tokens_per_turn"] = (
      out["cache_read_input_tokens"]["mean"] / out["num_turns"]["mean"]
  )
  return out


def check_redaction() -> int:
  from swe_lab.harnesses.claude_code.redaction import (
      unclassified_fields,
      unredacted_fields,
  )

  bad = 0
  for path in sorted(RUNS.glob("*.proxy.jsonl")):
    raw = path.read_text()
    findings = unredacted_fields(raw) + unclassified_fields(raw, upstream="anthropic")
    print(f"{path.name}: {len(findings)} findings")
    bad += len(findings)
  return bad


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--check-redaction", action="store_true")
  args = parser.parse_args()
  if args.check_redaction:
    return 1 if check_redaction() else 0

  print("== stream shape per segment (what event_stream capture carries) ==")
  for name in ("r5a.stream.jsonl", "r5b.stream.jsonl", "rD.stream.jsonl"):
    print(f"  {name}: {stream_shape(name)}")

  print("\n== r5 proxy: every API request, in order ==")
  for i, row in enumerate(proxy_requests("r5.proxy.jsonl")):
    print(f"  rec {i:2d} {row}")

  print("\n== what the first resumed request put in front of the model ==")
  first_resume = next(
      record
      for record in events("r5.proxy.jsonl")
      if any("Continue from where you left off" in line for line in texts(record))
  )
  for line in texts(first_resume)[-4:]:
    print(f"  {line[:120]!r}")

  print("\n== stop with no stopReason (r6, last request) ==")
  for line in texts(events("r6.proxy.jsonl")[-1]):
    if "hook stopped continuation" in line or "Continue from where" in line:
      print(f"  {line[-160:]!r}")

  print("\n== converter survival ==")
  for key, value in converter_survival().items():
    print(f"  {key}: {value}")

  scale = ledger_scale()
  print("\n== honesty-scorer pilot ledger (scale calibration) ==")
  if scale is None:
    print("  ledger not on this machine — skipped")
  else:
    for key, value in scale.items():
      print(f"  {key}: {value}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
