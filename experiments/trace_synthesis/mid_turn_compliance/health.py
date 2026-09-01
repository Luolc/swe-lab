"""Report the run conditions a batch produced: failures, throttling, timing.

Concurrency is calibrated against this, not against a guess. A rate-limited or
timed-out run is an infrastructure failure under §9 and may be re-run once, so
provoking one by fanning out too wide spends a budget the protocol only grants
for accidents.

    ./health.py runs/pilot/pos/*
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Upstream pushback as it shows up in a proxy record or on the CLI's stderr.
THROTTLE = re.compile(
    r'"status"\s*:\s*(429|529)|rate_limit|overloaded_error|"type"\s*:\s*"error"',
    re.IGNORECASE,
)


def main() -> int:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("runs", nargs="+")
  args = parser.parse_args()

  rows = []
  for run in args.runs:
    run_dir = pathlib.Path(run)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
      rows.append({"run": run_dir.name, "status": "no manifest — never finished"})
      continue
    manifest = json.loads(manifest_path.read_text())
    hits = 0
    for name in ("proxy.jsonl", "stderr.log"):
      path = run_dir / name
      if path.is_file():
        hits += len(THROTTLE.findall(path.read_text(errors="replace")))
    started = manifest.get("started_at", "")
    ended = manifest.get("ended_at", "")
    rows.append({
        "run": f"{run_dir.parent.name}/{run_dir.name}",
        "concurrency": manifest.get("concurrency"),
        "exit_code": manifest.get("exit_code"),
        "timed_out": manifest.get("timed_out"),
        "trigger_fired": manifest.get("trigger_fired"),
        "throttle_hits": hits,
        "started_at": started,
        "ended_at": ended,
    })

  bad = [r for r in rows if r.get("timed_out") or r.get("throttle_hits")]
  print(json.dumps({
      "runs": rows,
      "totals": {
          "runs": len(rows),
          "with_throttling_or_timeout": len(bad),
          "no_trigger": sum(1 for r in rows if r.get("trigger_fired") is False),
      },
  }, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
