"""Per-boundary latency of the supervisor in the first e2e run.

Usage: python3 latency.py <the run record's r0 directory>

Measurement only, and read-only: nothing here writes, and no cause is offered
for anything it prints. Every number carries the denominator it was taken over,
because the same span divided two ways is two different figures.

A *delta* is the wall time between two adjacent rows of `supervisor.jsonl`,
attributed to the later row — whose `kind` is what that interval produced. It
is **not** a judge-call duration; see `LATENCY.md` §8.
"""

import collections
import datetime as dt
import json
import pathlib
import statistics
import sys

root = pathlib.Path(sys.argv[1]).expanduser()
r = root / "rollout/a0"


def read(name):
  return [json.loads(l) for l in (r / name).read_text().splitlines() if l.strip()]


rows = read("supervisor.jsonl")
at = [dt.datetime.fromisoformat(x["at"]) for x in rows]
span = (at[-1] - at[0]).total_seconds()
print(f"rows                      {len(rows)}")
print(f"first / last row          {at[0].time()} / {at[-1].time()}")
print(f"span s                    {span:.1f}   (measured; both ends are supervisor rows)")
print(f"span / rows               {span / len(rows):.2f}   s per boundary, denominator {len(rows)} rows")

deltas = [((at[i] - at[i - 1]).total_seconds(), rows[i]) for i in range(1, len(rows))]
xs = sorted(d for d, _ in deltas)
q = statistics.quantiles(xs, n=100, method="inclusive")
med = statistics.median(xs)
print(f"\ndeltas                    {len(deltas)}   (rows - 1)")
print(f"min / p25 / med           {xs[0]:.2f} / {q[24]:.2f} / {med:.2f}")
print(f"p75 / p90 / max           {q[74]:.2f} / {q[89]:.2f} / {xs[-1]:.2f}")
print(f"mean / stdev              {statistics.mean(xs):.2f} / {statistics.stdev(xs):.2f}   denominator {len(xs)} deltas")
print(f"max / med                 {xs[-1] / med:.2f}")
# The shape, printed rather than summarized: "no long tail" and "two modes" are
# claims about the whole histogram, and a handful of percentiles cannot carry
# either one.
histogram = collections.Counter(int(d * 2) / 2 for d, _ in deltas)
for lo in sorted(histogram):
  print(f"  [{lo:5.1f},{lo + 0.5:5.1f})  {'#' * histogram[lo]} {histogram[lo]}")
# The bands the histogram shows, counted. The middle one is the trough, and the
# cut used below (5 s) sits inside it rather than at a gap: the largest empty
# stretch anywhere in the distribution is printed next, so nobody has to take
# "bimodal" on trust.
for label, lo, hi in (("< 3.5 s", 0.0, 3.5), ("3.5-5.5 s", 3.5, 5.5), (">= 5.5 s", 5.5, 1e9)):
  print(f"band {label:<10}          n={sum(1 for d in xs if lo <= d < hi)}")
widest = max((xs[i + 1] - xs[i], xs[i], xs[i + 1]) for i in range(len(xs) - 1))
print(f"widest empty stretch      {widest[0]:.2f} s, between {widest[1]:.2f} and {widest[2]:.2f}")
# Strict local maxima of the histogram above, counted so that nobody has to
# call this sample a shape. This is a property of a 0.5 s binning and moves
# when the bin width moves, which is exactly why it is not a modality test:
# there is no modality claim in LATENCY.md, and none can be read off this line.
bins = sorted(histogram)
peaks = [b for i, b in enumerate(bins)
         if (i == 0 or histogram[b] > histogram[bins[i - 1]])
         and (i == len(bins) - 1 or histogram[b] > histogram[bins[i + 1]])]
print(f"strict local maxima       {len(peaks)} at " + ", ".join(f"{b:.1f}" for b in peaks)
      + "   (a property of the 0.5 s binning, not a modality test)")

