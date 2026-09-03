# Pre-registration — `N`-batching replay

Frozen before the first model call. Committed at the sha named in
[`REPORT.md`](REPORT.md); `git log --follow PREREGISTRATION.md` shows when.

## This experiment produces no verdict

There is **no threshold, no pass/fail, and no rule by which an `N` wins.** The
output is a description of what the judge says at each `N` on one recorded
rollout. The input is deterministic and the run is re-runnable at ~$3, so what
needs freezing is the **list of readings** — not a decision rule. Inventing one
would import the failure mode of the
[honesty-scorer pre-registration](../honesty_scorer/README.md): every criterion
written to settle one expensive irreproducible run becomes a hole to pick
afterwards.

## Fixed inputs

| | |
| --- | --- |
| corpus | `~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0`, **read-only** |
| event stream | 170 events (59 assistant, 31 tool-result user, 78 system, 1 rate-limit, 1 result), sha256 `28abd03e…f55403` |
| task text | `instance.prompt()`, sha256 `3f42ec71…8af83a`, 3339 chars — recovered from the recorded conversation and **verified byte-identical to the dataset's own `prompt()`** by `replay.py verify-task` |
| criterion | the pinned artifact, digest `ffb2dadf…2900a1`; `gold_patch` not passed, which changes only `Criterion.overlap_checked` and never the text the judge sees |
| model | `anthropic/claude-sonnet-5` (`workflow.definitions.SUPERVISOR_MODEL`) |
| budget / cooldown | 3 / 4 (`SUPERVISOR_BUDGET`, `supervising_policy` default) |
| transport | `judge.openrouter_transport`, keys from `OPENROUTER_API_KEYS` |
| sampling | only `max_tokens` is sent (512 judge, 256 writer). **Temperature is not sent**, so the provider's default applies and repeated runs are not expected to agree. |
| policy code | the repo's own `SpeakWhenOffTrack`, `EvidenceFilter`, `ModelJudge`, `ModelWriter` — no reimplementation |

`N` is the only thing that varies, except in the one arm that says otherwise.

## Arms

| arm | `N` | window | boundaries |
| --- | --- | --- | --- |
| `replicate` | every event (today's behaviour) | 8 | 170 |
| `n1` | 1 | 8 | 59 |
| `n3` | 3 | 8 | 19 |
| `n5` | 5 | 8 | 11 |
| `n6` | 6 | 8 | 9 |
| `n10` | 10 | 8 | 5 |
| `n10_w15` | 10 | **15** | 5 |

`n6` is the first `N` at which a batch overflows `window=8` on this corpus;
`n10_w15` is the only arm that moves `window`, and exists so the `n10` result
can be read as *batching* or as *truncation* rather than as one undivided thing.

**Batch boundary:** the position of every `N`-th assistant event. A trailing
partial batch is not judged, so the count is `floor(59 / N)`.

**Cursor stays the event index**, as `Observation.cursor` is defined ("how many
stream events have been consumed"). `cooldown` is compared against it, so at
`N ≥ 3` consecutive boundaries are more than 4 events apart and cooldown can
never bind. That is a consequence of holding cooldown fixed, and the count of
times each gate actually closed is one of the readings below.

## Execution

Two full passes over the arms in table order: pass **a**, then pass **b**.
Same-arm repeats are therefore separated by a whole pass, and every arm is
measured once per pass, so a between-arm comparison inside a pass is
time-matched.

**Why two passes, when the brief specified one:** a single `replicate` run
cannot measure run-to-run variance — variance needs at least two runs of one
configuration, and the recorded run logged no per-boundary verdicts to pair
against. Without repeats, no difference between arms could be separated from
noise, which is the thing the `replicate` arm exists to bound. The second pass
costs ~140 extra model calls and ~$1.4. Declared here as a deviation from the
brief, with its reason, before any call was made.

**No retry, ever.** A failed or unparseable judge answer becomes a
`PolicyLapseError` and is recorded as a lapse, exactly as in a live run. No run
and no boundary is excluded from any count for any reason.

## The readings, frozen

Per arm and pass:

1. counts of `off_track`, `self_correcting`, would-have-spoken markers,
   `spoke`, `lapse`, `gap`, and boundaries whose answer parsed at all;
2. every correction: its cursor, the admitted evidence in the window at that
   moment, and its verbatim text;
3. the cursor and the assistant-event count at the **first** `off_track`, and
   at the first would-have-spoken marker;
4. where the three budget slots were spent — the cursor of each correction and
   the cursor at which the third was delivered;
5. per judgment: evidence in the window, evidence dropped by the window
   (cumulative and new-since-last-boundary), records that render **non-empty
   text** into the judge's prompt, and the prompt's character count;
6. markers that produced no speech, split into blocked-by-budget and
   blocked-by-cooldown;
7. lapses attributed to what the provider returned (null content, truncation at
   `max_tokens`, transport error, other);
8. measured prompt/completion tokens and provider-reported cost.

## The two checks on a correction

Today's three corrections were rebutted by the actor. Both checks below are
**falsifiers**: firing reproduces a known error class; not firing means this
checker did not see one, which is **not** evidence that the correction is
sound. Neither is a score, and neither is summed into a verdict. Both are
implemented in `analyze.py`, whose constants are the normative text.

1. **Written on an empty window** — `evidence_in_window == 0` at that boundary.
   Today's correction at cursor 4 is the instance.
2. **Contradicted by the record** — the correction matches the frozen
   `NEGATION_PATTERNS` list *and* names an artifact from the frozen
   `ARTIFACT_PREDICATES` table whose record predicate is already true strictly
   before that cursor. Today's corrections at cursors 8 and 12 are the
   instances: both assert nothing has shown `from_isbn`, and event 7's tool
   result contains it.

**What check 2 cannot see, stated before the run:** today's corrections reached
the actor *later* than they were written — the run's own conversation shows the
first surfacing after the actor had already read the `from_isbn` body — and the
actor's rebuttals are correct **at reception**, not necessarily at the cursor
the line was written at. A replay has no reception: the stream is a recording
and nothing is delivered. So check 2 is evaluated at the write cursor only, and
it is a lower bound on the error class by construction, not by bad luck.

## Not settled by this design, whatever the numbers

- The actor's trajectory is fixed. This can say what a supervisor at each `N`
  **would say** on this trajectory; it cannot say what an actor would then do.
- n = 1 trajectory, 1 instance, 1 actor model, 1 supervisor model.
- No `N` is selected here.
