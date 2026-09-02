# First e2e supervised rollout — report

**Status: skeleton. Written 2026-09-02, before the run's data existed, and
deliberately empty of findings.**

Every slot below is `___` until something fills it from this run's own
record. The reason the shape comes first is the one this experiment is
otherwise about: **the person inventing a reading at the moment the data
lands is the person who wants it to say something.** So the readings are
fixed here, in advance, and the fill-in step is only allowed to answer
questions this file already asks.

**The criteria are not here.** They are frozen in
[`PREREGISTRATION.md`](PREREGISTRATION.md) and are linked, never copied — a
second copy of a closure criterion is a criterion that can drift from the
frozen one without anything failing. When a row below says "closes when",
read it there.

## 1. The seven points

One row per point of
[task 01's acceptance table](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end),
judged against [`PREREGISTRATION.md` §4](PREREGISTRATION.md#4-per-point-closure--file-field-judgment).
Three slots each, and none of them may be merged:

- **Evidence from this run** — the artifact and the field, with the value.
  Not "the record shows it"; the file, the key, the number.
- **Verdict** — `closed` / `not closed` / `left open`. `left open` is a real
  verdict, not a soft `not closed`: §4 fixes that a run which produced zero
  corrections leaves **point 3 open** rather than closing it negative, since
  a silent real run says nothing either way.
- **Assertion relied on** — `A` (the actor's own native session transcript),
  `B` (`proxy_log.jsonl`'s wire capture), or `none`. §4 and
  [§5](PREREGISTRATION.md#5-evidence-for-points-1-3-and-4--two-assertions-not-one-on-two-different-bases)
  fix that points **1, 3 and 4** require A or B unconditionally, that point 4
  rests on B alone and is therefore **weaker** than point 3, and that if the
  native transcript is unavailable for this run, all three are **not closed**
  regardless of what `proxy_log.jsonl` alone shows.

| # | Claim | Evidence from this run | Verdict | Assertion |
|---|---|---|---|---|
| 1 | Supervisor attached to the actor's **live** stream | `___` | `___` | `___` |
| 2a | Barrier holds: no gold patch, no hidden tests in the supervisor's input | `___` (consumed, not re-verified — §4) | `___` | `___` |
| 2b | Criterion sha verified, mismatch refuses **the run** | `___` (closed by the suite, not by this run — §4) | `___` | `___` |
| 3 | Policy speaks at least once **because of a real deviation** | `___` | `___` | `___` |
| 4 | Correction arrives **mid-turn**, matching the measured wire shape | `___` | `___` | `___` |
| 5 | Rollout completes, patch taken **against the pre-agent baseline**, grading runs | `___` | `___` | `___` |
| 6 | Trace persisted, **interjection in it**, provenance complete | `___` | `___` | `___` |
| 7 | The **outcome word is correct** | `___` | `___` | `___` |

### 1a. The corrections themselves

To be filled by the owner. Two things go here, and they are separate:

- **The text of every correction delivered**, verbatim, and the judgment of
  whether any of them leaked the answer — gold-patch content, a test name, a
  line to change. This is the first real evidence for that barrier; the
  suite's `test_supervisor_input_carries_no_privileged_field` constrains the
  supervisor's *input*, not what a model then chose to say. `___`
- **The delivery's morphology in `proxy_log.jsonl`** — counted row by row,
  not sampled and generalised: how many outbound requests carry the block as
  the **last** message (the injection itself), how many carry it **retained
  in history** further back, and how many *responses* carry it (which must be
  zero, or it is our own narration rather than the actor's context). `___`

## 2. The three readouts

[`PREREGISTRATION.md` §6](PREREGISTRATION.md#6-readouts-required-alongside-the-seven-points)
requires these as **three lines, never merged into fewer** — a count that
looks like a cost is a defect shape this codebase has already named more than
once.

1. **Actor-side cost — a range, not a point.** `___`
2. **Supervision-side call counts — counts, not a rate.**
   `metrics["supervision.boundaries"]` = `___`,
   `metrics["supervision.corrections"]` = `___`. Read together, never alone:
   `corrections == 0` has two sources — nothing was off track, or delivery
   was broken — and only `boundaries` separates them
   ([`RUNBOOK.md` §4](RUNBOOK.md#4-reading-the-result)).
3. **Supervision-side tokens and dollars — absent, and the absence is the
   readout.** Not implemented: `usage` is discarded before anything durable
   sees it (§6.3). State what the absence means as well as that it exists —
   **line 2's counts cannot be multiplied into a dollar figure**, because
   each judge call's context grows with `window` and the boundary index, so
   token count is not constant across calls even within one run. `___`

## 3. The outcome word

`rollout_outcome` recorded **verbatim**, judged against `RolloutOutcome`'s
members read fresh from
[`src/swe_lab/rollout.py`](../../../src/swe_lab/rollout.py) at report time —
not from any list in this file or the pre-registration, both of which are
snapshots.

- **Recorded outcome:** `___`
- **What actually happened, in one sentence:** `___`
- **Do they match (point 7):** `___`

Two categories carry obligations of their own
([§7](PREREGISTRATION.md#7-failure-classification)), and each keeps its slot
whether or not it fires:

- **If `SUPERVISION_FAILED`** (`supervision.unhealthy`): points 1, 3, 4 and 6
  are judged **as of the moment supervision stopped being trustworthy**, not
  waved through because the rollout completed. Applies: `___`. If it applies,
  the moment and its evidence: `___`.
- **If `TIMED_OUT` on the first attempt**: presumed **ours**, a wiring
  failure, not the actor's — reclassified only if `proxy_log.jsonl`'s own
  timeline shows the actor actively working up to the wall clock rather than
  idle on the channel. Applies: `___`. If it applies, the timeline reading:
  `___`.

## 4. What this run does not claim

[§8](PREREGISTRATION.md#8-what-this-run-deliberately-does-not-measure-or-claim)'s
four exclusions, each restated as a live check on this report's own text
rather than as a promise made once:

- **Not an effect estimate.** No sentence in this report says supervision
  helped or hurt. Checked: `___`
- **Not the stability batch.** Nothing here sizes, schedules or pre-empts it.
  Checked: `___`
- **Not a rate.** No `resolved N/M` or `Rate`-shaped number is computed from
  this one run. Checked: `___`
- **Not a comparison to `control_rollout_and_unit_test`.** The control arm
  exists in the registry; this run did not run it, and nothing here reads as
  if it had. Checked: `___`

## 5. Cost and hygiene

- **Wall clock:** `___`
- **Actor spend:** `___` (see §2 line 1 for how it is reported)
- **Containers:** started `___`, left behind `___`. The window this ran in:
  `___`.

## 6. Artifacts

The files this report is read against, by path, so a later reader checks the
claims rather than the prose:

- Rollout record: `___`
- `supervisor.jsonl`: `___`
- `proxy_log.jsonl`: `___`
- Native session transcript (Assertion A): `___`
- Converted trace: `___`

## 7. Open questions

Anything this run raised and did not answer, one line each — **not** softened
into the sections above. `___`
