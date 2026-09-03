# Pre-registration: the first real supervised rollout

> **Frozen before any run.** Everything below is protocol, fixed at the commit
> that adds this file. **Nothing has been run. This file changes nothing that
> executes.** On `main` at the time of writing, `supervision_factory` is never
> invoked anywhere in `src/`, and neither `supervised_rollout_and_unit_test`
> nor `control_rollout_and_unit_test` is a registered workflow. The wiring that
> adds both is [PR #349](https://github.com/luolc/swe-lab/pull/349) — **open,
> not merged**, verified 2026-09-01 (`gh pr view 349 --json state` →
> `"OPEN"`). This document assumes #349 or its merged successor is on `main`
> by the time this run executes; §3 names what it must have landed, read from
> that PR's own diff rather than from a description of it.
>
> **Frozen means the text is frozen, not that the file is closed.** Nothing
> above or below this line is ever edited after the run; §10 appends what was
> later learned about one of these criteria, dated and attributed. **Read §10
> before relying on §4** — one criterion there is narrower than the claim it is
> labelled with, and this document is where that claim is at its widest.

## 1. What this run can and cannot establish

**The question is which of task 01's seven acceptance points a real run
closes — not whether supervision helps.**
[`docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end`](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end)
is explicit that the deliverable is a *working pipeline*, and that "how much
supervision helps is measured by a downstream consumer, not here." Nothing in
this run computes an effect, a rate, or a comparison against a control arm —
`control_rollout_and_unit_test` exists in the wiring plan but this
pre-registration covers **one rollout of the supervised (treatment) arm
only**, per the explicit instruction to run one.

A resolved rollout and an unresolved one are **both complete results** here.
Task 01's own verification clause says so:
> "A supervised rollout that fails to resolve is a complete result; what would
> make it incomplete is a point above that nothing can demonstrate."

So the outcome that ends this pre-registration's obligations is **seven
closure judgements**, not `resolved` / `not resolved`. A run whose patch fails
grading but for which all seven points close is a success of this experiment;
a run that resolves but whose supervisor pump died mid-run is not.

## 2. The instance

`instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb`
(SWE-bench Pro), from
[issue #261](https://github.com/luolc/swe-lab/issues/261)'s candidate table —
the fastest of the four "mixed outcome, both fast and cheap" candidates
(mean rollout wall 510 s over 2 rollouts, 1/2 resolved). Picked for wall-clock
cost alone: this run buys nothing from picking a slower instance, and #261's
own caveats name four *other* instances as possibly-flaky
(`claude-code-by-agents-recursive-delegation`, `clack-async-autocomplete-options`,
`eicrud-keyset-pagination-cursor`, `igel-persist-feature-schema`) — this one is
not among them.

**Wall time is environment-relative** (#261's own caveat) — 510 s is a ranking
signal against the other 39 candidates, not a prediction for this run's
sandbox.

## 3. What #349 must have landed before this can run

Read from [PR #349](https://github.com/luolc/swe-lab/pull/349)'s own diff
(`gh pr diff 349`) on 2026-09-01, not from its description:

- **`supervision()`** (`src/swe_lab/trace_synthesis/channel.py`) — the
  `SupervisionFactory` builder. It constructs the policy *while the run's
  observers are assembled, before the sandbox exists*, which is what makes
  point 2b's refusal a refusal to start rather than a late abort.
- **`SupervisedRun.policy: SpeakPolicy`** — a built policy, not a factory
  (superseding the `policy_factory` shape an earlier draft of this document
  assumed; corrected here against the actual diff rather than left standing).
- **Two registered workflows** in `src/swe_lab/workflow/definitions.py`:
  `supervised_rollout_and_unit_test` (the treatment arm, `supervision()`
  wired in) and `control_rollout_and_unit_test` (same harness, channel, relay
  and pump, policy is `NeverSpeak()` instead). This pre-registration covers
  one run of the **supervised** one only (§1, §8).
- **`openrouter_transport`** (`src/swe_lab/trace_synthesis/judge.py`) — the
  real `Transport`: `urllib.request` against `OPENROUTER_ENDPOINT`, keyed by
  the `OPENROUTER_API_KEYS` environment credential, no retry. This is what
  makes the judge and writer calls in this run **real, paid, external calls**,
  not a fake.
- **`BOUNDARIES_METRIC = "supervision.boundaries"` and
  `CORRECTIONS_METRIC = "supervision.corrections"`**
  (`src/swe_lab/trace_synthesis/channel.py`), landed in the same PR — see §6.
- **Point 2b's refusal now has a named test on the run's own path**:
  `test_a_forged_criterion_stops_the_run_before_a_sandbox_exists` and
  `test_the_shipped_supervised_arm_carries_the_pinned_criterion`. If #349
  merges with these tests intact, point 2b is closed by the test suite and
  does **not** additionally require evidence from this run (§4).
- **`event_stream.jsonl` must actually be written under `capture="proxy"` plus
  a correction channel** — fixed by commit `190d054` on #349's branch, found
  and fixed after the rest of this pre-registration's evidence rules were
  already drafted. Before the fix, a supervised proxy-captured run polled a
  file nothing wrote: no events → no `result` → no turn boundary → the
  channel never closes → the actor waits on the FIFO until the wall clock
  kills it, arriving as `TIMED_OUT` — our wiring gap billed to the actor's
  budget. This is why §7 pre-registers a default-attribution rule for a
  `TIMED_OUT` first run rather than trusting the word at face value: the
  failure mode that produces it was only found once, and finding it once is
  not the same as having ruled it out for good.

**A known coverage gap, named with its owner and its timing, not waved off
as a limitation.** None of the 9 docker-marked container tests exercises the
harness's actual generated supervised-invocation script — the shell assembly
that starts the in-sandbox proxy, sets `ANTHROPIC_BASE_URL`, and branches on
`capture` / `correction_channel` (the exact code path `190d054` fixed a bug
in). **Owner:** the wiring line, on #349. **Timing:** a stub-agent container
test (cheaper than a full rollout) is in progress as the first coverage step,
status not yet known as of this writing, and is expected before this
pre-registration's run rather than after it.

**What #349 does *not* give this run.** `ModelJudge.__call__` and
`ModelWriter.__call__` still only keep `requested_model`, `response_model`,
`sampling_sent` and `raw` (the answer text) on each `Call` —
`openrouter_transport` returns the full decoded OpenRouter response, `usage`
block included, but nothing between it and `Call` keeps that block. §6 is
written around this gap rather than assuming it closed by #349.

## 4. Per-point closure — file, field, judgment

Copied from
[the acceptance table](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end)
row by row, with what *this run's* evidence for each point actually is. A row
below that repeats the table's "what proves it" column without narrowing it
to this run is not a closure criterion, it's a citation — narrowed where the
table leaves room to read it two ways after the fact.

| # | Claim | Closes when | Judged against |
|---|---|---|---|
| 1 | Supervisor attached to the actor's **live** stream | Requires **§5's Assertion A** (plus `proxy_log.jsonl` corroborating real traffic at the claimed boundaries), unconditionally — `supervisor.jsonl` existing and `metrics["supervision.boundaries"] > 0` are necessary but not sufficient, since both are the pipeline's own account of itself. If §5's native-transcript dependency is unavailable, this point is **not closed** (see §5's branch). | `test_the_rollout_composes_the_supervisor_when_one_is_configured`, `test_the_supervisors_account_of_the_run_is_persisted` confirm composition and artifact; neither drives a real actor. |
| 2a | Barrier holds: no gold patch, no hidden tests in the supervisor's input | Consumed as-is, not re-verified here (the table's own instruction: "consumed here, not re-implemented"). | `test_supervisor_input_carries_no_privileged_field` (task 05, not re-run). |
| 2b | Criterion sha verified, mismatch refuses **the run** | Closed by the test suite once §3's two named tests are on `main` — this point does **not** additionally require evidence from this run. If #349 merges without them, or with a weaker refusal, this row reverts to open and this run cannot close it either (a run against a *correct* criterion says nothing about what happens against a forged one). | `test_a_forged_criterion_stops_the_run_before_a_sandbox_exists`, `test_the_shipped_supervised_arm_carries_the_pinned_criterion` (§3). |
| 3 | Policy speaks at least once **because of a real deviation** | Requires **§5's Assertion A**, unconditionally. `supervisor.jsonl` containing ≥1 row with `kind: "spoke"` whose `policy` is not `"speak-at"` (equivalently, `metrics["supervision.corrections"] > 0` on a `SpeakWhenOffTrack` run) is necessary — a run with zero such rows leaves this point **open**, not closed-negative, since a silent real run says nothing either way — but not sufficient by itself; Assertion A is what confirms a delivery the self-report claims actually reached the actor. If §5's native-transcript dependency is unavailable, this point is **not closed**. | `supervisor.jsonl` and the `metrics` field read directly off this run's own record; no test claims a real run's deviation count. |
| 4 | Correction arrives **mid-turn**, matching the measured wire shape | Requires **§5's Assertion B**, unconditionally — this row is not closable from `supervisor.jsonl` under any reading, and closes on a *weaker* basis than points 1 and 3 (Assertion B rests on a stated trust assumption about the proxy, not a boundary this project doesn't control). If §5's native-transcript dependency is unavailable, this point is **not closed** either, since Assertion B alone collapses to self-report plus trust assumption. The actor's subsequent behavior visibly responding (§5), if observed, is recorded alongside but is not what closes this row. | `experiments/trace_synthesis/sandbox_fold_check/` established the reference wire shape this run's capture is compared against. |
| 5 | Rollout completes, patch taken **against the pre-agent baseline**, grading runs | The rollout record has `patch_base_ref` set (ADR-0014); the grading entry's `metrics` has `unit_test.resolved` present (either `true` or `false` — presence, not value, closes this point). | `test_a_stub_agent_produces_an_empty_patch_on_a_dirty_image` (existing test, not re-run; this is a property of this run's own record). |
| 6 | Trace persisted, **interjection in it**, provenance complete | The converted trace contains the interjection text (if point 3 fired one) surviving conversion; `run_provenance()`'s stamped fields plus `extra["agent_model"]` are present in the record. | `test_an_interjection_survives_conversion_into_the_trace` (existing test, not re-run; a property of this run's own trace). |
| 7 | The **outcome word is correct** | `rollout_outcome` in the rollout record matches what actually happened, judged against the seven `RolloutOutcome` members read fresh from `src/swe_lab/rollout.py` at report time — not from this document's §7, which is a snapshot and may be stale by the time the run happens. | `tests/test_rollout.py`'s named tests pin the words apart; this row is a judgment call on one run's record against them. |

**Structural reason points 1 and 4 cannot close on `supervisor.jsonl` alone**,
per [the acceptance table's own rule](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end):
`supervisor.jsonl`, the trace and the record are all written by the pipeline
being checked, so "the run says it did" and "it did" are one statement made
twice. That rule, as currently written in the plan, names **points 1 and 4**.

**A discrepancy this document does not resolve on its own authority, and does
not let widen or narrow the frozen set.** Orchestra's most recent operational
guidance (2026-09-01) instead scopes the independent-evidence requirement to
**points 3 and 4**. The two sources disagree about whether point 1 or point 3
is the one that needs non-self-collected evidence. **This document freezes the
stricter union — points 1, 3, and 4 — for this run, with no later waiver**:
whichever of 1/3 a future reconciliation drops does not retroactively loosen
this pre-registration once it exists; a narrower rule only ever applies to a
*new* pre-registration written before its own run. Point 2b is unaffected
either way (§4).

## 5. Evidence for points 1, 3 and 4 — two assertions, not one, on two different bases

**Fixed by ruling (2026-09-02), not left for a run to discover.** No single
writer inside the container is independent in the sense points 1, 3 and 4
need. But "the cross-check" is not one thing with one trust question — it is
**two separate assertions, each carried by a different record with a
different trust basis**. Collapsing them under one label is what an earlier
draft of this section did, and it is wrong the same way twice over: it named
a broad category ("our collection code," "independent evidence") and treated
one narrow property as covering the whole category, rather than naming each
thing inside the category and asking whether the evidence actually reaches
it. Split, the two assertions don't share a trust question, so they don't
share a verdict.

**Assertion A — the correction actually reached the actor's context.** Carried
by the **actor's own native session transcript**
(`/agent-home/.claude/projects/-app/*.jsonl` inside the container — the
`docker rm` evidence-destruction hazard in
[`docs/conventions.md`](../../../docs/conventions.md)) — Claude Code's own
internal session persistence, written by the CLI for its own purposes
(resume, etc.), not by anything this project's harness or capture code
produces. This is a different file from the workspace's `event_stream.jsonl`,
which **is** our own redirected capture of the actor's stdout.

- **Rules out:** the supervisor/relay/collection code
  (`src/swe_lab/trace_synthesis/channel.py`, `supervisor.jsonl`'s account)
  falsely claiming a delivery that never happened — a broken or fabricating
  relay cannot also write a correctly-arrived block into a file the actor's
  own CLI process persists for itself.
- **Does not rule out:** the actor's CLI being wrong consistently across its
  two own serializations — the native transcript and the workspace's
  `event_stream.jsonl`/stdout capture — since both originate from the same
  process. This is the **permanent limitation**: it would need provider-side
  evidence this project cannot obtain, stated once, here, not re-litigated
  per run.

**Assertion B — the correction's wire shape and position match what was
measured.** Carried **only** by `proxy_log.jsonl`, the in-sandbox proxy's wire
capture — and that recorder **is** this project's own code
(`cc-reverse-proxy`, run in-sandbox per
[ADR-0012](../../../docs/decisions/ADR-0012-in-sandbox-capture-proxy.md)).

- **Not ruled out here, only trusted:** the proxy fabricating or corrupting
  what it records. This is a **stated trust assumption**, not a proven one —
  checking it against itself would be circular. If the assumption is wrong,
  nothing in this run would catch it.
- This makes **point 4 weaker than point 3**: point 3 (delivery happened) can
  rest on Assertion A, which crosses a boundary this project does not
  control; point 4 (wire shape/position) has no such record and rests on
  Assertion B alone. An earlier draft of this section used one evidence tier
  for both, which lent point 4 a strength it does not have.

**A dependency this document takes as a precondition, not an assumption.**
Assertion A requires the native transcript to actually be extracted before
the container is destroyed — work in progress (`swelab-inproxy-impl`, a
`before_destroy` sandbox observer pulling the whole `projects/` subtree,
because a single `*.jsonl` glob does not adequately describe the record).
**Its landing status by run time is not yet known.** This document fixes what
happens in both cases now, rather than deciding after seeing which one holds:

- **If the native transcript is available for this run:** point 1 and point 3
  close on Assertion A (point 1: `proxy_log.jsonl` also corroborates real
  actor traffic at the claimed boundaries, ruling out a synthetic stream, in
  addition to Assertion A ruling out a fabricated `supervisor.jsonl`; point 3:
  Assertion A directly). Point 4 closes on Assertion B, with its trust
  assumption stated in the report, not hidden.
- **If it is not available** — the extraction did not land, or failed for
  this run — **Assertion A does not exist**, and only `proxy_log.jsonl`
  remains, which is our own code. Both assertions then collapse to the same
  self-report-plus-trust-assumption shape. **Points 1, 3 and 4 are not marked
  closed in that case**, regardless of what `proxy_log.jsonl` alone shows —
  a run that reaches this branch reports all three as open, and says why.

**A third thing, stronger than either assertion if it happens: the actor's
subsequent behavior visibly changing in response to the correction.**
Recorded alongside either branch above, if observed, but never what closes
any of the three points — it is the hardest of the three to make a
mechanical criterion, so it stays observational only.

**If `proxy_log.jsonl` itself is unavailable for this run** — the recorder
failed to attach, the log is empty, or `capture="proxy"` was not actually
configured — none of points 1, 3 or 4 close under either branch above, since
Assertion B has no record to rest on and Assertion A's corroboration (point 1)
has nothing to corroborate against either.

## 6. Readouts required alongside the seven points

> **Three lines, not one, and not merged into fewer.** A count that looks
> like a cost is the textbook shape of a defect this codebase has already
> named more than once this cycle — this section exists specifically so the
> handoff note this feeds (`docs/trace-synthesis/downstream-scale-note.md`,
> already being scheduled against downstream) cannot round it down to one
> number.

1. **Actor cost — measured, $2.04–$4.17/rollout** (mean $3.00), from
   [`docs/trace-synthesis/downstream-scale-note.md`](../../../docs/trace-synthesis/downstream-scale-note.md)'s
   own two data points. This run's actor cost is read from its own `result`
   event the same way those two were.
2. **Supervision-side call counts — this run will produce them.**
   `metrics["supervision.boundaries"]` and `metrics["supervision.corrections"]`
   on the rollout record (`BOUNDARIES_METRIC` / `CORRECTIONS_METRIC`,
   `src/swe_lab/trace_synthesis/channel.py`, landed in #349 — §3): how many
   events the supervisor was consulted about (its judge-call count, since
   `SpeakWhenOffTrack` judges every boundary), and how many corrections it
   actually delivered (its writer-call count, one per `kind: "spoke"` row).
   Reported as raw counts from this one run, not a rate (§7's `Rate`
   precondition is about batches; one run has no denominator worth one).
3. **Supervision-side tokens and $ — not implemented. This run will not
   produce them.** `openrouter_transport` (§3) returns OpenRouter's full
   response, `usage` included, but `ModelJudge.__call__` /
   `ModelWriter.__call__` keep only `requested_model`, `response_model`,
   `sampling_sent` and `raw` on each `Call` — the `usage` block is discarded
   before anything durable sees it, and no other code path captures it either.
   **State the absence, and what it means:** a downstream reader cannot
   convert line 2's counts into a dollar figure by multiplying by a per-call
   average, because each call's context grows with `window` and the boundary
   index — the judge's prompt includes the actor's evidence window
   (`_prompt` in `judge.py`), so token count is not constant across calls
   within a single run, let alone across runs. A count is not a proxy for a
   cost here; treat it as what it is, a count.

## 7. Failure classification

The report records this run's `rollout_outcome` **verbatim**, as
`RolloutOutcome` defines it in [`src/swe_lab/rollout.py`](../../../src/swe_lab/rollout.py)
at report time — not as a list copied into this document, which would be a
second, staler copy of the same taxonomy the moment that enum changes. Point
7's closure criterion (§4) is exactly this: the recorded word matches what
actually happened, judged against the source's current members, not this
file's.

Two ending *categories* get special handling in the report regardless of
which specific word lands, because both are properties of *this
pre-registration's* obligations rather than of the taxonomy itself:

- **`SUPERVISION_FAILED`** — `SUPERVISION_METRIC` (`supervision.unhealthy`)
  fired, meaning the pump died or the channel closed uncleanly mid-run. If
  this happens, points 1, 3, 4 and 6 are judged **as of the point supervision
  stopped being trustworthy**, not waved through because the rollout as a
  whole "completed" — a run whose supervisor died halfway is not evidence for
  the points that needed the second half.
- **`NO_PATCH` or a resolved/unresolved `PATCH_PRODUCED`** — both are complete
  results per §1; neither alone closes or fails any of the seven points, which
  are evaluated independently of whether the patch resolved.
- **`TIMED_OUT` on this run's first attempt is presumed ours, not the
  actor's, unless evidence points to the actor.** ADR-0015 charges a timeout
  to the actor by default, and that default is right for an unsupervised run
  — but §3 names a bug, fixed once (commit `190d054`) and found only once,
  where a supervised proxy-captured run with no `event_stream.jsonl` hung the
  actor on its stdin FIFO until the wall clock killed it, and that failure
  arrived **indistinguishable from a genuine `TIMED_OUT`** from the rollout
  record alone. Finding that path once is not the same as having ruled out a
  sibling of it. So for this run specifically: a `TIMED_OUT` outcome is
  reported provisionally as a wiring failure, and only reclassified to "the
  actor's own timeout" if `proxy_log.jsonl`'s own timeline shows the actor
  actively working (tool calls, turns) up to the wall clock rather than idle
  on the channel.

## 8. What this run deliberately does not measure or claim

- **Not an effect estimate.** No claim of the form "supervision helped/hurt"
  is made or implied by any outcome of this run — task 01's own description
  rules this out, and one run of one instance could not support it regardless.
- **Not the stability batch.** The batch's size "has no committed home yet"
  (plans/README.md) and is a separate, later step gated on all seven points
  closing here first.
- **Not a rate.** §6 explains why; no `resolved N/M` or `Rate`-shaped number
  is computed from this single run.
- **Not a comparison to `control_rollout_and_unit_test`.** That workflow is
  named because the wiring PR builds both, not because this pre-registration
  runs both. A control run may follow separately, pre-registered on its own
  terms, once it has something worth comparing against.

## 9. What may still change

**Frozen:** the instance choice (§2), the seven closure criteria (§4), the
independent-evidence plan (§5), the readout list (§6), the failure-handling
rules (§7). Changing any of these after the run starts is what this document
exists to prevent.

**Not frozen:** how the report presents the findings, and any prose
explaining *why* a point closed or didn't, once the run's evidence exists.

**Re-runs.** If the run fails for an infrastructure reason unrelated to the
seven points — the sandbox never came up, the harness itself crashed before
the actor started — it may be re-run once, logged as a re-run with its
reason. A run that starts the actor and reaches any of the seven points'
evidence is not re-run regardless of what it shows, including
`SUPERVISION_FAILED`, which is itself one of the possible closures of point 7,
not a reason to discard the run.

## 10. Addenda — appended after the run, nothing above edited

An addendum is not an amendment. **The registered text is left exactly as it
was written**, because a frozen document's entire value is that it cannot be
edited afterwards: the moment it may be edited *when the edit is obviously an
improvement*, it is no longer frozen — "obviously an improvement" is judged by
someone who has seen the results, and that is precisely the input a
pre-registration exists to exclude. A narrowing feels safe, but the safety
comes from our present judgment, not from anything that was true at
registration.

The symmetric failure is leaving a reader of this file alone with a claim we
now know to be wider than its test. Editing without saying so destroys the
record; saying nothing sets a trap. Appending says it and keeps the record.

### 10.1 Point 3's registered claim is wider than its registered criterion

*Appended 2026-09-03 by `swelab-integ-impl` in
[PR #406](https://github.com/Luolc/swe-lab/pull/406), following
[#376](https://github.com/Luolc/swe-lab/pull/376) and
[#378](https://github.com/Luolc/swe-lab/issues/378). §4's row 3 is unchanged
and stays unchanged.*

**As registered**, point 3's claim reads *"Policy speaks at least once **because
of a real deviation**"*.

**What §4 registers as its closure test** is a `supervisor.jsonl` row with
`kind: "spoke"` whose `policy` is not `"speak-at"`, plus §5's Assertion A for
delivery. That separates a **judged** utterance from a **scheduled** one. It
does not reach **real**: no outcome of this test can differ according to
whether the deviation was real, so the clause was never something the run could
have failed.

**This was true before the run, in this file and in task 01's acceptance
table** — it is not a misreading of the evidence afterwards. The run then
produced evidence pointing the other way on the untested half: all three
corrections were false of the actor by the time they arrived, and the first was
decided on an **empty** evidence window.

**Point 3 is still closed**, on the test §4 registered. It is recorded as closed
on the narrower thing, which is what a closure on this row ever meant.

Where the narrowed statement is read, none of it editing anything here:

- [`REPORT.md` §1b](REPORT.md#1b-was-it-a-real-deviation-three-checks-three-noes)
  — the correction-by-correction readings, with `witness.py` printing each.
- [`REPORT.md` §1](REPORT.md#1-the-seven-points) — row 3's verdict cell, which
  states what the closure does and does not reach.
- [`docs/trace-synthesis/plans/README.md`](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end)
  — task 01's acceptance table, same narrowing.
- [`docs/trace-synthesis/downstream-scale-note.md`](../../../docs/trace-synthesis/downstream-scale-note.md)
  §5 — the copy written for a reader outside this repo, carrying its own
  revision notice because v0.3.0 shipped pointing at it.
