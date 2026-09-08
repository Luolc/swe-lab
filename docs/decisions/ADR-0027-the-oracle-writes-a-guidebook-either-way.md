# ADR-0027: The Oracle writes a guidebook either way, and the schema only measures

## Status

Accepted. The owner ruled on 2026-09-08 (items 1–4 below are theirs); the
implementation, the trace-synthesis spec reconciliation and this record land
together.

**Partly supersedes [ADR-0021](ADR-0021-compact-guidebook-rubric-has-a-legacy-read-path.md)**:
its read-time validation half — "phase C accepts a legacy guidebook with no
rubric … a rubric that is present is always validated in full", and the
consequence "a malformed partial rubric fails both write-time and read-time
validation". Phase C no longer validates. What stands from ADR-0021 is the
write contract (new phase-B output carries both representations), the
rubric-over-tutorial delivery, and `guidebook_context_mode` as the record of
which representation a run's prompts consumed.

## Date

2026-09-08

## Context

Two real runs of `from_scratch_guided_trace` were attempted through
OpenRouter. Both reached the Oracle; neither produced a usable guidebook, in
two different ways.

**Run r0.** The blind rollout failed its graded tests, the Oracle wrote a
guidebook, and the schema check rejected it: `stage 4: missing the 'Actions'
field; stage 4: missing the 'Expected observations' field`. Stages 1, 2, 3 and
5 used the required labels; stage 4 — the one that changes code — wrote
`**Edits.**` and `**Tests.**` instead, which are labels the brief itself
offers. Because `outputs_valid` required a schema-valid guidebook, the
attempt was failed and the chain blocked. About 900 seconds of agent time was
discarded over label spelling, and the guidebook itself was fine to use.

**Run r1.** The blind rollout **passed** (`resolved: true, score: 1.0`), and
the Oracle wrote no guidebook at all. It was not confused: it reproduced the
grading procedure three times from the clean baseline, got 25/25 passing, read
the verdict, diffed the submitted patch against the reference and found only a
parameter rename — and then declined, saying that writing a guidebook about a
"wrong decision or failing fork" would mean fabricating a failure the brief
forbids, and asked the operator how to proceed. There is no operator in a
headless `claude -p` run. It exited 0, having produced nothing.

The premise it objected to was ours. The brief's first sentence hard-codes *"A
coding agent already attempted the task below and **failed** its graded
tests"*, while
[ADR-0023](ADR-0023-phase-a-returns-as-an-entry-of-the-from-scratch-chain.md)
§2 runs the Oracle **unconditionally**, on purpose: the 2×2 needs the cell
where the guidebook made a passing instance worse. The model obeyed the brief
and the pipeline scored that as a failure.

Both runs are the same shape of mistake in different places: a component was
given a rule it could not satisfy honestly, and the pipeline treated its
honest response as a fault.

## Decision

### 1. The brief branches on the actual verdict

