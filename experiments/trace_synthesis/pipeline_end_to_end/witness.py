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


# The pinned actor, read from the container's own --version rather than from a
# constant in the checkout: the report's claim is about what ran.
info = (r / "claude.info").read_text()
version = info.split("--version\n[exit 0]\n", 1)[1].splitlines()[0]

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
# One exception class, two failures. A provider response whose `content` was
# null reaches `json.loads` as None; that is a call that returned nothing, not
# output that arrived and would not parse. Counted apart, with cursors, because
# the claim they support is about a distribution and a count is not its witness.
buckets = collections.defaultdict(list)
for x in lapse:
  nothing = "not NoneType" in x["reason"]
  buckets["no output" if nothing else "unparsable output"].append(x["cursor"])
for name in ("no output", "unparsable output"):
  cursors = buckets[name]
  label = f"lapse {name}"
  print(f"{label:<25}{len(cursors)}  cursors " + ", ".join(map(str, cursors)))
# The unparsable ones split again by what the decoder complained about, for the
# same reason: "three were truncated" is a claim about members, not a count.
detail = collections.defaultdict(list)
for x in lapse:
  if "not NoneType" in x["reason"]:
    continue
  # Matched on substrings, not on the whole message: the decoder's text is
  # nested inside two exception reprs, so the quotes in it are escaped.
  for name in ("Unterminated string", "Expecting value", "delimiter"):
    if name in x["reason"]:
      detail[name].append(x["cursor"])
      break
  else:
    detail["other"].append(x["cursor"])
for name in sorted(detail):
  label = f"  {name}"
  print(f"{label:<25}{len(detail[name])}  cursors " + ", ".join(map(str, detail[name])))

events = rows("claude_code.event_stream.jsonl")
stamped = [e["timestamp"] for e in events if e.get("timestamp")]
result = next(e for e in events if e.get("type") == "result")
usage = result.get("usage") or {}
print(f"actor total_cost_usd     {result.get('total_cost_usd')}")
print(f"actor num_turns          {result.get('num_turns')}")
print(f"actor duration_ms        {result.get('duration_ms')}")
keys = ("input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens")
print("actor usage              " + json.dumps({k: usage.get(k) for k in keys}))
print(f"events                   {len(events)}")
print(f"result events            {sum(1 for e in events if e.get('type') == 'result')}")
print(f"events carrying a time   {len(stamped)}")
print(f"actor last stamped event {max(stamped)}")
print(f"last stamped line        {max(i for i, e in enumerate(events, 1) if e.get('timestamp'))}")

run = json.loads(next((a / "store/adhoc").glob("*/r0/rollout/a0/run.json")).read_text())
wall = run["metrics"]["claude_code.wall_seconds"]
print(f"run_ts                   {run['run_ts']}")
print(f"backend                  {run['backend']}")
print(f"instance_id              {run['instance_id']}")
first = dt.datetime.fromisoformat(sup[0]["at"])
last = dt.datetime.fromisoformat(sup[-1]["at"])
actor = dt.datetime.fromisoformat(max(stamped).replace("Z", "+00:00"))
span = (last - first).total_seconds()
tail = (last - actor).total_seconds()
print(f"supervisor first row     {sup[0]['at']}")
print(f"supervisor last row      {sup[-1]['at']}")
# The result event carries no timestamp, so the actor stopped at the last
# stamped event or later: the span is measured, the rest are bounds.
print(f"supervisor span s        {span:.1f}  (measured; both ends are supervisor rows)")
print(f"s per boundary           {span / len(sup):.2f}  (measured; {span:.1f} s / {len(sup)} rows)")
print(f"lapses / boundaries      {len(lapse) / len(sup):.1%}  (denominator {len(sup)} rows)")
print(f"rollout wall s           {wall:.2f}  (measured; claude_code.wall_seconds)")
print(f"overlap with actor s     {span - tail:.1f}  (at least)")
print(f"tail after actor s       {tail:.1f}  (at most)")
# Two true ratios of the same numerator. Named, because a percentage without
# its denominator is not a reading: one is the run's cost, the other is how
# much of its own span the supervisor spent working alone.
print(f"tail / rollout wall      {tail / wall:.1%}  (at most; denominator {wall:.2f} s)")
print(f"tail / supervisor span   {tail / span:.1%}  (at most; denominator {span:.1f} s)")
# A second bound on the same tail, from the actor's own reported duration rather
# than from its last stamped event. Two derivations, so the gap between them is
# visible instead of assumed away.
other = wall - result["duration_ms"] / 1000
print(f"wall - actor duration s  {other:.1f}  (at most)")
print(f"the two bounds differ by {other - tail:.1f}  s")

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
print(f"carried in history       {occurrences - final}")

