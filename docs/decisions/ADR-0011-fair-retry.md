# ADR-0011: Retry only what is not the agent's own doing

## Status

Accepted

## Date

2026-08-07

## Context

[ADR-0008](ADR-0008-retry-moves-to-the-task.md) settled *where* a retry happens
(the task, one fresh sandbox per attempt) and [ADR-0005](ADR-0005-flaky-eval-retry.md)
settled *why* a failed **evaluation** is re-run. Neither settled *which
failures* are re-run at all. The predicate that fell out was the base default
— retry exactly when `outputs_valid` is false — and it turned out to be wrong
in both directions:

- **Too wide.** `outputs_valid` fails on `RunStatus.TIMEOUT`, so a killed
  attempt was retried. But wall-clock is a **budget**: an agent that thrashed,
  re-ran the suite ten times, or wandered for thirty minutes hit it, and that
  is a *result*. Retrying handed it a second budget. An eval that outran its
  budget is the same story one level down — usually the patch under test
  looping or deadlocking — and retrying paid the full timeout again to reach
  the same place.
- **Too narrow.** An agent that died on an API error, crashed out of its turn
  loop, or had its trace cut mid-flight still exits with `RunStatus.SUCCESS`
  and a present (if useless) `conversation.json`, so `outputs_valid` passed it
  and **nothing was retried**. Those are our failures, and not re-running them
  penalizes the agent for our problem.

So the one case that was retried was the one that should not have been, and
the cases that should have been were not.

The failure taxonomy needed to tell these apart already existed upstream and
was already mapped: [the 2026-07-29 rollout-outcome-states
review](../reviews/2026-07-29-rollout-outcome-states.md) read it out of the
Claude Code source (`SDKResultSuccessSchema` / `SDKResultErrorSchema` in
`entrypoints/sdk/coreSchemas.ts`, the synthesized result in `cli/print.ts`),
found that we collapsed nine distinct endings into one `complete: bool`, and
proposed both an `AgentOutcome` enum (§5) and the retry policy below (§6). Its
finding F3 (our own timeout was invisible) has since been fixed; F1 and the
policy had not been implemented. This ADR is that implementation, and re-reads
the vendored source to confirm the taxonomy before building on it.

Getting this wrong is not a tidiness problem. **Retry is the one mechanism that
can silently inflate a published number**, and it does so in the direction
nobody audits: a re-rolled agent looks like a better agent.

## Decision

**Retry only a failure that is not attributable to the agent.**

If the agent caused the failure *given a fixed budget*, retrying hands it
attempts a better-behaved agent would not have needed and the score inflates.
If our infrastructure caused it, not retrying penalizes the agent for our
problem and the score deflates, non-deterministically. The axis is **causal
attribution, never severity** — a crash is retryable because it is *ours*, not
because it is bad.

### 1. `AgentOutcome` replaces the completion bit

`Harness.outcome(sb) -> AgentOutcome` is the new contract method;
`Harness.completed` becomes `@final` and derives (`outcome is FINISHED`), so
the bit and the outcome cannot drift. `HarnessOutcomeObserver` records the
outcome and keeps `complete` / the `agent_complete` metric as its coarse view.

| Member | Retryable | Why |
|---|---|---|
| `NO_OUTPUT` | ✅ | nothing was ever written — SIGKILL, OOM |
| `TRUNCATED` | ✅ | partial trace, no terminal result: killed mid-flight |
| `FINISHED_WITH_API_ERROR` | ✅ | the loop ended, but its last turn was an API error |
| `EXECUTION_ERROR` | ✅ | an exception escaped the turn loop |
| `FINISHED` | ❌ | nothing to absorb |
| `MAX_TURNS` | ❌ | it spent the turn budget it was given |
| `MAX_BUDGET` | ❌ | it spent the spend budget it was given |
| `MAX_OUTPUT_RETRIES` | ❌ | its own output was invalid |

Two states of the review's §5 list deliberately have **no member**, because
they already have a home and one fact gets one home: *not started* is
`RunStatus.SETUP_ERROR` and *timed out* is `RunStatus.TIMEOUT` (the task
promotes a killed action to it). Both are the engine's axis; a harness reading
its own trace can see neither.

### 2. The timeout veto is the runner's gate, not a second hook

`Task.retry_on_timeout: bool = False`, and the veto lives in **one function**
in the runner, which the attempt loop goes through:

```python
def retry_permitted(task, result) -> bool:
  if result.run.status is RunStatus.TIMEOUT:
    return task.retry_on_timeout
  return task.should_retry(result)
```

It has to be structural rather than a convention, because a task's own
reasoning reinstates the retry *by accident*: a killed run has no terminal
trace event and no resolved verdict, so both shipped `should_retry` overrides
say "retry" precisely **because** of the kill.

**A function, not a second method on `Task`.** The obvious alternative — make
`should_retry` a `@final` template that applies the veto and then delegates to
a new subclass-overridable hook — was built first and rejected: it leaves two
near-synonymous retry methods on one class, and a downstream author reads both
as "the retry hook" and overrides the wrong one. `@final` turns that into a
type error rather than a silent bug, but a type error a reader has to hit is
not a design. As a function there is nothing to confuse — **`Task` has exactly
one overridable retry method, `should_retry`, under its original name and
meaning** — and the veto sits with the thing that actually spends the budget
(the attempt loop), where it also cannot be weakened from a subclass.

