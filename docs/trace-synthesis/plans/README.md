# Oracle-guided trace synthesis — task index

Ordered task index + status for the trace-synthesis component (per the repo's
planning convention: [`spec.md`](../spec.md) = target design, `plans/` = one
deep design per task, indexed here). Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8
(break down if larger).

**Single source of truth for status:** this table is the *only* live status for
these tasks. Any `plans/task-NN-*.md` that appears later is a point-in-time
**design record** — don't read status from it.

**The ordering principle is the cheapest falsifier first.** The pipeline rests
on one assumption — *that a supervisor holding a good guidebook can steer a
blind agent to a correct solution at tool-call granularity* — and on one
unmeasured mechanism. The owner ruled on 2026-09-01 that the **assumption is
taken as given**, so it is task **02** that can still kill the idea: if no hook
channel yields a genuine user turn at a tool boundary, the design needs a
different medium before anything after it is worth building. Task 01 keeps its
place at the head for a different reason — it produces the first real artifacts
the later tasks are designed against. The hook-mechanics research that preceded
this index is not a task: its results are recorded in
[`spec.md` §10](../spec.md#10-what-is-measured-about-hooks).

| # | Task | Status |
|---|---|---|
| 01 | **One instance, end to end** — an automated walkthrough producing the pipeline's first real artifacts on one real instance | ⬜ |
| 02 | **Measure the injection shape** — can a hook put a *user-role* turn at a tool boundary, and does it survive conversion? | ⬜ |
| 03 | **Hint materialization + conversion guard** (pure, tested) | ⬜ |
| 04 | **Oracle analysis task + guidebook schema** | ⬜ |
| 05 | **Supervisor + hook wiring in the sandbox** | ⬜ |
| 06 | **Trace-quality scorer** (decide whether to build) | ⬜ |
| 07 | **The `oracle_guided_trace` workflow + integrity separation** | ⬜ |
| 08 | **Batch run: N instances, measure yield / cost / quality** | ⬜ |

---

## Task 01: One instance, end to end

**Description:** Walk the whole pipeline over a single instance and keep what it
produces: a real phase-A failure (on an instance whose gold self-test resolves)
frozen with its conversation, a guidebook written against that failure in the
[spec's shape](../spec.md#phase-b--the-oracle), and a steered re-run of a blind
actor with every injected hint logged. The run is **automated** — the existing
CLI plus scratch scripts, not a person typing hints — and uses no production
code: nothing here is wired into a workflow definition.

Per the owner's 2026-09-01 ruling the core assumption is **taken as given**
rather than tested, so this is not the design's falsifier. Its value is the
artifacts: tasks 04, 05 and 06 are designed against what a usable guidebook and
a real steered conversation actually look like. The design record is
[`task-01-one-instance-end-to-end.md`](task-01-one-instance-end-to-end.md).

- **Acceptance:** a written record of the walkthrough — which candidates
  survived gold validation, the harvested failure and where it is frozen, the
  guidebook, the hint texts and where they were injected, the resulting verdict,
  and a judgement on whether the hints stayed directional or drifted into
  specifics. A **steered run that still fails is a complete result**.
- **Verification:** an [experiment](../../experiments/playbook.md) `REPORT.md`
  — hypothesis, logged run, conclusion.
- **Dependencies:** none. **Scope:** S

## Task 02: Measure the injection shape

**Description:** Settle the spec's
[head open question](../spec.md#11-open-questions) by measurement. What shape
can a hook actually put into the conversation at a tool boundary, and which of
them is a genuine **user-role** turn? `PostToolUse` `decision: "block"` is
already measured and is *not* one (it lands as an `attachment`). The remaining
candidates — `updatedToolOutput`, `PostToolBatch`'s `decision` /
`additionalContext`, and a re-confirmation of `additionalContext` at this
version — get measured together with the two event-coverage questions
(`PostToolUseFailure` for the spinning-after-an-error case, `PostToolBatch` for
the parallel-batch case), because those change what the experiment is asking.

Each candidate is measured on **two** things: what the actor does with it, and
what our typed `Conversation` conversion does with it.

- **Acceptance:** a table of candidate → transcript shape → role as the model
  sees it → whether the converter preserves it; plus a recommendation for the
  head question and, if no candidate produces a user turn, the evidence the
  owner needs to rule on materialization.
- **Verification:** an experiment `REPORT.md` with the raw transcripts kept.
- **Dependencies:** none (runs in parallel with 01). **Scope:** S

## Task 03: Hint materialization + conversion guard

**Description:** The small, well-defined
[phase D](../spec.md#phase-d--collection) step: given the run's
`stream-json` and the host-side hint log, produce a `Conversation` in which
every hint the actor received is present as a visible turn. Pure host-side code
— no Docker, no model calls — which is where the correctness risk belongs.

The load-bearing half is the **guard**: a hint that the converter cannot
represent must fail the conversion loudly. Silently emitting a hint-less trace
is the one fatal failure mode in the spec.

- **Acceptance:** the guard is pinned by a named test (a run whose hint cannot
  be represented → conversion errors, no trace produced); round-trip tests over
  the typed model; `tool_use` ↔ `tool_result` pairing preserved.
- **Verification:** unit tests, no Docker; the full quality bar.
- **Dependencies:** 02 (its outcome decides what is being materialized).
  **Scope:** M

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
