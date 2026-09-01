#!/usr/bin/env python3
"""Regenerate every number FEASIBILITY-A.md quotes, from `runs/`.

    uv run python experiments/trace_synthesis/process_supervision/analyze.py

Reads only the checked-in artifacts — no network, no `claude` invocation. The
proxy captures were redacted with this repo's own
:mod:`swe_lab.harnesses.claude_code.redaction` before being committed; the
`--check-redaction` flag re-asserts that.

**The pilot-scale figures come from another component's run ledger**, which
lives off-repo and in no git repository at all. A reduced, attributable
snapshot of it is committed beside the runs (`runs/pilot_ledger.jsonl`, with
`runs/pilot_ledger.provenance.json` recording the source path, its sha256 and
which fields were kept), so those figures regenerate here like every other one.
`--freeze-pilot` is the one-shot that produced the snapshot; it is the **only**
mode that reads outside this directory, and it runs only where the source
ledger exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
RUNS = HERE / "runs"

# The honesty-scorer pilot's ledger, as it sits on the machine that ran it.
# Read only by `--freeze-pilot`.
PILOT_SOURCE = pathlib.Path(
    "/home/ubuntu/dev/swe-lab-artifacts/honesty_scorer/pilot/ledger.jsonl"
)
PILOT_FROZEN = RUNS / "pilot_ledger.jsonl"
PILOT_PROVENANCE = RUNS / "pilot_ledger.provenance.json"

# What the snapshot keeps: the five statistics the report quotes, the keys that
# identify a row, and the commit that produced it. Everything else is dropped —
# including `frozen_to`, an absolute path on the operator's machine.
PILOT_FIELDS = (
    "cell",
    "class",
    "instance_id",
    "slot",
    "execution_no",
    "git_commit",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_cost_usd",
    "wall_seconds",
    "rollout_wall_seconds",
    "num_turns",
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


def freeze_pilot() -> int:
  """Snapshot the off-repo pilot ledger into `runs/`, with its provenance.

  One-shot, and the only mode that reads outside this directory. The source is
  another component's run ledger on this machine and is in no git repository,
  so without a snapshot the report's scale figures would be unauditable by
  anyone else — which is precisely the claim this experiment made and could
  not keep.

  Returns:
    0 on success, 1 when the source ledger is not on this machine.
  """
  if not PILOT_SOURCE.exists():
    print(f"pilot ledger not on this machine: {PILOT_SOURCE}")
    return 1
  raw = PILOT_SOURCE.read_bytes()
  rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
  reduced = [{k: row[k] for k in PILOT_FIELDS if k in row} for row in rows]
  PILOT_FROZEN.write_text(
      "\n".join(json.dumps(row, sort_keys=True) for row in reduced) + "\n"
  )
  PILOT_PROVENANCE.write_text(
      json.dumps(
          {
              "source_path": str(PILOT_SOURCE),
              "source_sha256": hashlib.sha256(raw).hexdigest(),
              "source_rows": len(rows),
              "frozen_rows": len(reduced),
              "fields_kept": list(PILOT_FIELDS),
              "dropped": sorted(
                  set().union(*(row.keys() for row in rows)) - set(PILOT_FIELDS)
              ),
              "produced_by": (
                  "analyze.py --freeze-pilot"
                  " (experiments/trace_synthesis/process_supervision)"
              ),
              "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "note": (
                  "The honesty-scorer pilot's own ledger is the source of"
                  " truth; this is a dated, field-reduced snapshot taken so"
                  " FEASIBILITY-A's scale figures can be rederived from this"
                  " directory alone."
              ),
          },
          indent=2,
          sort_keys=True,
      )
      + "\n"
  )
  print(f"froze {len(reduced)} rows -> {PILOT_FROZEN}")
  return 0


def ledger_scale() -> dict:
  """Calibrate against the 20 honesty-scorer pilot attempts.

  Reads the committed snapshot, never the off-repo original: a figure the
  report quotes has to be rederivable in a fresh checkout.

  Returns:
    Mean and median of the five statistics, plus the derived prefix scale.

  Raises:
    FileNotFoundError: If the committed snapshot is missing, which is a broken
      checkout rather than a machine without the pilot — the earlier "skipped"
      branch made those two look alike.
  """
  if not PILOT_FROZEN.exists():
    raise FileNotFoundError(
        f"{PILOT_FROZEN} is committed and missing; re-take it with"
        " `analyze.py --freeze-pilot` on a machine holding"
        f" {PILOT_SOURCE}"
    )
  rows = [
      json.loads(line)
      for line in PILOT_FROZEN.read_text().splitlines()
      if line.strip()
  ]
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
  parser.add_argument("--freeze-pilot", action="store_true")
  args = parser.parse_args()
  if args.check_redaction:
    return 1 if check_redaction() else 0
  if args.freeze_pilot:
    return freeze_pilot()

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

  print("\n== honesty-scorer pilot ledger (scale calibration) ==")
  print(f"  source: {PILOT_FROZEN.name} (see {PILOT_PROVENANCE.name})")
  for key, value in ledger_scale().items():
    print(f"  {key}: {value}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
