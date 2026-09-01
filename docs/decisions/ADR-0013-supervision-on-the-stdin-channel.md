# ADR-0013: Supervision is delivered on the harness's stdin channel, not from a hook

## Status

Accepted. Amends
[`docs/trace-synthesis/spec.md`](../trace-synthesis/spec.md) §5 — the *steer
from a Claude Code hook* attribution row and the *not a system-reminder* row —
together with the delivery mechanism as described in §3 (phase C) and §6, and
the two §12 invariants whose enforcement point was a hook response. The **hook
measurements** in §10 are untouched: they are measurements, not decisions, and
they remain true of hooks.

**Read the Context before citing this ADR.** The decision it records moved
across a pre-registered gate that **did not pass**. That is not a footnote; it
is the fact most likely to be lost.

## Date

2026-09-01

## Context

### What the spec said, and what it was waiting for

[`spec.md` §5](../trace-synthesis/spec.md#5-the-mechanism-decisions) has said
since 2026-08-31 that steering happens **from a Claude Code hook** — "not the
proxy, not our own agent loop" — because folding steering into the capture proxy
couples two complex things, and writing our own agent loop abandons the point of
hugging the harness we want traces of. **Those two negations are not in
question here and are carried forward unchanged.** What moves is the positive
half: *which* of the harness's own surfaces delivers the correction.

A structured debate on 2026-09-01 ruled **"A′ now"** — deliver the correction on
the stdin of a live `claude -p --input-format stream-json` process — **explicitly
gated on a registered compliance test that had not been run**
([DEBATE-VERDICT](../../experiments/trace_synthesis/process_supervision/DEBATE-VERDICT.md)).
The verdict named the ADR this one would have to be: A′ "widens §5's *hook*
letter to *the harness's own channels*".

### The gate, and what it was designated to adjudicate

The gate was pre-registered before any run
([PREREGISTRATION](../../experiments/trace_synthesis/mid_turn_compliance/PREREGISTRATION.md)):
sparse delivery, user-like wording, the tag in the body, N ≥ 10 interventions,
and the criterion fixed in code before the runs. **It was designated to decide
adoption**: whether an actor, corrected mid-turn, *takes the specific next
action the correction names*. Its decision rule required **both** a level and a
separation — `MID ≥ 0.70` **and** `MID − NEG ≥ +0.40`.

### The gate returned `BELOW_BAR`

60 graded runs, Claude Code 2.1.257, `claude-sonnet-5`, zero timeouts, zero
re-runs
([REPORT](../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)):

| | `MID` (corrected) | `NEG` (uncorrected) | `POS` (positive control) |
| --- | --- | --- | --- |
| rate | **0.529** (9/17) | 0.125 (2/16) | 1.000 (20/20) |

`MID − NEG = +0.404`. **Rule 3 needed both halves and got one**: the separation
cleared +0.40, the level missed 0.70. The verdict is `BELOW_BAR`, which the
protocol declares **terminal** — no added arms, no added runs, no adjusted
thresholds. The paired comparison that looked like an effect **was withdrawn by
the report itself** (only 3 of 16 pairs are interpretable), so no behavioural
claim survives from that batch.

**This ADR does not claim the gate passed, and nothing below should be read as
softening that.** It did not pass.

### What the same batch established mechanically, and is not in dispute

- **0 `NOT_DELIVERED` across 37 delivered interventions**, with a delivery lag of
  exactly one agent-loop record every time.
- **The mid-turn fold is the production shape.** A correction typed into the
  real interactive TUI and the same correction written on `-p` stdin produce the
  same wire: same message count (7 vs 7), same role sequence, same
  `<system-reminder>` count (4 vs 4), and a **byte-identical** injected block
  (`len 440`, `sha256 3ba88726…fb90c8`) — N=1 per arm, hash-pinned by
  `tests/test_streamjson_input_evidence.py`
  ([streamjson_input §14](../../experiments/trace_synthesis/streamjson_input/REPORT.md)).
- **Speaking costs zero extra actor API requests** mid-turn (N=1 per arm, 3
  proxied headless arms, one task, one model).
- **Provenance held**: 0 of 37 interventions were challenged as unattributed.
- **8 of 8 non-compliances arrived too late** — the failures were *timing*, not
  refusal.

### The owner's ruling — a judgement, not a measurement

The owner ruled on 2026-09-01 that **the gate measured the wrong quantity**: it
asks *did the actor take the specific action we named*, where the question worth
deciding is *does supervision raise the probability the actor gets it right* — a
question about distributions over outcomes. The ruling notes that the choice of
a machine-checkable criterion **reached back and reshaped the intervention**: a
predicate a machine can score requires a correction naming one concrete
checkable action, and a correction that specific is already close to handing over
the answer. So the instrument selected a supervisor more specific than the one we
would ship.

> **This paragraph is an owner judgement about which question to ask. It is not
> a measurement, it was not produced by the experiment, and it does not convert
> `BELOW_BAR` into a pass.** The verdict stands unwithdrawn, no number changed,
> nothing re-run. A reader who takes "the gate measured the wrong thing" as an
> empirical finding has misread this ADR.

## Decision

**Supervision is delivered on the harness's own input channel — a user message
written to the stdin of the live `claude -p --input-format stream-json`
process — and not from a Claude Code hook.**

The decision rests on two things and it is worth being explicit about which is
which:

1. **Evidence** — the mechanical results above. The transport is settled: it
   delivers, it costs nothing extra, and what it produces is byte-identical to
   what an ordinary interactive user produces.
2. **Judgement** — the owner's ruling that adoption should not be decided by a
   named-action compliance rate. Whether supervision helps is moved to a
   measurement of **resolved rate over paired arms**, which is a different
   experiment on a different question.

What the hook path loses, it loses on evidence rather than on preference:
**[M]** `updatedToolOutput` cannot carry a hint on a tool whose response has no
free-text field — three hints judged at `Edit` boundaries, all three
unappendable, zero reaching the actor — so the channel is **blind at exactly the
commit points a supervisor most wants to speak at**
([`spec.md` §10](../trace-synthesis/spec.md#10-what-is-measured-about-hooks)).

## Alternatives Considered

- **Keep the hook path and re-run the gate against it.** Rejected: the blindness
  at `Edit` boundaries is structural, and re-running a gate whose question the
  owner has ruled wrong buys a second answer to the same wrong question.
- **B — hold-then-forward resampling in the proxy.** Not adopted, and the reason
  is recorded rather than assumed: its gate (a reject-then-accept witness)
  terminated `material-retired` at attempt 0, and its own judge was measured to
  be a stochastic function with sampling never pinned
  ([FLIP-RATE-REPORT](../../experiments/trace_synthesis/process_supervision/reject_then_accept_witness/FLIP-RATE-REPORT.md)).
  B is not refuted here; it is un-evidenced, and it reverses §5's *not the
  proxy* row, which this ADR keeps.
- **Wait for a redesigned compliance gate before moving the attribution.**
  Rejected on cost, not on principle: the engineering that the attribution
  authorizes — the supervisor component — is the same component the redesigned
  measurement needs in order to run at all.

## Consequences

- **[`spec.md` §5](../trace-synthesis/spec.md#5-the-mechanism-decisions)**: the
  attribution row now names the stdin channel; *not the proxy* and *not our own
  agent loop* are unchanged. The *not a system-reminder* row is rewritten
  against criterion **(b)**: what made a hook's `additionalContext` unacceptable
  was that it is a supervision-only artifact, and the mid-turn message is the
  opposite — the shape an ordinary user produces at inference time.
- **§3 (phase C) and §6** describe the delivery mechanism and are rewritten to
  the channel; the hint is a message the actor received, not a tagged segment
  appended to a tool result.
- **§12**: the two invariants keyed to a hook response — *a hint never replaces
  a tool's output* and *no banned channel is reachable in a hook response* — move
  their enforcement point to the supervisor's emitter. The underlying bans
  (never rewrite, never deny) become **structural**: this channel has no field
  that could do either.
- **§10 is untouched.** Every hook fact there was measured and none is refuted.
- **[Task 05](../trace-synthesis/plans/README.md)** is re-scoped from *hook
  wiring* to the supervisor component, and **carries this ADR's refutation
  condition as an acceptance condition** — the in-sandbox fold check described
  under *What would overturn this*. **[Task 16](../trace-synthesis/plans/task-16-live-correction-channel-in-the-harness.md)
  remains design-only and unauthorized** — this ADR moves attribution, not the
  harness's stdin plumbing.
- **Nothing here authorizes a production run that injects.** §5's standing note
  survives: the production default is an uninterfered rollout with the guidebook
  used post-hoc.

## What would overturn this

Two conditions, and they are different in kind — a later ADR should say which
one it is acting on.

1. **Refutation — the artifact is not the one we ship.** Every measurement of
   this channel is host-side against Claude Code 2.1.257; the sandbox runs the
   pinned 2.1.212 with its own `CLAUDE_CONFIG_DIR`. If the fold differs there —
   in particular if local tool calls begin interrupting the way the 2.1.246
   changelog describes for MCP — then the byte-identity result is about the
   wrong artifact and this decision is **wrong**, not merely obsolete.

   **This condition is scheduled, and that is deliberate.** It is an
   **acceptance condition of [task 05](../trace-synthesis/plans/README.md)**,
   not a future check filed against an unowned task: the supervisor must
   deliver one intervention *inside the sandbox* and the fold's measured shape
   (block length and `sha256`) must match the host measurement. A mismatch
   means **task 05 is not complete** and opens this ADR's refutation path. A
   falsification condition nobody is scheduled to evaluate is not a
   falsification condition, and this one sits on the critical path anyway — the
   rollouts that will use this channel run in containers, and the byte-identity
   result was measured on a host.
2. **Retirement — the decision loses its purpose.** If the paired-arm
   measurement shows supervision does not raise the resolved rate, there is
   nothing to deliver and the attribution question is moot. This ADR would then
   be **superseded as pointless rather than as mistaken**, and the superseding
   record should say so, because the two failures teach different things: the
   first says the channel is not what we measured, the second says supervision
   is not worth delivering by any channel.
