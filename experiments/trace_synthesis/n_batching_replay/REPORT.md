# `N`-batching replay — what the supervisor says at each batch size

| | |
| --- | --- |
| Author | `swelab-nsweep-impl` (Claude, Opus 5) |
| Ran | 2026-09-03, 03:11–04:25 UTC (registered arms first, then the post-hoc control) |
| Corpus | `~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0`, 170 events, sha256 `28abd03e…f55403`, read-only |
| Instance | `instance_internetarchive__openlibrary-5de7de19…debb` (1 instance, 1 attempt, 0 rollouts run) |
| Actor | recorded — `claude-sonnet-5`, from the preserved stream. Nothing re-ran it. |
| Supervisor | requested `anthropic/claude-sonnet-5` (`workflow.definitions.SUPERVISOR_MODEL`), via `judge.openrouter_transport`. All **896** judge responses reported that same model, and all 902 calls the provider `Claude Platform on AWS` — so the alias was not re-pointed under the run. |
| Held fixed | `budget=3`, `cooldown=4`, `window=8`, criterion digest `ffb2dadf…2900a1`, task sha256 `3f42ec71…8af83a` |
| Varied | `N` only, except the one arm that moves `window` and the one post-hoc arm that moves `budget` |
| Pre-registration | [`PREREGISTRATION.md`](PREREGISTRATION.md), committed at `39be140` **before the first model call** |
| Code at | `39be140` plus this experiment's own files; the manifests record `39be140-dirty` because `runs/` was being written while it ran |
| Sampling | only `max_tokens` sent (512 judge / 256 writer); `temperature`, `top_p`, `top_k`, `seed` and `stop` all recorded as **not sent** |
| Produced by | §1 from `replay.py shape`; §§2–8 from `analyze.py`; the corpus/criterion/task digests from `runs/*/*/manifest.json`. No single command regenerates all three classes. |

**This report selects no `N` and contains no verdict.** It was pre-registered
not to.

## Read this first: what every arm ran on

Three conditions hold for **every** arm below. Each is a defect of the system
under test, not of this experiment, and each has its own home; nothing about
`N` can be read without them.

