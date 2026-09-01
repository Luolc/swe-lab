# Oracle-guided trace synthesis — task index

Ordered task index + status for the trace-synthesis component (per the repo's
planning convention: [`spec.md`](../spec.md) = target design, `plans/` = one
deep design per task, indexed here). Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8
(break down if larger).

**Single source of truth for status:** this table is the *only* live status for
these tasks. Any `plans/task-NN-*.md` that appears later is a point-in-time
**design record** — don't read status from it.

**The ordering principle is the cheapest falsifier first.** The pipeline rests
on one untested assumption — *that a supervisor holding a good guidebook can
steer a blind agent to a correct solution at tool-call granularity* — and on
one unmeasured mechanism. Tasks 01 and 02 exist to kill the idea early if it is
going to die; nothing after them is worth building until both land. The
hook-mechanics research that preceded this index is not a task: its results are
recorded in [`spec.md` §10](../spec.md#10-what-is-measured-about-hooks).

| # | Task | Status |
|---|---|---|
| 01 | **One hand-made instance, end to end, by hand** — the cheapest test of the core assumption | ⬜ |
| 02 | **Measure the injection shape** — can a hook put a *visibly external* hint at a tool boundary, and does it survive conversion? | ✅ |
| 03 | **Hint log + conversion guard** (pure, tested) | ⬜ |
| 04 | **Oracle analysis task + guidebook schema** | ⬜ |
| 05 | **Supervisor + hook wiring in the sandbox** | ⬜ |
| 06 | **Trace-quality scorer** (decide whether to build) | ⬜ |
| 07 | **The `oracle_guided_trace` workflow + integrity separation** | ⬜ |
| 08 | **Batch run: N instances, measure yield / cost / quality** | ⬜ |

---

## Task 01: One hand-made instance, end to end, by hand

**Description:** On a single known instance (a real phase-A failure whose gold
self-test resolves), hand-write a guidebook in the
[spec's shape](../spec.md#phase-b--the-oracle) and then hand-steer one rollout:
a human reads each tool result, decides on-track / off-track, and types a
directional hint when it is off. **No code, no hooks, no supervisor model.**

This is the cheapest possible test of the assumption everything else rests on.
If a person holding a perfect guidebook cannot steer a blind actor from failure
to a passing verdict with directional hints alone, the design is dead and no
amount of machinery saves it.

- **Acceptance:** a written record of the attempt — the guidebook, the hint
  texts and where they were injected, the resulting verdict, and a judgement on
  whether the hints stayed directional or drifted into specifics. A **negative**
  result is a complete result.
- **Verification:** an [experiment](../../experiments/playbook.md) `REPORT.md`
  — hypothesis, logged run, conclusion.
- **Dependencies:** none. **Scope:** S

## Task 02: Measure the injection shape

**Description:** Settle the spec's
[head open question](../spec.md#11-open-questions) by measurement. What shape
can a hook actually put into the conversation at a tool boundary, and which
shapes pass the three tests the question now asks — the actor sees it, it is
**marked as an external injection**, and our conversion preserves it? (The
wire-level `role` field is not the criterion; owner, 2026-09-01.) `PostToolUse`
`decision: "block"` is already measured and fails the third (it lands as an
`attachment`). The candidates — `updatedToolOutput` carrying a tagged suffix
appended to the tool's real output, `PostToolBatch`'s `decision` /
`additionalContext`, and a re-confirmation of `additionalContext` at this
version — get measured together with the two event-coverage questions
(`PostToolUseFailure` for the spinning-after-an-error case, `PostToolBatch` for
the parallel-batch case), because those change what the experiment is asking.

Each candidate is measured on **two** things: what the actor does with it, and
what our typed `Conversation` conversion does with it.

- **Acceptance:** a table of candidate → transcript shape → what the model sees
  it as → whether the converter preserves it → any observation on whether the
  actor complies; plus a recommendation for the head question and, if no
  candidate survives with its marker intact, the evidence the owner needs to
  rule on materialization.
- **Verification:** an experiment `REPORT.md` with the raw transcripts kept.
- **Dependencies:** none (runs in parallel with 01). **Scope:** S
- **Outcome:** [`experiments/trace_synthesis/injection_shape/REPORT.md`](../../../experiments/trace_synthesis/injection_shape/REPORT.md).
  `PostToolUse` `updatedToolOutput` with a tagged suffix appended to the tool's
  real output is the recommendation — the only candidate kept by **both**
  converters. Survival turned out to be a property of the converter rather than
  the channel, materialization is not needed, and two defects surfaced that the
  task did not go looking for (`proxy_log_to_conversation` keeps only the last
  thread; routing the actor through a proxy changes whether it follows a hint).

## Task 03: Hint log + conversion guard

**Description:** What is left of the [phase D](../spec.md#phase-d--collection)
step once [task 02](#task-02-measure-the-injection-shape) removed the
materialization half: a host-side log of every hint the Supervisor injected, and
a guard that cross-checks it against the converted `Conversation`. Pure
host-side code — no Docker, no model calls — which is where the correctness
risk belongs.

The load-bearing half is the **guard**: a hint that is not present in the
converted trace must fail the conversion loudly. Silently emitting a hint-less
trace is the one fatal failure mode in the spec, and task 02 found two live
routes to it — `proxy_log_to_conversation` keeps only the last proxy record's
thread, so a hint delivered inside a subagent's conversation disappears; and a
hint injected after the actor's last API call never reaches the model at all,
which is legitimate but must be recorded rather than assumed.

- **Acceptance:** the guard is pinned by a named test (a run whose hint is
  absent from the converted trace → conversion errors, no trace produced);
  round-trip tests over the typed model; `tool_use` ↔ `tool_result` pairing
  preserved.
- **Verification:** unit tests, no Docker; the full quality bar.
- **Dependencies:** 02 (its outcome decided what is left to build).
  **Scope:** S–M

## Task 04: Oracle analysis task + guidebook schema

**Description:** Phase B as a `Task`: a sandbox with the golden patch, the
golden tests and the failed conversation mounted, the **git-history purge
off**, producing a validated `guidebook.md`. The schema enforces the
`justification` field per stage — the field that makes an honest hint possible
at all.

Phase B is independently useful: a guidebook is a readable artifact even
without phase C.

- **Acceptance:** schema validation rejects a guidebook with a stage missing
  its `justification`; the task declares `guidebook.md` as an output and the
  purge-off configuration is explicit rather than incidental.
- **Verification:** unit tests for the schema; one live run producing a
  guidebook a human judges usable.
- **Dependencies:** 01 (which shapes what a usable guidebook looks like).
  **Scope:** M

## Task 05: Supervisor + hook wiring in the sandbox

**Description:** The real machinery. Hook settings injected per run
(`--settings` + an isolated `CLAUDE_CONFIG_DIR`), a host-side Supervisor called
over the API with its own credential (the hook subprocess inherits none), the
belief state kept outside the sandbox, and an intervention record written per
decision. Includes the explicit timeout policy: the default is fail-open, so a
dropped decision must be **recorded**, never silently skipped.

- **Acceptance:** the guidebook and the belief state are provably absent from
  the actor's context and mounts (named tests, per the spec's
  [invariants](../spec.md#12-invariants-intended-none-enforced-today)); a
  dropped or timed-out supervisor decision appears in the run record; the
  Supervisor's hook response can never carry `updatedInput`, a deny decision or
  `additionalContext` — the three channels
  [§5](../spec.md#5-the-mechanism-decisions) bans, `additionalContext` included
  because it is delivered as a system reminder.
- **Verification:** unit tests over the hook payload handling; one live guided
  rollout end to end.
- **Dependencies:** 02, 04. **Scope:** M–L

## Task 06: Trace-quality scorer (decide whether to build)

**Description:** A blind judge — a model seeing only the visible prefix — is no
longer needed as a *leak detector*, because keeping the hints satisfies that by
construction. The open question is whether it earns its keep as a plain
trace-**quality** score: does this trace teach anything, or did the hint do the
work? **This task starts with the decision, and may end there.**

- **Acceptance:** either a written "not worth it" with the reasoning, or a
  scorer that produces a per-trace number plus evidence it correlates with a
  human reader's judgement on a sample.
- **Verification:** if built, a scored sample with human agreement reported.
- **Dependencies:** 01 (needs real traces to judge). **Scope:** M

## Task 07: The `oracle_guided_trace` workflow + integrity separation

**Description:** Wire A→B→C→D as a registered workflow on the existing
[workflow layer](../../decisions/ADR-0007-task-and-workflow-layer.md), with the
edges resolved from the store. The integrity half is not optional: every phase
B / C record carries the oracle-guided **policy stamp**
([ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §5), so these runs
can never be pooled with benchmark numbers, and the
[result verifier](../../horizontal/plans/task-26-result-verifier.md) is left
free to flag them as contaminated — which is correct behaviour, not a bug.

- **Acceptance:** a named test asserting the stamp is on the record and that
  aggregation across differing stamps still errors; the verifier's contaminated
  flag on an oracle-guided run is asserted as *expected*, not suppressed.
- **Verification:** unit tests plus one end-to-end workflow run.
- **Dependencies:** 03, 04, 05. **Scope:** M

## Task 08: Batch run — N instances, measure yield / cost / quality

**Description:** Run the pipeline over a batch and produce the numbers that
decide whether it scales: kept-trace yield, cost per kept trace (a baseline
rollout + eval, an oracle analysis, a guided rollout + eval, and one supervisor
call per tool call — with the guided rollout still able to fail and cost all of
it for nothing), and a quality read on the traces. Also the first real data on
the [selection question](../spec.md#11-open-questions): measured `pass@10` band
versus the cheap "failed once, gold self-test passes" proxy.

- **Acceptance:** a report with yield, cost per kept trace, and a comparison
  against rejection sampling on the same instances.
- **Verification:** an experiment `REPORT.md`.
- **Dependencies:** 07. **Scope:** L
