# ADR-0015 — Four words for how a rollout ends, and a denominator that defaults to "in"

## Status

Accepted.

## Date

2026-09-01

## Context

A rollout can end in ways that need different treatment, and until now they all
arrived downstream looking the same: **no usable patch**. Measured on
2026-09-01, an agent that died 1.4 s in still spent the **full 1800 s grading
budget**, and the run was recorded as a zero — the same shape a genuinely hard
task produces.

That is the accounting failure underneath it: **a broken system and a hard task
render identically.** Pooled into one bucket, our own breakage is counted as the
actor's difficulty, and every success rate computed from that bucket is wrong in
a direction nobody can see.

[ADR-0014](ADR-0014-the-pre-agent-baseline-is-the-default.md) removed the
*wall-clock* half of this by accident: with the baseline on, a crashed agent
produces an **empty** patch, and an empty patch is already refused by the edge
before a container is paid for (ADR-0007 §5). But it made the *classification*
half worse — a crashed harness and an agent that ran fine and produced nothing
now look **exactly** alike.

### The sister rule this belongs to

Four separate facts hit the same wall today:

| Fact | State before today |
| --- | --- |
| `patch_baseline` | existed, documented, **default off** |
| ADR-0001's amendment | described the failure almost verbatim, **no forcing function** |
| `total_cost_usd` | already parsed, **never persisted** |
| `sandbox.oom_kills` | recorded every run, **read by nothing** |

None of these was an oversight. In each, the knowledge was already in the
system and had **no consequence**. Beside the repo's *"an invariant needs a
test, or downgrade the claim"*:

> **A fact that is recorded but never consumed is not a safeguard; it is
> decoration. The test: name the branch it changes. If you cannot, it is not yet
> load-bearing.**

`sandbox.oom_kills` is the worked example — this ADR is what turns it from a
number into a gate.

## Decision

**1. `RolloutOutcome` — the stage's own word**, distinct from `AgentOutcome`
(what the agent's trace says about its loop):

| Word | Meaning | Ours? |
| --- | --- | --- |
| `OOM_KILLED` | killed for memory | **yes** |
| `SYSTEM_FAILED` | engine failed, or the loop ended in a way it did not choose (`AgentOutcome.retryable`) | **yes** |
| `TIMED_OUT` | the action hit its wall-clock budget | no — the actor spent it (ADR-0011) |
| `NO_PATCH` | terminated on its own terms, produced nothing | no |
| `PATCH_PRODUCED` | there is something to grade | no |
| `UNCLASSIFIED` | the evidence to attribute the ending was not there | **neither** (see the amendment below) |

Deliberately **not** named `AGENT_FAILED`: reading "the agent failed" for our
own breakage is the exact mistake the split exists to prevent.

**2. Classification is by cause, never by exit code.** An actor that exhausts
its own turn budget may exit non-zero, and that is still its result. The
question each word answers is: *did the actor terminate on its own terms?*
Killed from outside, or a precondition never met, is ours; running to its own
boundary and stopping is the actor's. This reuses `AgentOutcome.retryable`,
which is already that causal axis — *"everything the agent did not choose,
given the budget it was handed."*

Where causes co-occur, order decides: OOM first (it explains the broken loop it
causes), then wall-clock (a killed action leaves a truncated trace, and calling
that a crash would move a budget the actor spent onto our side), then the
agent's own ending, then the patch.

**3. One causal bit, two consumers.** `RolloutOutcome.ours` decides both
whether the attempt produced trustworthy outputs — `outputs_valid`, which is
what blocks the grading entry — and whether the run counts in a solve rate. One
bit, so the gate and the accounting cannot disagree about the same run.

**4. The denominator defaults to "in".** Only an ending *positively identified*
as ours leaves it. An ending nobody classified stays, which can only
**understate** a rate. The opposite default lets the excluded set grow
unwatched, and it grows in the direction that makes results look better.

**5. Every rate is reported with its excluded count. Always.**

```
resolved 12 / 40  (3 system failures excluded, 2 unclassified)
```

`12/40` alone makes the exclusion set an invisible knob — the same shape as a
default-off switch silently deciding a result. The second count is required by
the amendment below.

## Consequences

- A run whose ending is ours is refused at the rollout stage, so the workflow
  blocks the grading entry and no grading container is paid for.
- An ending the actor owns is **not** refused: a spent budget and a clean run
  that produced nothing are real results. The empty patch is still stopped by
  the edge one step later, at no container cost.
- `should_retry` is untouched. `run_task` calls it and `outputs_valid`
  independently, so making *validity* read the patch does not feed the patch
  into the **retry** decision — ADR-0011's "the retry predicate never reads the
  patch" holds, and was checked before this change rather than assumed.
- Four named tests pin the words apart, including one that pins the *default
  direction* so a later change cannot quietly turn "unclassified" into
  "excluded".
- **Point 5 is not enforced by a test**, because the aggregation it constrains
  does not exist yet. It is a contract on the bench that reports these rates:
  when that reporting is written, the function that returns a rate returns the
  excluded count with it. Until then this is an intention, not an invariant.

## Amendment — 2026-09-01: the unclassified count

Point 4 is unchanged: an ending nobody classified stays in the denominator, and
that direction can only understate a rate. The objection this amendment answers
is not about the direction; it is about **what the direction hides**.

The excluded set is watched by construction — an ending has to be *positively*
identified as ours to leave it, so it cannot grow without someone naming a
cause. The set kept **in** has no such property. A system failure we have not
named yet is classified as the actor's, and it is then invisible: it shows up
only as a lower rate, and the amount it lowers it by varies with infrastructure
quality rather than with the actor. Two batches can differ because of the
machine and read as differing because of the model.

That failure shape is not hypothetical here. Three defects found on 2026-09-01
— the rollout-record wipe, the 1800 s grading budget spent on a 1.4 s agent
death, and the missing `.envrc.local` — were each, before being named, exactly
"a frequently-occurring system failure nobody had positively identified".

**So: keep the default, and add the second number.**

1. **A sixth word, `UNCLASSIFIED`**, for an ending where the evidence needed to
   attribute it was not there: no harness outcome to read (a crash and a clean
   stop are indistinguishable from there), or no diff extraction at all (we
   never looked for work). Previously both fell into `NO_PATCH`, which asserts
   something stronger than we knew — that an extraction ran and came back
   empty. **Absence of evidence was being booked as evidence the actor produced
   nothing.**
2. **`UNCLASSIFIED` is not ours**, so nothing about grading or the denominator
   changes: it stays in, exactly as point 4 requires.
3. **`RolloutOutcome.unclassified` is reported with every rate**, beside the
   excluded count (point 5). This is the whole point of the amendment: it turns
   an unnamed crash mode from silence into a growing number.

Like point 5, the *reporting* half is a contract on a bench that does not exist
yet, not an invariant — what is enforced today is the word, its exclusion from
the ours-set, and its separation from `NO_PATCH`, each with a named test in
`tests/test_rollout.py`. Writing the enum member without a reporter would be
the decoration this ADR's own sister rule warns about, so the branch it changes
is named here: the reporter reads `unclassified`, and the acceptance for that
bench includes the second count.

### What this does not fix

A system failure that *does* produce a readable outcome and an empty patch —
a harness that reports `FINISHED` while silently broken — still classifies as
`NO_PATCH` and is still counted as the actor's. `UNCLASSIFIED` catches missing
evidence, not **wrong** evidence. That case needs a positive signal we do not
have, and inventing one from the patch would feed the patch back into an
attribution decision ADR-0011 keeps it out of.