A consumer whose timeouts are known to be the machine's rather than the work's
opts in per entry (`--rollout.retry_on_timeout=true`), and discloses it with
the number.

### 3. The rollout predicate reads two axes, and never the answer

`CodingAgentTask.should_retry`: an integrity failure never
([ADR-0010](ADR-0010-benchmark-integrity.md) — deterministic, so a repeat buys
the same verdict a container later); else the engine's verdict (setup error,
run error, a missing declared output — all ours); else the agent's own ending
per the table above.

**The predicate never reads the patch or the grade.** Retrying an empty patch
or a failing test re-rolls bad luck until it lands, which inflates pass@1
directly. It is a function of `AgentOutcome` + `RunStatus` alone, and that is
pinned by a named test rather than by a comment.

### 4. Grading is the one place a grade *may* be read — and it is not the same thing

`UnitTestTask.should_retry` keeps ADR-0005's flake absorption: an unresolved
verdict earns another attempt. That looks like the thing §3 forbids and is its
opposite. Grading re-runs a **fixed** candidate against a nondeterministic
suite, so a repeat averages out the *harness's* noise; a rollout retry re-rolls
the *agent*. ADR-0005's empirical case (the corpus's own gold patch fails up to
16% of the time on some instances) is unchanged and not re-litigated here.

### 5. The outcome lands on the record

`Task.record_extra(result)` is a new hook whose return is merged into the
attempt's shard `extra`; `CodingAgentTask` returns `agent_outcome`. The retry
decision is a function of that value, so it has to be auditable from the
manifest afterwards — otherwise telling "retried three times on API errors"
from "solved nothing" means re-parsing every attempt's trace.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep `complete: bool`, special-case the timeout | Fixes the too-wide half and leaves the too-narrow half: an API error still reads exactly like a clean finish, so it still would not be retried. The one bit cannot carry the decision. |
| Retry on timeout by default, opt *out* | Inverts who pays for the mistake. A default that inflates is discovered by nobody; a default that under-retries shows up as an infra-failure rate someone has to explain. |
| Leave `should_retry` as the only mechanism and document the veto | The two shipped overrides would each have reinstated the retry *by accident*, for the reason in §2. A rule that survives only by everyone remembering it is not a rule. |
| A `@final` `should_retry` template delegating to a second, subclass-overridable hook | Built, then rejected: two near-synonymous retry methods on one class is a trap, even when the type checker catches the wrong override. See §2. |
| Classify `EXECUTION_ERROR` via the result's `errors[]` before retrying | Worth doing (a usage-limit exhaustion burns the budget on retries that cannot succeed) but not needed for attribution — every flavour of it is still ours. Recorded under Consequences as the known gap. |
| Give `PROXY` capture the same taxonomy | It cannot evidence it: the proxy log records API traffic, and `max_turns` ends on a perfectly complete final response. It reports the coarse pair it can defend. |

## Consequences

**Good**

- **The metric stops being inflatable by the retry path.** The predicate cannot
  see the patch or the grade, and every ending an agent can *choose* is
  non-retryable.
- **A genuine infrastructure failure is now actually retried.** It was not
  before, in exactly the cases (API error, execution error) most likely to hit
  a long sweep.
- **Nine endings are distinguishable in the record**, so an eval report can
  break out `MAX_TURNS` and timeouts from genuine failures — which the review
  named as a requirement for publishing a fair number.

**Bad, and accepted knowingly**

- **A usage-limit exhaustion burns the retry budget.** It arrives as
  `EXECUTION_ERROR`, which is retryable, and every attempt fails the same way
  until the window refreshes. Bounded (the budget is small; the shipped rollout
  entry has none) and visible in the record. Classifying it needs the result's
  `errors[]` text — the machinery exists in
  `harnesses/claude_code/errors.py::classify_error_text` — and is left as
  follow-up rather than guessed at.
- **`PROXY` capture cannot see budget endings**, so a `max_turns` run there
  reads `FINISHED`. The ambiguity is resolved towards *not* retrying, which
  costs a missed retry rather than an inflated score. A composition that needs
  the distinction captures `STREAM`.
- **A rollout that exhausts its budget on infrastructure failures still marks
  `SUCCEEDED`.** That is `outputs_valid`'s call and it is unchanged
  (retry-desire is not failure, ADR-0007 §6); the evidence is `agent_outcome`
  on every attempt's shard, which is why §5 exists.
- **`Harness` implementers must classify.** `completed` alone no longer
  satisfies the contract. A harness that genuinely cannot attribute should
  report a *non-retryable* member for an unexplained ending — guessing
  `EXECUTION_ERROR` is the direction that inflates.

**Neutral**

- The disclosure obligation is unchanged and now has a second clause: a
  published number is "resolved within N eval attempts of one patch", and — if
  `retry_on_timeout` was ever set — it must say so.
