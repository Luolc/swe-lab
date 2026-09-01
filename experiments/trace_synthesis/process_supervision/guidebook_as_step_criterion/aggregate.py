"""Recompute every figure in REPORT.md from the preserved raw responses.

Runs offline. Later verdict files override earlier ones for the same step, so
a re-judged step replaces its truncated first answer; the merge is therefore
deterministic given the same file order.

Usage:
  python3 aggregate.py --verdicts verdicts.jsonl [--verdicts retry.jsonl] \
                       --guidebook <path> [--out summary.json]
"""

import argparse
import collections
import json
import pathlib
import re

NORMALIZE = lambda text: re.sub(r"\s+", " ", text.replace("`", "").replace("**", "")).strip()


def parse(raw: str | None) -> dict | None:
  if not raw:
    return None
  match = re.search(r"\{.*\}", raw, re.S)
  if not match:
    return None
  try:
    return json.loads(match.group(0))
  except json.JSONDecodeError:
    return None


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--verdicts", type=pathlib.Path, action="append", required=True)
  parser.add_argument("--guidebook", type=pathlib.Path, required=True)
  parser.add_argument("--out", type=pathlib.Path)
  args = parser.parse_args()

  guidebook = args.guidebook.read_text()
  normalized_guidebook = NORMALIZE(guidebook)

  merged: dict[tuple[str, int], dict] = {}
  attempts = collections.Counter()
  for path in args.verdicts:
    for line in path.read_text().splitlines():
      record = json.loads(line)
      key = (record["rollout"], record["position"])
      attempts[key] += 1
      merged[key] = record

  rows = []
  for key, record in sorted(merged.items()):
    verdict = parse(record.get("raw"))
    rows.append({"key": key, "record": record, "verdict": verdict})

  judged = len(rows)
  unparseable = [r for r in rows if r["verdict"] is None]
  parsed = [r for r in rows if r["verdict"] is not None]
  adjudicable = [r for r in parsed if r["verdict"].get("adjudicable")]
  off_track = [r for r in adjudicable if r["verdict"].get("verdict") == "off_track"]

  def quote(row):
    return (row["verdict"].get("quote") or "").strip()

  literal = [r for r in adjudicable if quote(r) and quote(r) in guidebook]
  normalized = [r for r in adjudicable if quote(r) and NORMALIZE(quote(r)) in normalized_guidebook]

  summary = {
      "steps_judged": judged,
      "parsed": len(parsed),
      "unparseable": len(unparseable),
      "unparseable_steps": [
          {"rollout": r["key"][0], "position": r["key"][1],
           "max_tokens": r["record"].get("max_tokens"),
           "completion_tokens": (r["record"].get("usage") or {}).get("completion_tokens")}
          for r in unparseable
      ],
      "adjudicable": len(adjudicable),
      "silent": len(parsed) - len(adjudicable),
      "verdicts": dict(collections.Counter(r["verdict"].get("verdict") for r in adjudicable)),
      "stages_cited": dict(sorted(
          collections.Counter(r["verdict"].get("stage") for r in adjudicable).items(),
          key=lambda kv: (kv[0] is None, kv[0]))),
      "quotes_literal": len(literal),
      "quotes_normalized": len(normalized),
      "off_track": len(off_track),
      "off_track_literal": sum(1 for r in off_track if quote(r) in guidebook),
      "per_rollout": {
          rollout: {
              "steps": sum(1 for r in parsed if r["key"][0] == rollout),
              "adjudicable": sum(1 for r in adjudicable if r["key"][0] == rollout),
              "off_track": sum(1 for r in off_track if r["key"][0] == rollout),
          }
          for rollout in sorted({r["key"][0] for r in parsed})
      },
      "re_judged_steps": sum(1 for k, n in attempts.items() if n > 1),
      "cost_usd": round(sum((r["record"].get("usage") or {}).get("cost", 0) for r in rows), 6),
  }
  text = json.dumps(summary, indent=2)
  print(text)
  if args.out:
    args.out.write_text(text)


if __name__ == "__main__":
  main()
