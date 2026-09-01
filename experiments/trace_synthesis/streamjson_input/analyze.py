"""Turn one run directory into the numbers the report quotes.

Reads `runs/<variant>/{events.jsonl,meta.json}`, rebuilds the raw
`event_stream.jsonl` the repo's stream converter expects, and prints:

  1. the stdout event sequence (type/subtype, with tool names),
  2. what `event_stream_to_conversation` recovers (does the injected user
     message survive stream capture?),
  3. the session transcript record sequence, read from the run's **committed**
     `transcript.jsonl` (see `transcripts.py`) so every table in `REPORT.md` is
     recomputable from this directory alone, and
  4. a literal grep for the three resume artifacts.

A phase-structured run (`resume-control`, whose stdout lives in `phase1/` and
`phase2/`) is read through the same command.

Usage: python analyze.py runs/<variant> [more dirs...]
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))

import transcripts  # noqa: E402
from swe_lab.harnesses.claude_code.convert import (  # noqa: E402
    event_stream_to_conversation,
    proxy_log_to_conversation,
)

# The three artifacts the stop+resume path necessarily adds.
RESUME_ARTIFACTS = (
    "<system-reminder>",
    "Continue from where you left off.",
    "No response requested.",
)

MARKERS = ("BANANA", "MANGO", "Correction from the operator")


def event_files(run_dir: pathlib.Path) -> list[pathlib.Path]:
  """Return the run's stdout logs — one file, or one per phase, in order."""
  own = run_dir / "events.jsonl"
  if own.is_file():
    return [own]
  return sorted(run_dir.glob("phase*/events.jsonl"))


def out_events(run_dir: pathlib.Path) -> list[dict[str, object]]:
  events = []
  for path in event_files(run_dir):
    for line in path.read_text().splitlines():
      entry = json.loads(line)
      if entry["dir"] == "out":
        events.append(entry)
  return events


def raw_stream(run_dir: pathlib.Path) -> str:
  """Rebuild the agent's literal stream-json stdout (one event per line)."""
  return "".join(json.dumps(e["event"]) + "\n" for e in out_events(run_dir))


def describe(event: dict[str, object]) -> str:
  kind = str(event.get("type"))
  if kind == "system":
    return f"system/{event.get('subtype')}"
  message = event.get("message")
  if isinstance(message, dict):
    parts = []
    content = message.get("content")
    if isinstance(content, list):
      for block in content:
        if not isinstance(block, dict):
          continue
        btype = block.get("type")
        if btype == "tool_use":
          parts.append(f"tool_use:{block.get('name')}")
        elif btype == "tool_result":
          parts.append("tool_result")
        elif btype == "text":
          parts.append(f"text:{str(block.get('text'))[:60]!r}")
        else:
          parts.append(str(btype))
    elif isinstance(content, str):
      parts.append(f"text:{content[:60]!r}")
    return f"{kind}[{', '.join(parts)}]"
  if kind == "result":
    return f"result/{event.get('subtype')} result={str(event.get('result'))[:60]!r}"
  return kind


def record_label(record: dict[str, object]) -> str:
  kind = str(record.get("type"))
  if kind == "attachment":
    attachment = record.get("attachment")
    sub = attachment.get("type") if isinstance(attachment, dict) else "?"
    return f"attachment/{sub}"
  if kind in ("user", "assistant"):
    message = record.get("message")
    blocks = []
    if isinstance(message, dict):
      content = message.get("content")
      if isinstance(content, list):
        for block in content:
          if not isinstance(block, dict):
            continue
          btype = block.get("type")
          if btype == "tool_use":
            blocks.append(f"tool_use:{block.get('name')}")
          elif btype == "tool_result":
            blocks.append("tool_result")
          elif btype == "text":
            blocks.append(f"text:{str(block.get('text'))[:70]!r}")
          else:
            blocks.append(str(btype))
      elif isinstance(content, str):
        blocks.append(f"text:{content[:70]!r}")
    extra = ""
    if kind == "user":
      extra = (
          f" promptSource={record.get('promptSource')!r}"
          f" entrypoint={record.get('entrypoint')!r}"
          f" userType={record.get('userType')!r}"
      )
    return f"{kind}[{', '.join(blocks)}]{extra}"
  return kind