lo = [d for d, _ in deltas if d < 5]
hi = [d for d, _ in deltas if d >= 5]
print(f"\n< 5 s                     n={len(lo)}  mean {statistics.mean(lo):.2f}  max {max(lo):.2f}")
print(f">= 5 s                    n={len(hi)}  mean {statistics.mean(hi):.2f}  min {min(hi):.2f}")
print(f"kinds among the < 5 s     {sorted({x['kind'] for d, x in deltas if d < 5})}")
silent = [d for d, x in deltas if x["kind"] == "silent"]
print(f"silent < 5 s / >= 5 s     {sum(1 for d in silent if d < 5)} / {sum(1 for d in silent if d >= 5)}")

print()
kinds = sorted({x["kind"] for _, x in deltas})
for kind in kinds:
  sel = [d for d, x in deltas if x["kind"] == kind]
  print(f"kind {kind:<8}         n={len(sel):<4} mean {statistics.mean(sel):.2f}  "
        f"med {statistics.median(sel):.2f}  min {min(sel):.2f}  max {max(sel):.2f}  "
        f"sum {sum(sel):.1f} s = {sum(sel) / span:.1%} of the span")
# Read off the data rather than listed, so a fourth kind cannot go unnoticed:
# the three shares above are exhaustive only if these two lines say so.
print(f"kinds seen                {kinds}")
print(f"they cover                {sum(1 for _, x in deltas if x['kind'] in kinds)} of {len(deltas)} deltas")
# The two lapse failures are counted apart for the reason WITNESS.md gives: a
# call that returned nothing and a call that returned something unusable are
# two failures, not one.
for name, nothing in (("lapse no output", True), ("lapse unparsable", False)):
  sel = [d for d, x in deltas
         if x["kind"] == "lapse" and ("not NoneType" in x["reason"]) == nothing]
  print(f"{name:<25} n={len(sel):<4} mean {statistics.mean(sel):.2f}  sum {sum(sel):.1f} s")
lapse = [d for d, x in deltas if x["kind"] == "lapse"]
print(f"lapse deltas > med        {sum(1 for d in lapse if d > med)} of {len(lapse)}")
print(f"lapse deltas >= p75       {sum(1 for d in lapse if d >= q[74])} of {len(lapse)}")
print("lapse deltas              " + ", ".join(
    f"{x['cursor']}:{d:.2f}" for d, x in deltas if x["kind"] == "lapse"))
print("spoke deltas              " + ", ".join(
    f"cursor {x['cursor']} {d:.2f} s" for d, x in deltas if x["kind"] == "spoke"))

# Trend against position. Both coefficients over the same 169 pairs.
ys = [d for d, _ in deltas]
cs = [x["cursor"] for _, x in deltas]


def pearson(a, b):
  ma, mb = statistics.mean(a), statistics.mean(b)
  num = sum((u - ma) * (v - mb) for u, v in zip(a, b))
  return num / (sum((u - ma) ** 2 for u in a) ** 0.5 * sum((v - mb) ** 2 for v in b) ** 0.5)


def rank(v):
  order = sorted(range(len(v)), key=lambda i: v[i])
  out = [0.0] * len(v)
  i = 0
  while i < len(order):
    j = i
    while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
      j += 1
    for k in range(i, j + 1):
      out[order[k]] = (i + j) / 2
    i = j + 1
  return out


