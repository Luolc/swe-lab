"""Recompute every derived number the first run's report cites."""

import collections
import datetime as dt
import json
import pathlib
import re
import sys
import tarfile

a = pathlib.Path(sys.argv[1])  # the rollout attempt directory
r = a / "rollout/a0"


def rows(name):
  return [json.loads(l) for l in (r / name).read_text().splitlines() if l.strip()]


sup = rows("supervisor.jsonl")
kinds = collections.Counter(x["kind"] for x in sup)
print(f"boundaries               {len(sup)}")
for k in ("silent", "lapse", "spoke"):
  print(f"  {k:22} {kinds[k]}")

spoke = [x for x in sup if x["kind"] == "spoke"]
print("spoke cursors            " + ", ".join(str(x["cursor"]) for x in spoke))
print("spoke at                 " + ", ".join(x["at"][11:23] for x in spoke))
print("spoke policies           " + ", ".join(sorted({x["policy"] for x in spoke})))

lapse = [x for x in sup if x["kind"] == "lapse"]
print("lapse cursors            " + ", ".join(str(x["cursor"]) for x in lapse))
print(f"lapse cursor range       {min(x['cursor'] for x in lapse)}-{max(x['cursor'] for x in lapse)}")
classes = collections.Counter(
    tuple(re.findall(r"([A-Za-z]+Error)", x["reason"])[:2]) for x in lapse
)
for names, n in classes.most_common():
  print(f"lapse classes            {n} x {' <- '.join(names)}")

events = rows("claude_code.event_stream.jsonl")
stamped = [e["timestamp"] for e in events if e.get("timestamp")]
print(f"events                   {len(events)}")
print(f"result events            {sum(1 for e in events if e.get('type') == 'result')}")
print(f"events carrying a time   {len(stamped)}")
print(f"actor last stamped event {max(stamped)}")

first = dt.datetime.fromisoformat(sup[0]["at"])
last = dt.datetime.fromisoformat(sup[-1]["at"])
actor = dt.datetime.fromisoformat(max(stamped).replace("Z", "+00:00"))
span = (last - first).total_seconds()
tail = (last - actor).total_seconds()
print(f"supervisor first row     {sup[0]['at']}")
print(f"supervisor last row      {sup[-1]['at']}")
# The result event carries no timestamp, so the actor stopped at the last
# stamped event or later: the span is measured, the rest are bounds.
print(f"supervisor span s        {span:.1f}  (measured)")
print(f"overlap with actor s     {span - tail:.1f}  (at least)")
print(f"tail after actor s       {tail:.1f}  (at most)")
print(f"tail share of span       {tail / span:.3f}  (at most)")

TAG = "<supervisor_note>"
proxy = rows("claude_code.proxy_log.jsonl")
carrying = occurrences = final = in_response = 0
for rec in proxy:
  messages = ((rec.get("request") or {}).get("body") or {}).get("messages") or []
  hits = sum(json.dumps(m).count(TAG) for m in messages)
  if hits:
    carrying += 1
    occurrences += hits
    final += TAG in json.dumps(messages[-1])
  in_response += TAG in json.dumps(rec.get("response") or {})
print(f"proxy records            {len(proxy)}")
print(f"requests carrying it     {carrying}")
print(f"occurrences              {occurrences}")
print(f"in the final message     {final}")
print(f"in any response          {in_response}")

report = json.loads((r / "claude_code.native_transcript.json").read_text())
print("native transcript report " + json.dumps(report, sort_keys=True))
with tarfile.open(r / "claude_code.native_transcript.tar.gz") as tar:
  members = tar.getmembers()
  jsonl = [m for m in members if m.name.endswith(".jsonl")]
  lines = tar.extractfile(jsonl[0]).read().decode().splitlines()
print(f"archive members          {len(members)}")
print(f"transcript files         {len(jsonl)}")
print(f"transcript lines         {len(lines)}")
noted = [json.loads(l) for l in lines if TAG in l]
print(f"lines carrying it        {len(noted)}")
print("their types              " + ", ".join(sorted({x.get("type") for x in noted})))

run = json.loads(next((a / "store/adhoc").glob("*/r0/rollout/a0/run.json")).read_text())
unit = json.loads(next((a / "store/adhoc").glob("*/r0/unit_test/a2/run.json")).read_text())
keys = ("agent_complete", "claude_code.exit_code", "claude_code.timed_out",
        "claude_code.wall_seconds", "supervision.boundaries",
        "supervision.corrections", "supervision.lapses", "patch_is_empty")
for k in keys:
  print(f"metric {k:26} {run['metrics'].get(k)}")
print(f"metric supervision.unhealthy      {'supervision.unhealthy' in run['metrics']}")
for k in ("unit_test.required", "unit_test.passed", "unit_test.missing",
          "unit_test.resolved"):
  print(f"metric {k:26} {unit['metrics'].get(k)}")
print(f"patch.diff bytes         {(r / 'patch.diff').stat().st_size}")
