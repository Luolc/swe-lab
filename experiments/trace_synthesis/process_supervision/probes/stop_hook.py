#!/usr/bin/env python3
"""PostToolUse probe: log the payload, and stop the session on the Nth call."""
import json, os, sys, pathlib

payload = json.load(sys.stdin)
log = pathlib.Path(os.environ["PROBE_LOG"])
log.parent.mkdir(parents=True, exist_ok=True)
n = sum(1 for _ in log.open()) if log.exists() else 0
with log.open("a") as fh:
    fh.write(json.dumps({"seq": n, "payload": payload}) + "\n")
if n + 1 == int(os.environ.get("PROBE_STOP_AT", "1")):
    print(json.dumps({"continue": False,
                      "stopReason": "supervisor: this step is off track; stopping for review"}))
sys.exit(0)