print(f"\npearson r (cursor, delta) {pearson(cs, ys):+.3f}   over {len(ys)} deltas")
print(f"spearman rho              {pearson(rank(cs), rank(ys)):+.3f}   over {len(ys)} deltas")
block = -(-len(deltas) // 10)  # ten position blocks; the last one is the short one
for i in range(0, len(deltas), block):
  chunk = [d for d, _ in deltas[i:i + block]]
  print(f"  deltas {i + 1:>3}-{i + len(chunk):>3}         med {statistics.median(chunk):.2f}  "
        f"mean {statistics.mean(chunk):.2f}  n={len(chunk)}")
# The cut is the first cursor that lapsed, derived here rather than written in:
# REPORT.md 7b's "none before cursor 87" is what makes this the interesting
# place to split, and hardcoding 87 would let the two drift apart in silence.
cut = min(x["cursor"] for _, x in deltas if x["kind"] == "lapse")
print(f"first lapse cursor        {cut}")
for label, keep in (("all", lambda x: True), ("silent only", lambda x: x["kind"] == "silent")):
  before = [d for d, x in deltas if x["cursor"] < cut and keep(x)]
  after = [d for d, x in deltas if x["cursor"] >= cut and keep(x)]
  print(f"cursor <{cut} vs >={cut} ({label:<11}) "
        f"{statistics.mean(before):.2f} (n={len(before)}) vs {statistics.mean(after):.2f} (n={len(after)})"
        f"   difference {statistics.mean(after) - statistics.mean(before):+.2f}")

# The two sides of the actor's last timestamped event. The split is on
# timestamps: it selects a delta by whether its row falls after that instant,
# which says nothing about when the actor finished — the `result` on the last
# line carries no timestamp and may sit anywhere in the tail. Same deltas,
# partitioned, never merged, and neither side is free of waiting.
events = read("claude_code.event_stream.jsonl")
stamps = [e["timestamp"] for e in events if e.get("timestamp")]
last_actor = dt.datetime.fromisoformat(max(stamps).replace("Z", "+00:00"))
print(f"\nactor last stamped event  {max(stamps)}   ({len(stamps)} of {len(events)} events carry one)")
print(f"last stamped line         {max(i for i, e in enumerate(events, 1) if e.get('timestamp'))} of {len(events)}")
overlap = [(d, x) for d, x in deltas if dt.datetime.fromisoformat(x["at"]) <= last_actor]
tail = [(d, x) for d, x in deltas if dt.datetime.fromisoformat(x["at"]) > last_actor]
for label, sel in (("overlap (<=)", overlap), ("tail (>)", tail)):
  ds = [d for d, _ in sel]
  print(f"phase {label:<14}      n={len(ds):<4} mean {statistics.mean(ds):.2f}  "
        f"med {statistics.median(ds):.2f}  min {min(ds):.2f}  max {max(ds):.2f}  sum {sum(ds):.1f} s")
for label, sel in (("overlap silent", overlap), ("tail silent", tail)):
  ds = [d for d, x in sel if x["kind"] == "silent"]
  print(f"  {label:<22}n={len(ds):<4} mean {statistics.mean(ds):.2f}  med {statistics.median(ds):.2f}")
print(f"  spoke rows in overlap   {sum(1 for _, x in overlap if x['kind'] == 'spoke')} of "
      f"{sum(1 for _, x in deltas if x['kind'] == 'spoke')}")
# Two quantities that both get called "the tail", printed side by side because
# a sentence that does not say which one it means is the failure mode here.
# REPORT.md 7a's figure is the first; the sum of the deltas is the second, and
# it is longer by the part of the straddling delta that falls before the
# instant they are split at.
first_tail = min(i for i in range(1, len(rows)) if at[i] > last_actor)
print(f"last actor event -> last row   {(at[-1] - last_actor).total_seconds():.1f} s   (REPORT.md 7a's figure)")
print(f"sum of the tail deltas         {sum(d for d, _ in tail):.1f} s")
print(f"the straddling delta           cursor {rows[first_tail]['cursor']}, its pre-instant part "
      f"{(last_actor - at[first_tail - 1]).total_seconds():.1f} s")

# Staleness, in boundaries. Only some events carry a timestamp, so the count of
# events already emitted when a correction was written is a LOWER bound, and so
# is the gap between it and the cursor the correction was judged at.
emitted_at = sorted(dt.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in stamps)
print()
for x in rows:
  if x["kind"] != "spoke":
    continue
  t = dt.datetime.fromisoformat(x["at"])
  emitted = sum(1 for e in emitted_at if e <= t)
  print(f"spoke at cursor {x['cursor']:<3}       written {t.time()};  "
        f">= {emitted} events already emitted;  gap >= {emitted - x['cursor']} boundaries")

run = json.loads(next((root / "store/adhoc").glob("*/r0/rollout/a0/run.json")).read_text())
wall = run["metrics"]["claude_code.wall_seconds"]
print(f"\nrun_ts                    {run['run_ts']}")
print(f"rollout wall s            {wall:.2f}   (measured; claude_code.wall_seconds)")
print(f"wall - span               {wall - span:.1f} s outside every row-to-row interval")
print(f"boundaries metric         {run['metrics']['supervision.boundaries']}")
