"""Extract one record per assistant step from captured proxy logs.

A *step* is one assistant message: the unit a per-step supervisor would hold
before forwarding. Text and tool_use blocks of the same message belong to the
same step, because they arrive in the same response.

Usage:
  python3 extract_steps.py --out steps.json <rollout> [<rollout> ...]
"""

import argparse
import json
import pathlib

ARTIFACTS = pathlib.Path("~/dev/swe-lab-artifacts/trace_synthesis").expanduser()


def blocks(message: dict) -> list[dict]:
  return [b for b in (message.get("content") or []) if isinstance(b, dict)]


def is_agent_turn(body: dict) -> bool:
  """An agent turn carries the tool set; Claude Code's side calls do not.

  The capture also contains auxiliary requests -- conversation-title generation
  asks for a JSON object with a `title` property and no tools. Judging those as
  trajectory steps would measure a population the question is not about.
  """
  schema = (((body.get("output_config") or {}).get("format") or {}).get("schema") or {})
  wants_title = "title" in (schema.get("properties") or {})
  return bool(body.get("tools")) and not wants_title


def summarize(message: dict, limit: int = 400) -> str:
  parts = []
  for b in blocks(message):
    if b.get("type") == "text":
      parts.append(f"[text] {(b.get('text') or '').strip()[:limit]}")
    elif b.get("type") == "tool_use":
      parts.append(f"[tool_use {b.get('name')}] {json.dumps(b.get('input') or {})[:limit]}")
  return "\n".join(parts)


def extract(rollout: str) -> list[dict]:
  path = ARTIFACTS / rollout / "rollout/a0/claude_code.proxy_log.jsonl"
  steps = []
  for index, line in enumerate(path.read_text().splitlines()):
    record = json.loads(line)
    if not is_agent_turn(record.get("request", {}).get("body") or {}):
      continue
    message = (record.get("response") or {}).get("message") or {}
    if not blocks(message):
      continue
    steps.append({
        "rollout": rollout,
        "step_index": index,
        "stop_reason": message.get("stop_reason"),
        "tool_names": [b.get("name") for b in blocks(message) if b.get("type") == "tool_use"],
        "content": summarize(message),
    })
  return steps


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("rollouts", nargs="+")
  parser.add_argument("--out", type=pathlib.Path, required=True)
  args = parser.parse_args()
  steps = [s for rollout in args.rollouts for s in extract(rollout)]
  args.out.write_text(json.dumps(steps, indent=2))
  print(f"{len(steps)} steps -> {args.out}")


if __name__ == "__main__":
  main()
