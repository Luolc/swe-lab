# Supervisor per-boundary latency — measurement only

[`REPORT.md`](REPORT.md) §7a states the supervisor's cost as one figure: 6.58 s
per boundary. **This file is that figure opened up into a distribution**, for
the same run and the same record. It is measurement and correlation only: no
cause is offered and no fix is proposed, and §8 lists what these numbers cannot
carry.

It does not restate the report's findings or [`WITNESS.md`](WITNESS.md)'s
provenance — those have one home each, and this file links to them.

## 0. Coordinates

| | |
| --- | --- |
| Corpus | the copy `WITNESS.md` pins by sha256, at `~/corpora/swe-lab/first-e2e-2026-09-02/r0/`; nothing under it was modified |
| Run | `run_ts = 20260902-072316`, one instance, one attempt (`a0`) |
| Inputs | `rollout/a0/supervisor.jsonl`, `rollout/a0/claude_code.event_stream.jsonl`, `store/adhoc/…/rollout/a0/run.json` |
| Read on | 2026-09-02, after the run ended and its container was gone — the inputs were complete, not still being written |

**One definition, because everything below rests on it.** A *delta* is the wall
time between two adjacent rows of `supervisor.jsonl`, attributed to the later
row — whose `kind` is what that interval produced. 170 rows give **169 deltas**.
A delta is **not** a judge-call duration; §8.2 says what else is inside it.

## The witness

Same rule as `WITNESS.md`: a number nothing can print does not go in. Every
number in this file that is a reading of the run is in the block below, and
`latency.py` is read-only, stdlib-only, and writes nothing. The exceptions are
`WITNESS.md`'s: things that are not claims about the run — the `§` and item
references, digests and commit ids, and §7's `poll_interval` with its
`file:line`, which §7 flags as read from the checkout rather than from the
corpus.

```sh
python3 experiments/trace_synthesis/pipeline_end_to_end/latency.py "$ATTEMPT"
```

Its output on the corpus above, verbatim:

```
rows                      170
first / last row          07:23:35.650499 / 07:42:14.572388
span s                    1118.9   (measured; both ends are supervisor rows)
span / rows               6.58   s per boundary, denominator 170 rows

deltas                    169   (rows - 1)
min / p25 / med           2.37 / 3.33 / 6.89
p75 / p90 / max           8.74 / 10.31 / 12.07
mean / stdev              6.62 / 2.83   denominator 169 deltas
max / med                 1.75
  [  2.0,  2.5)  ### 3
  [  2.5,  3.0)  ################## 18
  [  3.0,  3.5)  ########################### 27
  [  3.5,  4.0)  ### 3
  [  4.0,  4.5)  ### 3
  [  4.5,  5.0)  ## 2
  [  5.0,  5.5)  ## 2
  [  5.5,  6.0)  ######### 9
  [  6.0,  6.5)  ########## 10
  [  6.5,  7.0)  ############ 12
  [  7.0,  7.5)  ### 3
  [  7.5,  8.0)  ################ 16
  [  8.0,  8.5)  ######### 9
  [  8.5,  9.0)  ############## 14
  [  9.0,  9.5)  ##### 5
  [  9.5, 10.0)  ########### 11
  [ 10.0, 10.5)  ######### 9
  [ 10.5, 11.0)  ##### 5
  [ 11.0, 11.5)  ### 3
  [ 11.5, 12.0)  #### 4
  [ 12.0, 12.5)  # 1
band < 3.5 s             n=48
band 3.5-5.5 s           n=10
band >= 5.5 s            n=111
widest empty stretch      0.47 s, between 4.52 and 4.99
strict local maxima       6 at 3.0, 6.5, 7.5, 8.5, 9.5, 11.5   (a property of the 0.5 s binning, not a modality test)

< 5 s                     n=56  mean 3.17  max 4.99
>= 5 s                    n=113  mean 8.33  min 5.22
kinds among the < 5 s     ['silent']
silent < 5 s / >= 5 s     56 / 94

kind lapse            n=16   mean 10.65  med 10.44  min 8.67  max 12.07  sum 170.3 s = 15.2% of the span
kind silent           n=150  mean 6.21  med 6.60  min 2.37  max 11.73  sum 931.7 s = 83.3% of the span
kind spoke            n=3    mean 5.62  med 5.52  min 5.51  max 5.85  sum 16.9 s = 1.5% of the span
kinds seen                ['lapse', 'silent', 'spoke']
they cover                169 of 169 deltas
lapse no output           n=11   mean 10.87  sum 119.6 s
lapse unparsable          n=5    mean 10.15  sum 50.8 s
lapse deltas > med        16 of 16
lapse deltas >= p75       15 of 16
lapse deltas              87:10.00, 96:11.35, 98:11.61, 100:9.52, 101:10.12, 106:10.34, 108:10.20, 109:10.73, 110:10.53, 128:11.95, 131:8.67, 134:11.16, 136:11.45, 140:10.34, 158:12.07, 165:10.30
spoke deltas              cursor 4 5.51 s, cursor 8 5.85 s, cursor 12 5.52 s

pearson r (cursor, delta) +0.118   over 169 deltas
spearman rho              +0.150   over 169 deltas
  deltas   1- 17         med 3.20  mean 4.29  n=17
  deltas  18- 34         med 6.71  mean 6.27  n=17
  deltas  35- 51         med 7.91  mean 6.88  n=17
  deltas  52- 68         med 7.22  mean 6.67  n=17
  deltas  69- 85         med 6.04  mean 6.17  n=17
  deltas  86-102         med 10.01  mean 9.77  n=17
  deltas 103-119         med 6.49  mean 6.74  n=17
  deltas 120-136         med 7.75  mean 7.39  n=17
  deltas 137-153         med 3.65  mean 5.72  n=17
  deltas 154-169         med 5.68  mean 6.29  n=16
first lapse cursor        87
cursor <87 vs >=87 (all        ) 6.06 (n=85) vs 7.19 (n=84)   difference +1.14
cursor <87 vs >=87 (silent only) 6.07 (n=82) vs 6.38 (n=68)   difference +0.31

actor last stamped event  2026-09-02T07:26:19.485Z   (90 of 170 events carry one)
last stamped line         169 of 170
phase overlap (<=)        n=32   mean 5.07  med 5.11  min 2.37  max 9.55  sum 162.3 s
phase tail (>)            n=137  mean 6.98  med 7.64  min 2.47  max 12.07  sum 956.6 s
  overlap silent        n=29   mean 5.02  med 4.02
  tail silent           n=121  mean 6.50  med 6.89
  spoke rows in overlap   3 of 3
last actor event -> last row   955.1 s   (REPORT.md 7a's figure)
sum of the tail deltas         956.6 s
the straddling delta           cursor 34, its pre-instant part 1.5 s

spoke at cursor 4         written 07:23:47.541647;  >= 20 events already emitted;  gap >= 16 boundaries
spoke at cursor 8         written 07:24:04.404111;  >= 31 events already emitted;  gap >= 23 boundaries
spoke at cursor 12        written 07:24:23.080492;  >= 34 events already emitted;  gap >= 22 boundaries

run_ts                    20260902-072316
rollout wall s            1124.47   (measured; claude_code.wall_seconds)
wall - span               5.5 s outside every row-to-row interval
boundaries metric         170.0
```