def report(run_dir: pathlib.Path) -> None:
  meta = json.loads((run_dir / "meta.json").read_text())
  print("=" * 78)
  print(f"RUN {run_dir.name}  session={meta['session_id']}")
  print(f"timeline: {meta['timeline']}")
  print("-" * 78)

  print("STDOUT EVENTS")
  for entry in out_events(run_dir):
    print(f"  {entry['dt']:>7}s  {describe(entry['event'])}")

  raw = raw_stream(run_dir)
  conversation = event_stream_to_conversation(raw)
  print("-" * 78)
  print(f"event_stream_to_conversation -> {len(conversation.messages)} messages")
  for message in conversation.messages:
    texts = [
        b.text[:70] for b in message.content if getattr(b, "text", None)
    ]
    print(f"  {message.role}: {texts}")
  stream_text = raw
  for marker in MARKERS:
    print(f"  stream contains {marker!r}: {marker in stream_text}")

  proxy = run_dir / "proxy.jsonl"
  if proxy.is_file():
    proxy_raw = proxy.read_text()
    conv = proxy_log_to_conversation(proxy_raw)
    print("-" * 78)
    print(f"proxy_log_to_conversation -> {len(conv.messages)} messages")
    for message in conv.messages:
      texts = [b.text[:70] for b in message.content if getattr(b, "text", None)]
      print(f"  {message.role}: {texts}")
    for marker in MARKERS:
      print(f"  proxy log contains {marker!r}: {marker in proxy_raw}")

  body, source = transcripts.load(run_dir)
  print("-" * 78)
  if not body:
    print("NO TRANSCRIPT FOUND (run `python transcripts.py <run-dir>`)")
    return
  print(f"TRANSCRIPT (source: {source})")
  for line in body.splitlines():
    print(f"  {record_label(json.loads(line))}")
  print("-" * 78)
  print("RESUME-ARTIFACT GREP (transcript / stream)")
  for artifact in RESUME_ARTIFACTS:
    print(
        f"  {artifact!r}: transcript={artifact in body} stream={artifact in raw}"
    )


def from_evidence(run_dir: pathlib.Path) -> None:
  """Print the same tables from the committed, redacted `evidence.json`.

  This is the path a fresh checkout takes: the raw captures are gitignored, so
  `report()` above cannot run there, and this reads the artifact that is
  actually in the repo.
  """
  data = json.loads((run_dir / "evidence.json").read_text())
  print("=" * 78)
  print(f"RUN {data['run']} (from evidence.json)")
  print(f"timeline: {data['meta'].get('timeline')}")
  print(f"sources: {data['sources']}")
  print("-" * 78)
  print("STDOUT EVENTS")
  for row in data["stdout_events"]:
    label = row.get("type")
    if row.get("subtype"):
      label = f"{label}/{row['subtype']}"
    extra = row.get("blocks") or row.get("result") or ""
    print(f"  {row['dt']:>8}s {row['dir']:>3}  {label} {extra}")
  print("-" * 78)
  print("TRANSCRIPT RECORDS")
  for row in data["transcript_records"]:
    label = row.get("attachment") and f"attachment/{row['attachment']}" or row["type"]
    fields = {
        k: row[k]
        for k in ("promptSource", "entrypoint", "origin", "isMeta", "reason")
        if k in row
    }
    print(f"  {label} {row.get('blocks', '')} {fields if fields else ''}")
  wire = data.get("wire")
  if wire:
    print("-" * 78)
    print(
        f"WIRE api_calls={wire['api_calls']} messages={len(wire['messages'])}"
        f" system_reminder_blocks={wire['system_reminder_blocks']}"
    )
    for row in wire["messages"]:
      print(f"  {row['i']} {row['role']} {row['blocks']} sr={row['system_reminder_blocks']}")
  print("-" * 78)
  print("ARTIFACT GREP (computed on the raw capture at build time)")
  for artifact, where in data["artifact_greps"].items():
    print(f"  {artifact!r}: {where}")


def main() -> int:
  args = [a for a in sys.argv[1:] if a != "--from-evidence"]
  if "--from-evidence" in sys.argv[1:]:
    for arg in args:
      from_evidence(pathlib.Path(arg))
    return 0
  for arg in args:
    report(pathlib.Path(arg))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
