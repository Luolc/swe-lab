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

# What the corrections said about the actor, against what the actor had done.
# Point 3's claim carries a "because of a real deviation" clause that §4's
# closure criterion does not test, so the readings that bear on it are printed
# here rather than left to be inferred from the counts above.
#
# `EvidenceFilter.admit` is re-implemented rather than imported: this witness
# runs on the corpus with the standard library alone, and one that imports the
# code under examination cannot contradict it.
events = rows("claude_code.event_stream.jsonl")


def admitted(event):
  """Return the disposition `EvidenceFilter` gives this event, or None."""
  if event.get("type") == "assistant":
    return "assistant"
  content = (event.get("message") or {}).get("content") or []
  hit = any(b.get("type") == "tool_result" for b in content if isinstance(b, dict))
  return "tool-result" if hit else None


def blocks(event, kind):
  """Yield an event's content blocks of one type."""
  for b in ((event.get("message") or {}).get("content") or []):
    if isinstance(b, dict) and b.get("type") == kind:
      yield b


def result_text(event):
  """Return the concatenated text of an event's tool results."""
  out = []
  for b in blocks(event, "tool_result"):
    inner = b.get("content")
    out.append(inner if isinstance(inner, str) else json.dumps(inner))
  return "".join(out)


def first(predicate):
  """Return the 1-indexed position and timestamp of the first match."""
  for i, event in enumerate(events, 1):
    if predicate(event):
      return i, event.get("timestamp")
  return None, None


# Cross-check before use: the same events, dispositioned by this function and by
# the run's own supervisor, must agree row for row. A disagreement would mean
# the readings below describe a filter the run did not use.
disagreements = sum(
    (admitted(e) or "excluded-nothing-to-keep") != s["evidence"]
    for e, s in zip(events, sup, strict=True)
)
print(f"filter disagreements     {disagreements}  (this witness vs supervisor.jsonl)")
# Printed *and* enforced. A cross-check that only prints is not a check: every
# reading below would still be computed, still look ordinary, and still exit 0
# against a filter the run never used.
if disagreements:
  raise SystemExit(
      f"filter disagreements {disagreements}: this script's copy of"
      " EvidenceFilter does not match the dispositions in supervisor.jsonl, so"
      " nothing below describes the filter that ran"
  )

# How much evidence the judge held at each boundary that spoke. The supervisor
# appends an admitted record before building the observation, so the count at
# cursor n is taken over events 1..n.
held = [sum(1 for e in events[: x["cursor"]] if admitted(e)) for x in spoke]
print("evidence held when spoke " + ", ".join(
    f"cursor {x['cursor']}: {n}" for x, n in zip(spoke, held, strict=True)))

# Naming the function and reading it are different actions, and the three
# corrections distinguish them: the first says the file was never opened, the
# third says the *body* never landed. So the line number is read off the actor's
# own grep rather than written here, and "read the body" is a property of the
# request — a Read whose window covers that line — not a substring hunt over
# output that a one-line grep hit would also satisfy.
TARGET = "openlibrary/core/models.py"
grep_at, grep_when = first(lambda e: "def from_isbn" in result_text(e))
defined = int(re.search(r"(\d+):\s+def from_isbn", result_text(events[grep_at - 1])).group(1))


def covers(event, path, line):
  """Whether the event is a Read of `path` whose window contains `line`."""
  for b in blocks(event, "tool_use"):
    got = b.get("input") or {}
    if b.get("name") != "Read" or path not in str(got.get("file_path", "")):
      continue
    start = got.get("offset", 1)
    if start <= line < start + got.get("limit", 1 << 30):
      return True
  return False


def window(event, path):
  """Return the (offset, limit) of the event's Read of `path`."""
  for b in blocks(event, "tool_use"):
    got = b.get("input") or {}
    if b.get("name") == "Read" and path in str(got.get("file_path", "")):
      return got.get("offset", 1), got.get("limit")
  return None, None


body_at, body_when = first(lambda e: covers(e, TARGET, defined))
isbn_at, isbn_when = first(lambda e: covers(e, "openlibrary/utils/isbn.py", 1))
offset, limit = window(events[body_at - 1], TARGET)
print(f"from_isbn defined at     {TARGET}:{defined}  (read off the grep result)")
print(f"grep naming it           event {grep_at} at {grep_when}  (a hit on the signature line)")
print(f"first Read covering it   event {body_at} at {body_when}  offset {offset}, limit {limit}")
print(f"first Read of isbn.py    event {isbn_at} at {isbn_when}")

# Whether that Read was inside the evidence each judgement actually held. This
# separates "the judge was wrong" from "the judge was right about a prefix the
# actor had left behind", which the delta below cannot do on its own.
print("covering Read in window  " + ", ".join(
    f"cursor {x['cursor']}: {'yes' if body_at <= x['cursor'] else 'no'}" for x in spoke)
    + f"  (it is event {body_at})")

# Two instants, not one. `supervisor.jsonl` records when a correction was
# *written*; the actor's own transcript records when it *arrived*. The question
# these readings serve is what the actor had already done when the note reached
# it, so the receipt is the load-bearing one and the write is what the
# supervisor's own account can see. Both are printed because a single column
# labelled "delivered" carrying the write time is how they get confused.
received = sorted(x["timestamp"] for x in noted)
lags = [
    (dt.datetime.fromisoformat(r.replace("Z", "+00:00"))
     - dt.datetime.fromisoformat(s["at"])).total_seconds()
    for s, r in zip(spoke, received, strict=True)
]
# Pairing the two lists by order is only sound if each receipt follows its own
# write and nothing else's, so that is checked rather than assumed.
if not all(0 < lag < 1 for lag in lags):
  raise SystemExit(f"note receipts do not follow their writes within 1 s: {lags}")
print("note received at         " + ", ".join(x[11:23] for x in received))
print("written -> received ms   " + ", ".join(f"{lag * 1000:.0f}" for lag in lags))


def since(when, stamps):
  """Return seconds from `when` to each of `stamps`, formatted."""
  base = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
  return ", ".join(
      f"{(dt.datetime.fromisoformat(s.replace('Z', '+00:00')) - base).total_seconds():.1f}"
      for s in stamps
  )


print("written minus that Read s " + since(body_when, [x["at"] for x in spoke]))
print("receipt minus that Read s " + since(body_when, received))
print("isbn.py Read to note 2 s " + since(isbn_when, [spoke[1]["at"], received[1]])
      + "  (written, received)")

# The actor's own answer to each note. Transcript rows are stored out of
# chronological order, so "next" is by timestamp and not by line, and what is
# printed is the next assistant text rather than a verdict about it.
stamped = sorted(
    ((json.loads(l).get("timestamp") or "", i, json.loads(l))
     for i, l in enumerate(lines, 1)),
    key=lambda t: t[0],
)


def says(row):
  """Return an assistant row's text blocks, joined; empty for anything else."""
  if row.get("type") != "assistant":
    return ""
  return "".join(b.get("text", "") for b in blocks(row, "text"))


for line_no, note in zip(where, noted, strict=True):
  when, at_line, row = next(
      t for t in stamped if t[0] > note["timestamp"] and says(t[2]))
  print(f"note line {line_no:<3} answered by  line {at_line} at {when}")
  # In full: the report quotes these, and a witness truncated shorter than the
  # quotation cannot support it.
  print(f"  {says(row)}")

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