## 1. Two figures, 6.58 and 6.62, and one clock

    span                   1118.9 s        (measured)
    span / 170 rows        6.58 s          (the report's §7a figure)
    mean of 169 deltas     6.62 s          (per delta, not per row)

One clock, two denominators. Neither is wrong; a sentence that does not say
which one it means is. Everything after this point uses the 169 deltas, because
the shape of the distribution is the thing being measured and rows have no
shape.

## 2. The distribution: no long tail

    min 2.37 | p25 3.33 | med 6.89 | p75 8.74 | p90 10.31 | max 12.07
    mean 6.62 | stdev 2.83                    denominator 169 deltas

`max / med = 1.75`. There is no small population of slow intervals dragging an
otherwise fast mean — **the fastest interval in the whole run is 2.37 s**, and
the slowest is under twice the median.

The deltas are not spread evenly across that range. Counted in three bands,
the middle one is sparse:

    < 3.5 s     n=48
    3.5-5.5 s   n=10
    >= 5.5 s    n=111

Those are three counts over bands chosen by hand, and nothing more: this file
makes **no claim about how many modes the sample has**, and the script defines
no test for one. Its histogram has **6 strict local maxima** — at 3.0, 6.5,
7.5, 8.5, 9.5 and 11.5 — and that count is a property of the 0.5 s bin width
rather than of the sample, which is why it settles nothing about shape and is
printed instead as the reason no shape word appears here. The histogram is the
observation; read the bins.

**The sparse band is thin, not empty.** The widest gap anywhere in the sorted
deltas is 0.47 s, between 4.52 and 4.99 — so the 5 s cut used below is a cut
chosen inside the sparse band, not a boundary the data hands you. Read that
way:

    < 5 s   n=56   mean 3.17   max 4.99
    >= 5 s  n=113  mean 8.33   min 5.22

All 56 sub-5 s deltas are `silent` rows. That is a statement about the fast
ones and not about `silent`: 94 of the 150 `silent` deltas are above 5 s.

## 3. Split at the actor's last timestamped event

`claude_code.event_stream.jsonl`'s last stamped event is
`2026-09-02T07:26:19.485Z`, on line 169 of 170. Partitioning the deltas at that
instant is a split on **timestamps**, and that is all it is: it selects each
delta by whether the supervisor row it is attributed to falls after that
instant, which is not a fact about when the actor finished. One event — the
unstamped `result` on line 170 — had still to arrive, and **this record does
not time it**, so it may sit anywhere in the tail. **Actor-wait may therefore
be present on either side of the split, and this record gives no bound on how
much.** The two sides are reported apart rather than averaged together, and
neither is a phase in which waiting is known to be absent.

    overlap phase  n=32   mean 5.07  med 5.11  min 2.37  max 9.55   sum 162.3 s
    tail phase     n=137  mean 6.98  med 7.64  min 2.47  max 12.07  sum 956.6 s
      overlap, silent only   n=29   mean 5.02  med 4.02
      tail, silent only      n=121  mean 6.50  med 6.89

**The tail's 956.6 s is not the report's 955.1 s**, and the two are printed side
by side above so the difference is visible rather than reconciled by hand. The
report's figure is *last stamped actor event → last supervisor row*; this one is
*the sum of the deltas whose row falls after that instant*. The delta at cursor
34 straddles the instant, and its 1.5 s pre-instant part is the whole
difference. Two quantities, two definitions.

## 4. By row kind

    silent  n=150  mean 6.21  med 6.60  min 2.37  max 11.73  sum 931.7 s = 83.3% of the span
    lapse   n=16   mean 10.65 med 10.44 min 8.67  max 12.07  sum 170.3 s = 15.2% of the span
    spoke   n=3    mean 5.62  med 5.52  min 5.51  max 5.85   sum  16.9 s =  1.5% of the span

      lapse, no output      n=11  mean 10.87  sum 119.6 s
      lapse, unparsable     n=5   mean 10.15  sum 50.8 s

Those three kinds cover 169 of 169 deltas; the script prints both the kinds it
found and that count, rather than assuming the three it was told to look for are
all there are. The lapse split into *returned nothing* and *returned something
unusable* is `WITNESS.md`'s — the same two failures, here with their intervals
instead of their cursors.

`lapse` is the slowest kind in this run, and not marginally: **all 16 are above
the overall median (6.89) and 15 of the 16 are at or above p75 (8.74)** — both
counts are printed, so either one fails visibly if the corpus changes. The one
exception is 8.67. Individually:

    87:10.00  96:11.35  98:11.61  100:9.52  101:10.12  106:10.34  108:10.20
    109:10.73 110:10.53 128:11.95 131:8.67  134:11.16  136:11.45  140:10.34
    158:12.07 165:10.30

**Two orderings of the same two kinds, and neither is a conclusion.** Over the
whole run `spoke` (5.62) is the smaller mean and `silent` (6.21) the larger.
All three `spoke` rows fall in the overlap phase, where the `silent` mean is
**5.02** — the smaller of the two on that side, reversing the order. Both are
descriptions of these samples; **n=3 supports no direction either way** (§8.4),
so nothing is concluded from either ordering. It is recorded because the
run-wide ordering reads like evidence about `spoke` and is not.

## 5. Staleness, measured in boundaries rather than seconds

For each delivered correction: when its row was written, how many actor events
had already been emitted, and how far ahead of the judged cursor that is.

    spoke at cursor 4    written 07:23:47.541647   >= 20 events emitted   gap >= 16 boundaries
    spoke at cursor 8    written 07:24:04.404111   >= 31 events emitted   gap >= 23 boundaries
    spoke at cursor 12   written 07:24:23.080492   >= 34 events emitted   gap >= 22 boundaries

**All three are lower bounds.** Only 90 of the 170 events carry a timestamp, so
an event that was already emitted but carries none cannot be counted — the true
count can only be higher, and so can the gap. This is the distance at the moment
the judgement was *recorded*; it is not when the actor read it, which nothing in
this corpus times.

## 6. Position trend: weak, non-monotone, and mostly the lapse rows

    pearson r  (cursor, delta)  +0.118    over 169 deltas
    spearman rho                +0.150    over 169 deltas

Medians of ten position blocks, in order — not monotone:

    3.20  6.71  7.91  7.22  6.04  10.01  6.49  7.75  3.65  5.68

Split at cursor 87, which the script derives as the first cursor that lapsed
rather than hardcoding (it is `REPORT.md` §7b's boundary, and deriving it keeps
the two from drifting apart):

    cursor <87 vs >=87, all deltas    6.06 (n=85) vs 7.19 (n=84)
    cursor <87 vs >=87, silent only   6.07 (n=82) vs 6.38 (n=68)

The regional rise shrinks from +1.14 s to +0.31 s once the `lapse` rows are
dropped: most of the difference is those rows, which all fall at cursor >= 87.

## 7. Cross-checks

- **The sum of the deltas equals the span by construction.** It cannot fail, so
  it is not a check and is not reported as one.
- `metrics["supervision.boundaries"] = 170` matches the 170 rows.
- `metrics["claude_code.wall_seconds"] = 1124.47` minus the 1118.9 s span leaves
  **5.5 s** that falls outside every row-to-row interval — before the first row
  and after the last. That is the residual, stated rather than absorbed.
- **The poll loop's granularity is not what this distribution is measuring.**
  `poll_interval` defaults to `0.5` s
  (`src/swe_lab/trace_synthesis/channel.py:322`), well under the 2.37 s minimum
  delta. **This number is read from the checkout, not from the corpus** — the
  same kind of mixed derivation `REPORT.md` §7a flags for the agent timeout, and
  it needs the same coordinate: that file's last change is `3e97442`, which
  `WITNESS.md` records as the repository state at run time, so the value that
  ran is the value in the file today. Nothing in the corpus records it, and a
  later edit to that line would make this sentence quietly false.

## 8. What this data does not support

Listed so the boundary is visible when these numbers are used to argue for
something.

1. **n = 1.** One instance, one run, one actor/supervisor model pair. No second
   instance, no repeat of the same instance, no other supervisor model or
   effort. Nothing here separates "this configuration" from "this instance".
2. **A delta is not a judge-call duration.** It is the interval between two
   written rows, and it contains the judge call *plus* poll-loop overhead,
   serialization, and waiting for the actor's next event. This corpus has no
   per-call timing, so **it cannot say the model call is the slow part** — that
   decomposition does not exist here. §3's split is on timestamps, not on
   actor-wait: unmeasured actor-wait may be present on either side of it, so
   neither side is a clean read of the judge call.
3. **`lapse` being slower is a correlation.** Both directions of explanation fit
   these 16 rows and one run cannot separate them. No cause is offered.
4. **`spoke`'s n=3 supports nothing** in either direction (§4), and §5's three
   staleness figures are those same three rows, as lower bounds.
5. **The trend is weak and non-monotone** (pearson r = +0.118, and the ten
   position-block medians above rise and fall rather than climb). It does not
   support "the supervisor slows down as the run goes on".
6. **15.2% of the span in lapses is this run's share, not a rate.** It is not an
   estimate of how much of the next run's span goes the same way.
7. **`REPORT.md` §7a's extrapolation to the agent timeout assumes another
   instance's boundary intervals are drawn from this same distribution.** This
   data cannot support that assumption — it is one draw, and §2's band counts
   show that draw is not evenly spread within itself.
