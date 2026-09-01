#!/usr/bin/env python3
import json, os, sys, pathlib
payload = json.load(sys.stdin)
log = pathlib.Path(os.environ["PROBE_LOG"])
n = sum(1 for _ in log.open()) if log.exists() else 0
log.open("a").write(json.dumps({"seq": n, "tool": payload.get("tool_name")}) + "\n")
if n + 1 == 2:
    print(json.dumps({"continue": False}))   # no stopReason at all