report = json.loads((r / "claude_code.native_transcript.json").read_text())
print("native transcript report " + json.dumps(report, sort_keys=True))
with tarfile.open(r / "claude_code.native_transcript.tar.gz") as tar:
  members = tar.getmembers()
  jsonl = [m for m in members if m.name.endswith(".jsonl")]
  lines = tar.extractfile(jsonl[0]).read().decode().splitlines()
print(f"archive members          {len(members)}")
print(f"transcript files         {len(jsonl)}")
print(f"transcript member        {jsonl[0].name}")
print(f"transcript lines         {len(lines)}")
noted = [json.loads(l) for l in lines if TAG in l]
where = [i for i, l in enumerate(lines, 1) if TAG in l]
print(f"lines carrying it        {len(noted)}  lines " + ", ".join(map(str, where)))
print("their types              " + ", ".join(sorted({x.get("type") for x in noted})))

conversation = json.loads((r / "conversation.json").read_text())["messages"]
carried = [i for i, m in enumerate(conversation) if "supervisor_note" in json.dumps(m)]
print(f"conversation messages    {len(conversation)}")
print(f"carrying it              {len(carried)}  " + ", ".join(f"msg[{i}]" for i in carried))
print("their roles              " + ", ".join(sorted({conversation[i]["role"] for i in carried})))

# The integrity side, both readings. The verifier's own list and the purge's
# before/after are two different questions asked of the same repository.
verifier = json.loads((r / "verifier.json").read_text())
print("verifier flagged         " + json.dumps(verifier["flagged"]))
print("verifier high_confidence " + json.dumps(verifier["high_confidence"]))
print(f"verifier suspicious_git  {len(verifier['suspicious_git'])} commands")
for command in verifier["suspicious_git"]:
  print(f"  {command}")
integrity = json.loads((r / "git_integrity.json").read_text())
before, after = integrity["before"], integrity["after"]
print(f"integrity base_sha       {before['base_sha']}")
print(f"integrity purged         {integrity['purged']}")
print(f"integrity future_commits {before['future_commits']} -> {after['future_commits']}")
print(f"integrity solution_reach {before['solution_reachable']} -> {after['solution_reachable']}")
print("integrity violations     " + json.dumps(integrity["violations"]))

unit = json.loads(next((a / "store/adhoc").glob("*/r0/unit_test/a2/run.json")).read_text())
keys = ("agent_complete", "claude_code.exit_code", "claude_code.timed_out",
        "claude_code.wall_seconds", "supervision.boundaries",
        "supervision.corrections", "supervision.lapses", "patch_is_empty",
        "verifier.flagged")
for k in keys:
  print(f"metric {k:26} {run['metrics'].get(k)}")
print(f"metric supervision.unhealthy      {'supervision.unhealthy' in run['metrics']}")
for k in ("unit_test.required", "unit_test.passed", "unit_test.missing",
          "unit_test.resolved"):
  print(f"metric {k:26} {unit['metrics'].get(k)}")
print(f"actor version            {version}")
print(f"actor model              {run['extra']['agent_model']}")
print(f"patch base_ref           {run['extra']['patch_base_ref']}")
print(f"rollout outcome          {run['extra']['rollout_outcome']}")
for name in ("patch.diff", "supervisor.jsonl", "claude_code.proxy_log.jsonl"):
  print(f"{name + ' bytes':<24} {(r / name).stat().st_size}")
print(f"corpus files             {sum(1 for f in a.rglob('*') if f.is_file())}")