1. **The judge sees no tool results at all.** `judge._render` emits only
   `TextBlock`s, so of the 90 records the filter admits over this run, **11**
   render non-empty text (§5). Every arm was judged on that. →
   [#380](https://github.com/Luolc/swe-lab/issues/380)
2. **About one judgement in ten was swallowed by the judge's token ceiling.**
   All 85 lapses are `max_tokens = 512` reached while the model was still
   reasoning (§7); the recorded run lost 16 of its 170 boundaries the same way.
   → [#383](https://github.com/Luolc/swe-lab/issues/383)
3. **The judge's verdict is dominated by whether it has already spoken**, not by
   the actor (§4, A3) — this one rests on the arm added *after* the registered
   passes (`PREREGISTRATION.md`, Amendment 1). →
   [#381](https://github.com/Luolc/swe-lab/issues/381)

**And the one control that was supposed to separate `window` from `N` did not
run.** `n10` versus `n10_w15` was the arm pair meant to tell truncation apart
from batching. Both returned zero `off_track`, so moving `window` from 8 to 15
had nothing to move. **That is a failed comparison, not a negative result**: it
is not evidence that `window` does not matter.

### The conclusion, in the form the evidence supports

This experiment **measured no effect of `N`** — and *why* it could not is the
finding. On this trajectory the verdict is driven by the supervisor's own
memory, and any effect of `N` sits underneath that. So the sentence this
supports is **not** "`N` does not matter". It is:

> **`N` is not measurable until the self-confirmation effect is dealt with.**

## What this cannot answer, whatever the numbers say

Every reading below is inside these walls.

- **The actor's trajectory is a recording and does not move.** A correction
  this replay writes is never delivered. So this can say *what a supervisor at
  each `N` would say on this trajectory*; it cannot say what an actor would
  then do, or whether a different `N` produces a better patch. Only a live run
  answers that.
- **n = 1 trajectory, 1 instance, 1 actor model, 1 supervisor model.** Every
  sentence below is about this one recording.
- **No `N` is selected here.** The use of this report is to remove `N`s that
  are visibly broken and to make `N`'s effect on judgement legible — not to
  pick one.
- **Temperature was not sent** (only `max_tokens`), so the provider's default
  applies and two runs of one arm are not expected to agree. That is why every
  arm ran twice.

## Results

### 1. The shape of the sweep, before any model call

`replay.py shape`, deterministic, no credentials. Its judgment counts,
first-judgment events, maximum batch sizes and overflow fractions match every
value in [issue #375](https://github.com/Luolc/swe-lab/issues/375)'s table and
in the correction to it, computed independently here:

```
   N  judgments  first@event  max batch  mean batch  overflow w=8
   1         59            5          2         1.5           0%
   2         29            6          4         3.0           0%
   3         19           10          6         4.6           0%
   4         14           11          7         6.1           0%
   5         11           13          8         7.5           0%
   6          9           15         10         9.0          89%
   7          8           19         11        10.6         100%
   8          7           20         13        12.1         100%
  10          5           25         15        15.0         100%
  20          2           49         30        30.0         100%
```

The `N`/`window` boundary on this corpus is between **5 and 6**.

### 2. Every arm, both passes

| arm | pass | boundaries | answered | lapse | `off_track` | would-have-spoken | corrections |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `replicate` | a | 170 | 141 | 29 | 105 | 91 | 3 |
| `replicate` | b | 170 | 139 | 31 | 114 | 104 | 3 |
| `n1` | a | 59 | 55 | 4 | 0 | 0 | 0 |
| `n1` | b | 59 | 56 | 3 | 0 | 0 | 0 |
| `n3` | a | 19 | 16 | 3 | 0 | 0 | 0 |
| `n3` | b | 19 | 18 | 1 | 0 | 0 | 0 |
| `n5` | a | 11 | 9 | 2 | 0 | 0 | 0 |
| `n5` | b | 11 | 10 | 1 | 0 | 0 | 0 |
| `n6` | a | 9 | 8 | 1 | 0 | 0 | 0 |
| `n6` | b | 9 | 9 | 0 | 0 | 0 | 0 |
| `n10` | a | 5 | 5 | 0 | 0 | 0 | 0 |
| `n10` | b | 5 | 5 | 0 | 0 | 0 | 0 |
| `n10_w15` | a | 5 | 5 | 0 | 0 | 0 | 0 |
| `n10_w15` | b | 5 | 5 | 0 | 0 | 0 | 0 |
| `replicate_budget0` **(post-hoc)** | a | 170 | 166 | 4 | 3 | 3 | 0 |
| `replicate_budget0` **(post-hoc)** | b | 170 | 164 | 6 | 3 | 3 | 0 |

**No batched arm produced a single `off_track` verdict or a single
correction.** Across all six batched arms and both passes that is 0 out of
**201** answered judgments — with per-arm denominators of 111 (`n1`), 34
(`n3`), 19 (`n5`), 17 (`n6`), 10 (`n10`), 10 (`n10_w15`). The small arms carry
almost no weight on their own; `n1`'s 0/111 is the one that does.

`replicate` reproduced its own shape across passes exactly: first `off_track` at
**cursor 7** in both, corrections at **cursors 7, 11 and 15** in both, the third
budget slot spent by cursor 15 of 170 in both. The recorded run spent all three
by cursor 12 of 170. In every run of this configuration — the recorded one and
both replays — the budget is gone inside the first 9% of the run.

### 3. Where the every-event arms actually find a deviation *(rests on the post-hoc arm)*

`replicate_budget0` is the arm added **after both registered passes had run**
(`PREREGISTRATION.md`, Amendment 1); everything in this section and in §4 rests
on it and is post-hoc in that sense. It judges all 170 boundaries and never
speaks. Its `off_track` verdicts fall at cursors **`[7, 8, 9]`** — in **both** passes, and nowhere else
in 170 boundaries.

Events 7, 8 and 9 are one tool result and two `thinking_tokens` events. No
batched arm has a boundary there: `n1`'s boundaries are the assistant events
5, 6, 10, 11, 13, 15, … So the batched arms' zero is not a disagreement with the
every-event arm about the same moment — **it is a different set of moments**,
and the one stretch at which a *never-speaking* supervisor judged this
trajectory off track falls entirely inside the gap. (`replicate`, which does
speak, judges it off track almost everywhere — that is §4, and a consequence of
having spoken at cursor 7.)

### 4. The isolation: what makes `replicate` different is that it spoke *(rests on the post-hoc arm)*

**Post-hoc**, as §3: `replicate_budget0` was added after the registered passes.
It and `replicate` judge the **same 170 boundaries** and, at all 170, hold an
**identical evidence window**. Their only measured prompt
difference is the `# What you have already said to them` section — 0 characters
until `replicate` speaks, then 153 / 348 / 477 (pass a) and 145 / 281 / 409
(pass b), exactly the cumulative lengths of its own corrections.

What the same-prompt comparisons show, set out before the between-arm ones:

| comparison | prompts | `off_track` agreement |
| --- | --- | --- |
| `replicate_budget0` a vs b | byte-identical | **163 / 163** |
| `n1` a vs b | byte-identical | **52 / 52** |
| `replicate_budget0` vs `n1` (pass a) | byte-identical | **52 / 52** |
| `replicate_budget0` vs `n1` (pass b) | byte-identical | **53 / 53** |
| `replicate` vs `replicate_budget0` (pass a) | differ only in `said` | 37 / 138 |
| `replicate` vs `replicate_budget0` (pass b) | differ only in `said` | 28 / 133 |

**No same-prompt comparison here produced a single `off_track` disagreement** —
320 comparable pairs, including 3/3 at the three cursors where it is `True`.
Those 320 are drawn from **four** runs (`replicate_budget0`/a, `replicate_budget0`/b,
`n1`/a, `n1`/b) and the same calls are reused across the four comparisons, so
they are 320 comparable pairs and **not** 320 independent observations. What
they establish is the sentence in bold and nothing wider: this is an
observation over four runs, not an estimate of a sampling rate.

`self_correcting` is a different story and varies freely (114/163 agreement on
the pair of fields for the same comparison), but it only gates behaviour when
`off_track` is `True`.

Set beside that, the two arms differing **only** in `said` disagree on
`off_track` at 101 of 138 and 105 of 133 comparable cursors — `off_track` in 219
of 280 answered judgments versus 6 of 330. Before `replicate` speaks the two
agree 7/7 in both passes; after, 12/131 and 9/126. The report states those two
readings side by side and does not convert them into a significance claim.

### 5. What the judge is actually given

`judge._render` emits only `TextBlock`s. A tool call is a `ToolUseBlock`, a
tool result a `ToolResultBlock`, and reasoning a `ReasoningBlock` — **none of
them is a `TextBlock`**. So a record admitted as evidence renders as
`[assistant] ` or `[user] ` with an empty body unless the actor wrote prose.

Over the whole stream the filter admits **90** records. **11** of them render
non-empty text.

Every correction in this experiment, and all three in the recorded run, was
written with **0** records rendering non-empty text in the window:

| run | cursor | records in window | of those, rendering non-empty text |
| --- | --- | --- | --- |
| recorded | 4 | 0 | 0 |
| recorded | 8 | 3 | 0 |
| recorded | 12 | 6 | 0 |
| `replicate`/a | 7, 11, 15 | 3, 5, 8 | 0, 0, 0 |
| `replicate`/b | 7, 11, 15 | 3, 5, 8 | 0, 0, 0 |

The judge wrote "no output shows a read of …" while holding a window whose
rendered body was empty by construction. **The window-size question sits
downstream of this**: at `N=10` the judge discards 7 of the 15 new records per
batch, and of the 8 it keeps, between 0 and 1 render non-empty text.

### 6. The two frozen checks

Applied to the recorded run's three corrections, they fire exactly as
pre-registered:

- cursor 4 — **written on an empty window** (0 records).
- cursors 8 and 12 — **contradicted by the record**, via `from_isbn`: both
  assert nothing has shown `from_isbn`, and the tool result at event 7 contains
  it.

Applied to this experiment's six corrections, one fires: `replicate`/b at
cursor 11 ("Still no sign you've actually run the tests or opened the current
`from_isbn` implementation"), again via `from_isbn`. **So the error class
reproduces in an independent run of the same configuration.** No batched arm
produced a correction, so no batched arm can be scored on either check — that
is an absence of corrections, not a clean bill.

**One error the frozen checks cannot see, surfaced by reading rather than
measured.** `replicate`/a's correction at cursor 7 says the actor is "jumping to
edit `models.py`"; the record before cursor 7 holds one `Bash` grep and no edit.
That is an assertion that something *did* happen, and check 2 only covers
assertions that something *did not*. This is reported as one observation found
by reading, not as a rate — adding a check for it after seeing the data, and
then counting with it, is the move the pre-registration exists to prevent.

**And the check is a lower bound by construction**, as registered before the
run: the recorded corrections reached the actor far later than they were
written, so the actor's on-the-spot rebuttals are correct at *reception*, and a
replay has no reception.

| written at cursor | first on the wire (proxy request) | response is event | lag |
| --- | --- | --- | --- |
| 4 | 9 | 37 | 33 events |
| 8 | 13 | 79 | 71 events |
| 12 | 14 | 86 | 74 events |

Two independent records place the notes at the same points: the recorded
conversation puts each one immediately before the assistant message that is
event 37, 79 and 86, and the proxy log puts its first appearance on the wire in
request 9, 13 and 14 of the run's 33. The two orderings agree, including that
the second and third arrive one request apart. The event column is the
conversation's placement; the two are not the same measurement and are not
claimed to be.

### 7. Lapses — one cause, and it is ours

An unusable judge answer is a recorded lapse and is never retried, as in a live
run. Nothing was excluded from any count.

**All 85 lapses in this experiment have `finish_reason: "length"` and
`completion_tokens` exactly 512** — `ModelJudge.max_tokens`. The model's
reasoning tokens take a median of **511** of those 512, so nothing is left for
the JSON object the judge requires. An answered call, by contrast, uses a median
of 152 completion tokens, 91 of them reasoning.

So a lapse here is not a flaky provider and not a bad answer: it is **the judge's
own token ceiling reached while the model was still thinking.** The two error
texts it produces — `the JSON object must be str … not NoneType` when no content
escaped, and `Unterminated string` when some did — are one event seen at two
cut points.

The recorded run's 16 lapses carry those same two error texts, but that run
**did not record a finish reason**, so for it the identification is inferred
from this experiment rather than measured. Its rate, 9.4%, is between the two
arms measured here.

| run | boundaries | lapses | rate |
| --- | --- | --- | --- |
| recorded | 170 | 16 | 9.4% |
| `replicate` a + b | 340 | 60 | 17.6% |
| `replicate_budget0` a + b | 340 | 10 | 2.9% |
| batched arms, a + b | 216 | 15 | 6.9% |

`replicate` and `replicate_budget0` judge the same 170 boundaries and differ in
exactly one input, so **the 6× lapse gap follows that same `said` block** — a
post-hoc comparison, since `replicate_budget0` is the added arm — the arm carrying its own prior corrections runs the
model past 512 tokens six times as often. *Which* property of that block does it
— its length, or that arguing with a prior accusation takes more reasoning — is
**not** isolated here.

### 8. Cost

| | |
| --- | --- |
| runs | 16 (7 registered arms × 2 passes, plus the post-hoc control × 2) |
| boundaries | 896 |
| model calls | 902 (judge + writer) |
| tokens | 2,256,390 prompt / 184,890 completion |
| cost | **$6.36**, provider-reported per call, not estimated |
| SWE-bench Pro instances rolled out | **0** |

## Analysis

### What is attributable

**A1. No same-prompt comparison here produced an `off_track` disagreement** —
320 comparable pairs, drawn from four runs whose calls are reused across the
four comparisons, so not 320 independent observations. That is the whole of it:
it is an observation over four runs, **not** an estimate of a sampling rate and
not a basis for calling any between-arm difference significant. The readings in
A2 and A3 are set beside it and are not converted into such a claim.

**A2 (post-hoc arm). Batching to any `N ≥ 1` removed every correction on this
trajectory, and what did it is *which* moments get judged rather than how
many.** The zero itself is a registered reading; the *explanation* below comes
from `replicate_budget0` and is therefore post-hoc. Two measured facts carry
it. The every-event control finds this actor off track only at
cursors 7, 8 and 9, in both passes; and **no batched arm has a boundary at 7, 8
or 9** — every one of the six excludes all three (checked mechanically over the
boundary sets). On the 59 moments `replicate_budget0` and `n1` do share, they
agree on `off_track` 52/52 and 53/53. So the batched arms did not disagree with
the judge about anything; they never asked it at the only moments a
never-speaking supervisor said yes.

**A3 (post-hoc arm). Speaking changes what the judge subsequently says.** This
rests entirely on `replicate_budget0`, added after the registered passes. With
the boundary set and the evidence window held identical and the `said` block the
only measured difference, `off_track` went from 6/330 to 219/280. The judge's
own prior corrections are in its prompt, and its later reasons read as a
response to them
("Repeated prompts about running the tests have gone unanswered"). **This is a
feedback loop between the supervisor's memory and its own judgement**, and it is
what spends the budget: 91 and 104 would-have-spoken markers per run, of which
87 and 96 were blocked only because the budget was already gone by cursor 15.

**A4. The judge decides on a nearly empty prompt.** 11 of 90 admitted records
render non-empty text; all 9 corrections across the recorded run and both
`replicate` passes were written with 0 non-empty records in the window. This is
a property of `judge._render`, not of `N` or `window`.

**A5. Every lapse is `max_tokens` exhausted by reasoning tokens.** 85 of 85,
at `completion_tokens` 512 with a median of 511 spent reasoning. This is a
property of `ModelJudge.max_tokens = 512` meeting a model that reasons. The
recorded run lost 16 of its 170 boundaries to lapses carrying the same two error
texts, but recorded no finish reason, so for that run the identification is
inferred rather than measured (§7). It has nothing to do with `N`.

**A6. The factual-error class is reproducible.** The pre-registered
contradicted-by-record check fires on 2 of the recorded run's 3 corrections and
on 1 of `replicate`/b's 3, in an independent run of the same configuration.

### What is not attributable, and why

- **Whether `N=1` is "better" than `N=10`.** Every batched arm returned the same
  reading — zero. Nothing here separates them, and the arms below `n1` have
  denominators of 34, 19, 17, 10 and 10 answered judgments.
- **Whether removing corrections is good or bad.** The actor is a recording. A
  supervisor that says nothing cannot be scored on this corpus at all.
- **Which property of the `said` block drives the lapse gap.** That the gap
  follows that one input is established (§7); that it is the block's *length*
  rather than the reasoning it provokes is not.
- **Whether the cursor 7–9 stretch is about the boundary *type* (tool result vs
  assistant message) or about that specific moment.** Both every-event arms put
  their first `off_track` at cursor 7 in all four runs, which makes it
  reproducible, not explained.
- **Anything about `window` at `N ≥ 6`** — the `n10` / `n10_w15` control did
  not run; see *Read this first*.

## What this means for issue #375

The issue proposes three changes together. This experiment speaks to two of
them and adds one the issue did not raise.

1. **"`N` and `window` as jointly configured parameters"** — the deterministic
   coupling holds and the boundary is between `N=5` and `N=6`, one step
   narrower than the issue's table implied. This run adds nothing to that,
   because no arm at any `N` produced a judgement for `window` to truncate.
2. **"The judge must never receive an empty window"** — supported, and it is not
   sufficient. The recorded run's cursor-4 correction is the empty-window case,
   and batching does make it unreachable. But of the 9 corrections observed
   in total — 3 in the recorded run, 6 in this experiment — **8 were written on
   a non-empty window whose rendered body was empty**, which the proposed
   invariant does not catch — an invariant over the *count* of admitted records
   is satisfied by a window of eight blank lines. The renderer itself is
   [#380](https://github.com/Luolc/swe-lab/issues/380).
3. **The blocking mechanism** — untouched by this experiment.

A fourth thing, not in the issue and not about `N` at all: `ModelJudge`'s
`max_tokens = 512` is below what this judge needs (§7). It is filed as
[#383](https://github.com/Luolc/swe-lab/issues/383) and not restated here.

The finding the issue did not anticipate is A3, and it comes from the post-hoc
arm: **the supervisor's own memory is an input to its judge, and on this
trajectory it dominates the judge's output.** It has its own home in
[#381](https://github.com/Luolc/swe-lab/issues/381); this report does not
restate it or propose the fix.

## Open questions

- **Does A3 survive a trajectory where the actor is genuinely off track?** Here
  the never-speaking control called this actor on-track at 324 of its 330
  answered judgments, so the feedback loop had nothing true to reinforce.
- **Does A2 survive an actor that writes prose?** This actor's assistant events
  are almost all reasoning and tool calls. On a trajectory with more text
  blocks, the judge would see something, and every reading in §5 would change.
- **What does batching do to a *live* run?** Nothing here can say. The recorded
  corrections took 33–74 events to reach the actor, and that delay is a property
  of the delivery path this replay does not exercise.
- **Is `off_track`'s perfect run-to-run stability a property of this trajectory
  or of the judge?** 320/320 was measured on one recording.

## Reproduce

```sh
cd "$(git rev-parse --show-toplevel)/experiments/trace_synthesis/n_batching_replay"
uv run python replay.py shape         # §1, no credentials
uv run python replay.py self-check    # the driver == the shipped Supervisor
uv run python replay.py verify-task   # needs the gitignored parquet
uv run python analyze.py              # every number in §2–§8
```
