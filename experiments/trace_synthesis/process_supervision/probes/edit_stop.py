#!/usr/bin/env python3
"""Stop only at an Edit/Write boundary (the commit point the old channel was blind at)."""
import json, os, sys, pathlib
payload = json.load(sys.stdin)
log = pathlib.Path(os.environ["PROBE_LOG"])
with log.open("a") as fh:
    fh.write(json.dumps({"event": payload.get("hook_event_name"),
                         "tool": payload.get("tool_name")}) + "\n")
if payload.get("tool_name") in ("Edit", "Write"):
    print(json.dumps({"continue": False, "stopReason": "supervisor: hold on, that edit needs review"}))
sys.exit(0)