The Oracle already receives the graded verdict (`unit_test.verdict.json` over
the chain's edge, `failed_verdict.json` from an `oracle_failures` row). The
prompt builder reads its `resolved` flag and writes one of two briefs:

- **the blind attempt failed** → today's brief: diagnose why, and write a
  guidebook that leads a blind agent to success;
- **the blind attempt passed** → a different brief: analyse *how* it
  succeeded, and specifically **which steps were arrived at by luck rather
  than by evidence** — where the agent guessed right. The guidebook makes
  those steps deliberate and reproducible, which is a *more complete*
  guidebook rather than a narrative of a failure that did not happen.

The flag is **read, never defaulted**. A verdict that is absent, unparseable
or carries no boolean `resolved` refuses the run in the workspace, before the
agent is launched — the same class of refusal as an input nobody staged. A
default would put a guess in the brief's first sentence and state it as fact,
which is exactly what r1 was.

### 2. The Oracle always produces a guidebook — there is no refusal path

A refusal path was proposed during implementation and rejected by the owner: a
component that can decline turns "the premise looks off" into a stop switch
for the whole chain. Once success is a legitimate branch, the Oracle has no
reason to stop and ask, and neither brief offers it one.

### 3. The schema restricts nothing; it is a metric

`validate_guidebook` measures what the Oracle wrote. Validity and the list of
problems are recorded — `guidebook.valid` as a run metric, `guidebook_problems`
on the attempt record — and **nothing gates on them**. There were two gates,
and both go:

- **phase B**, `OracleAnalysisTask.outputs_valid`, which failed an attempt
  whose guidebook missed a label. Removed; the baseline stands, so a run that
  produced no guidebook **at all** still fails as a missing declared output.
- **phase C**, `require_valid_guidebook` in the guided harness, which refused
  to start the actor. Removed with the function itself; nothing called it
  afterwards. What is left there is a **presence** check: a guided run whose
  guidebook never arrived is an unguided run wearing the guided entry key, and
  the 2×2 would read it as guided.

An imperfect guidebook goes downstream and gets used. That is the point: the
supervisor's own prompts already tolerate whichever representation they find,
and a label a regex did not recognize is not evidence that the prose is
useless.

### 4. Retry only on genuinely exceptional errors

`should_retry` loses its schema clause. A guidebook that fails a label check
is not bad luck — it is what the model produced, and re-running buys another
sample of the same writer at the price of a paid agent run. What remains is
the baseline (a run that ended anything but `SUCCESS`, or produced no
guidebook at all) plus the harness's retryable endings: things that happened
*to* the agent — a container that would not start, a network failure, a crash.

The entry's retry budget stays **0**, which means the policy above is dormant
on every shipped path: `run_task` loops `range(retries + 1)`, so an Oracle
attempt is never retried today, whatever happened to it. That is a decision
about spending, not a claim that those endings are unworthy — they are the
ones that are. Raising the default is a separate call with its own money
attached, and an operator who wants it asks per run
(`--oracle_analysis.retries=N` reaches the entry field). The method stays
because that path is real, and because whoever pays should get this ruling's
behaviour rather than the base class's.

## Alternatives Considered

### Keep the schema as a gate and teach the prompt more label discipline

Rejected. It re-runs a paid agent to fix a regex mismatch, and it was tried
implicitly already — the brief names the five labels *and* offers `Edits` /
`Tests`, and r0's model still substituted them. This ADR deliberately does
**not** also add a synonym list or withdraw the `Edits` / `Tests` offer: the
schema no longer gating already unblocks r0, and changing two things at once
would leave neither attributable. Whether the prompt should stop offering
those labels is a separate question with its own evidence.

### Let the Oracle decline when the premise does not fit

Rejected by the owner. It is a stop switch for the chain in the hands of the
component least able to see the chain, and it is unnecessary once both
verdicts have a brief.

### Skip the Oracle when the blind attempt passed

Rejected: it is ADR-0023 §2 again. The regressed cell — solved blind, unsolved
under the guidebook — is a fact about the guidebook this chain exists to make
observable, and a chain that stops early cannot observe it.

### Raise the retry budget instead of removing the schema clause

Rejected, and explicitly out of scope. It pays for re-rolls of the same writer
and hides the design question behind a number. Note what this does **not**
say: the exceptional endings `should_retry` names are real and would warrant
another attempt if anyone were paying for one. The budget is 0 because nobody
has decided to, not because there is nothing there to rescue — and the two
sentences are kept apart deliberately, since collapsing them is how a
docstring starts describing behaviour its only entry cannot reach.

## Consequences

- Phase B has two default briefs, both pinned by digest. An edit to the branch
  nobody has run yet cannot slip through unnoticed.
- A guidebook the schema faults still becomes phase C's input, and the run
  record says what it faulted on. "The check did not run" and "the check ran
  and found nothing" stay distinguishable, because the metric is written on
  every attempt that produced a guidebook.
- `require_valid_guidebook` and `GuidebookRejectedError` are gone;
  `GuidebookMissingError` names what phase C still refuses, which is absence.
- `validate_guidebook` loses its `require_rubric` flag. Phase B was its only
  caller with the flag set, and phase C no longer validates, so a lenient mode
  would have had no caller. ADR-0021's legacy read path is unaffected — it
  lives in `guidebook_context_mode` / `extract_guidebook_rubric`, which are
  about which representation a prompt consumes, not about validity.
- No Oracle attempt is retried on any shipped path, and two tests say so
  together: `test_the_shipped_oracle_entries_carry_no_retry_budget` pins the
  budget at 0 for both entries that carry the task, and
  `test_a_caller_who_pays_for_a_retry_gets_this_policy` shows the policy is
  reachable the moment someone raises it. Neither alone distinguishes "the
  policy is dormant" from "the policy is dead code".
- An Oracle run over a record with no readable verdict now fails at input
  build rather than producing a brief. The `oracle_failures` contract already
  requires the verdict file, so this reaches only malformed rows.
- What no test can reach: whether the success brief actually elicits a useful
  guidebook. That needs a paid run, and it is the first thing to look at when
  one is made.
