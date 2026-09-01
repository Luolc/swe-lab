#!/usr/bin/env python3
import json, os, sys, pathlib
payload = json.load(sys.stdin)
pathlib.Path(os.environ["PROBE_LOG"]).open("a").write(
    json.dumps({"event": payload.get("hook_event_name"), "tool": payload.get("tool_name")}) + "\n")
print(json.dumps({"continue": False, "stopReason": "supervisor: stopping after the failed call"}))
