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
resolved 12 / 40  (3 system failures excluded)
```

`12/40` alone makes the exclusion set an invisible knob — the same shape as a
default-off switch silently deciding a result.

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
