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
| 1 | Supervisor attached to the actor's **live** stream | `supervisor.jsonl` exists in the run's output artifacts and `metrics["supervision.boundaries"] > 0` (§6) — the account of a real actor's boundaries, not a synthetic stream. See the flag below the table for whether this point additionally needs §5's independent evidence. | `test_the_rollout_composes_the_supervisor_when_one_is_configured`, `test_the_supervisors_account_of_the_run_is_persisted` confirm composition and artifact; neither drives a real actor. |
| 2a | Barrier holds: no gold patch, no hidden tests in the supervisor's input | Consumed as-is, not re-verified here (the table's own instruction: "consumed here, not re-implemented"). | `test_supervisor_input_carries_no_privileged_field` (task 05, not re-run). |
| 2b | Criterion sha verified, mismatch refuses **the run** | Closed by the test suite once §3's two named tests are on `main` — this point does **not** additionally require evidence from this run. If #349 merges without them, or with a weaker refusal, this row reverts to open and this run cannot close it either (a run against a *correct* criterion says nothing about what happens against a forged one). | `test_a_forged_criterion_stops_the_run_before_a_sandbox_exists`, `test_the_shipped_supervised_arm_carries_the_pinned_criterion` (§3). |
| 3 | Policy speaks at least once **because of a real deviation** | `supervisor.jsonl` contains ≥1 row with `kind: "spoke"` whose `policy` is not `"speak-at"` — equivalently, `metrics["supervision.corrections"] > 0` on a run whose policy is `SpeakWhenOffTrack` (not `SpeakAt`). A run with zero such rows leaves this point **open**, not closed-negative: the table says a scheduled-only run doesn't satisfy it, and a silent real run says nothing either way. See the flag below the table for whether this point additionally needs §5's independent evidence. | `supervisor.jsonl` and the `metrics` field read directly off this run's own record; no test claims a real run's deviation count. |
| 4 | Correction arrives **mid-turn**, matching the measured wire shape | Requires **§5's independent evidence**, unconditionally — this row is not closable from `supervisor.jsonl` under any reading. | `experiments/trace_synthesis/sandbox_fold_check/` established the reference wire shape this run's independent capture is compared against. |
| 5 | Rollout completes, patch taken **against the pre-agent baseline**, grading runs | The rollout record has `patch_base_ref` set (ADR-0014); the grading entry's `metrics` has `unit_test.resolved` present (either `true` or `false` — presence, not value, closes this point). | `test_a_stub_agent_produces_an_empty_patch_on_a_dirty_image` (existing test, not re-run; this is a property of this run's own record). |
| 6 | Trace persisted, **interjection in it**, provenance complete | The converted trace contains the interjection text (if point 3 fired one) surviving conversion; `run_provenance()`'s stamped fields plus `extra["agent_model"]` are present in the record. | `test_an_interjection_survives_conversion_into_the_trace` (existing test, not re-run; a property of this run's own trace). |
| 7 | The **outcome word is correct** | `rollout_outcome` in the rollout record matches what actually happened, judged against the seven `RolloutOutcome` members read fresh from `src/swe_lab/rollout.py` at report time — not from this document's §7, which is a snapshot and may be stale by the time the run happens. | `tests/test_rollout.py`'s named tests pin the words apart; this row is a judgment call on one run's record against them. |

**Structural reason points 1 and 4 cannot close on `supervisor.jsonl` alone**,
per [the acceptance table's own rule](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end):
`supervisor.jsonl`, the trace and the record are all written by the pipeline
being checked, so "the run says it did" and "it did" are one statement made
twice. That rule, as currently written in the plan, names **points 1 and 4**.

**A discrepancy this document does not resolve on its own authority.**
Orchestra's most recent operational guidance (2026-09-01) instead scopes the
independent-evidence requirement to **points 3 and 4**. The two sources
disagree about whether point 1 or point 3 is the one that needs
non-self-collected evidence, and this document does not silently pick one —
that is exactly the kind of restated number the codebase's own review
discipline says to flag rather than smooth over. **Until reconciled, this run
treats the requirement conservatively as covering point 1 *and* point 3 *and*
point 4**: none of the three may be marked closed on `supervisor.jsonl` alone,
and each needs §5's independent evidence (or, for whichever of 1/3 turns out
not to need it once reconciled, a named argument for why self-evidence
suffices there — not a default). Point 2b is unaffected either way (§4).

## 5. Independent evidence for points 1, 3 and 4

**The candidate, and its status.** `swelab-inproxy-impl` is extracting the
actor's own native event stream — inside the container, at
`/agent-home/.claude/projects/-app/*.jsonl`
([`docs/conventions.md`](../../../docs/conventions.md), the `docker rm`
evidence-destruction hazard) — before the container is torn down. **Its
independence from our own collection path is not yet confirmed.** The
question it must answer, in the words it was given: is this file's content
itself a product of the same code being verified, or a record from outside
that code's reach? If the former, it is not independent evidence no matter
how it was extracted; extraction mechanics do not confer independence.

**This "unconfirmed" is left standing, not filled in.** Per the same
guidance: if the independence question is still open when this run happens,
the correct report for points 1, 3 and 4 is **not closed** — reached via our
own collection path, however extracted — not "closed using our own log
because nothing else was ready in time."

**If independence is confirmed before this run:** the candidate closes a
point when its content corroborates the corresponding row in §4's table —
e.g. for point 4, the native stream's own record of the correction message
arriving between two specific actor turns, checked against the same wire
shape (`sandbox_fold_check`'s reference) `supervisor.jsonl` claims.

**If a second, already-landed independent chain exists** (task 10's
in-sandbox proxy capture — see
[task 10](../../../docs/trace-synthesis/plans/README.md#task-10-run-the-capture-proxy-inside-the-sandbox))
and is available for this run, it may be used in place of, or alongside, the
native-stream candidate — it does not pass through `supervisor.jsonl` either.
Which of the two (or both) is actually used is decided at run time and
recorded in the report, not fixed here, because which one is ready first is
an operational fact this document cannot know in advance.

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

Read fresh from `src/swe_lab/rollout.py` at the time this file was written
(2026-09-01) — **re-read at report time rather than trusted from here**, since
this enum is exactly the kind of fact that must come from source, not from a
document about source:

`OOM_KILLED`, `SYSTEM_FAILED`, `TIMED_OUT`, `NO_PATCH`, `PATCH_PRODUCED`,
`UNCLASSIFIED`, `SUPERVISION_FAILED` — with
`_OURS = frozenset({OOM_KILLED, SYSTEM_FAILED, SUPERVISION_FAILED})`.

This run's `rollout_outcome`, whatever it is, is reported verbatim and
matched against point 7's closure criterion (§4). Two endings get special
handling in the report regardless of which one lands:

- **`SUPERVISION_FAILED`** — `SUPERVISION_METRIC` (`supervision.unhealthy`)
  fired, meaning the pump died or the channel closed uncleanly mid-run. If
  this happens, points 1, 3, 4 and 6 are judged **as of the point supervision
  stopped being trustworthy**, not waved through because the rollout as a
  whole "completed" — a run whose supervisor died halfway is not evidence for
  the points that needed the second half.
- **`NO_PATCH` or a resolved/unresolved `PATCH_PRODUCED`** — both are complete
  results per §1; neither alone closes or fails any of the seven points, which
  are evaluated independently of whether the patch resolved.

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
