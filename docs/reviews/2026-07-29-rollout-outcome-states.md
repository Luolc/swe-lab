# Rollout outcome states — how many ways can a rollout end?

**Date:** 2026-07-29 · **Kind:** engineering audit (a snapshot, not a spec)

A rollout has no single status. It has **four independent axes**, and the agent
axis — the one the question is really about — is currently collapsed to a single
boolean. This maps the real state space, from three sources: our code, the
Claude Code CLI source vendored in the sibling `coding-cli-survey`
(`submodules/claude-code`), and the published headless docs. It closes with the
states we currently cannot tell apart, and a proposed taxonomy.

## 1. The axes we record today

| Axis | Where | Values |
|---|---|---|
| **Engine** | `RunStatus` (`sandbox/result.py`) | `success`, `setup_error`, `run_error` |
| **Agent** | `RolloutOutcome.complete` | `True` / `False` — **one bit** |
| **Patch** | `RolloutOutcome.is_empty`, `.binary_stripped` | empty / non-empty × stripped or not |
| **Grade** (optional) | `SweBenchProVerdict` + `OutputState` | `resolved` bool + `score`; `ok` / `absent` / `unparseable` |

The CLI flattens these into one string for humans (`cli/rollout.py::_finish`):
`solved_not_graded`, `empty_patch`, `resolved`, `unresolved_tests_failed`.
That's 4 labels over a space that is much larger — and none of them can say
*why* an agent produced nothing.

## 2. What the agent actually reports (authoritative)

The terminal `result` message has **exactly five** subtypes. From
`submodules/claude-code/src/entrypoints/sdk/coreSchemas.ts`
(`SDKResultSuccessSchema` / `SDKResultErrorSchema`):

| `subtype` | Meaning |
|---|---|
| `success` | The loop finished |
| `error_during_execution` | An exception escaped the turn loop |
| `error_max_turns` | Hit `--max-turns` |
| `error_max_budget_usd` | Hit `--max-budget-usd` |
| `error_max_structured_output_retries` | Could not produce schema-valid output |

Load-bearing details, all verified in source:

- **`is_error` is an independent boolean, present on *both* schemas.** A
  `success` subtype can carry `is_error: true` (the final turn was an API
  error). So "finished cleanly" needs **both** `subtype == "success"` *and*
  `not is_error` — which `event_stream_complete` already does correctly.
- **`errors: z.array(z.string())`** exists only on the error schema and carries
  the diagnostic text. **We never read it.**
- Also free on every result and unused by us: `num_turns`, `duration_ms`,
  `total_cost_usd`, `usage`, `permission_denials`, `stop_reason` (nullable —
  never depend on it).
- **Exit code** (`src/cli/print.ts`): `1` if the last message is a `result` with
  `is_error`, else `0`. Docs add: **SIGTERM → 143**, stdin over the 10 MB cap →
  non-zero, invalid `--json-schema` → non-zero.
- **A result is not guaranteed.** The top-level `catch` in `print.ts`
  synthesizes an `error_during_execution` result — but inside its own
  `try/catch` that comments *"If we can't emit the error result, continue with
  shutdown anyway"*. A hard kill (SIGKILL, OOM) emits **nothing**.
- Pre-terminal transients surface as `system/api_retry` events with a
  10-value `error` category (`rate_limit`, `overloaded`, `server_error`,
  `authentication_failed`, `billing_error`, …). These are retries, not
  outcomes — but they are the breadcrumb trail for a run that later dies.

## 3. The real state space of an agent run

Ten reachable states, ordered from "never ran" to "finished":

| # | State | Evidence available |
|---|---|---|
| 1 | **Never started** — image pull / mount / `up` failed | `RunStatus.setup_error`; no trace |
| 2 | **No output at all** — SIGKILL / OOM before any write | trace file absent |
| 3 | **Truncated** — partial stream, no terminal `result` | trace present, no `result` event |
| 4 | **Timed out** — our watchdog killed the exec | `ExecResult(124, timed_out=True)` — **discarded today** |
| 5 | **Finished** | `success`, `is_error: false` |
| 6 | **Finished, last turn errored** | `success`, `is_error: true` |
| 7 | **Max turns** | `error_max_turns` + `errors[]` |
| 8 | **Max budget** | `error_max_budget_usd` + `errors[]` |
| 9 | **Structured-output retries exhausted** | `error_max_structured_output_retries` |
| 10 | **Execution error** (incl. usage/credit limit) | `error_during_execution` + `errors[]` |

States 5–10 are all "the agent wrote a terminal result"; 2–4 are all "it
didn't". **Today we report `complete = True` for state 5 only, and
`complete = False` for the other nine** — so nine distinct failure modes are one
indistinguishable bucket.

Orthogonally: the patch axis (absent / empty / non-empty, × binary-stripped) and,
if `--grade`, the verdict axis (`resolved` + `score`) with its own
`output_state ∈ {ok, absent, unparseable}` guarding against a crashed parser
masquerading as "no tests passed".

## 4. Findings — what we cannot currently tell apart

**F1. Nine failure modes collapse to one bit.** `complete: bool` cannot
distinguish "hit max turns" (a *budget* problem — raise the cap) from "crashed
in execution" (an *infra* problem — retry) from "we killed it on timeout" (a
*sizing* problem). All three read as `complete = False, patch = ""`. This is the
core answer to the question: the taxonomy exists upstream and we discard it.

**F2. The agent's exit code is thrown away.** The invocation script ends with
`|| true` (`harness.py`), so the CLI's exit status — including the documented
`143` for SIGTERM and the non-zero for the stdin cap — never reaches us.
Deliberate (a non-zero agent must not fail the sandbox step), but it means the
exit code is unavailable as a signal.

**F3. Our own timeout is invisible.** `ClaudeCodeHarness.run` does
`_ = sb.run_script(...)`, discarding the `ExecResult`. On timeout the backend
returns `ExecResult(124, timed_out=True)` — dropped. A timed-out rollout reports
`RunStatus.SUCCESS` with `complete = False`: **byte-identical to a silent
crash**, even though the engine knew. Cheapest high-value fix in this list.

**F4. `complete` means different things per capture mode.** This one is a
correctness hazard, not just lost detail:

- **STREAM**: `complete` = the *agent loop* finished (`result`/`success`).
- **PROXY**: `complete` = the *last HTTP response* was fully received —
  `reverse_proxy.go` sets it from a `message_delta` carrying a `stop_reason`, or
  a fully-read buffered body.

Those are not the same claim. In PROXY mode, `error_max_turns` still ends with a
perfectly complete final API response, and so does a crash *after* the last
response — both yield `complete = True`. **PROXY mode false-positives on exactly
the failures we most want to catch.** The two modes are not interchangeable for
completion, only for the conversation.

**F5. Free metrics unused.** `num_turns`, `total_cost_usd`, `usage`,
`permission_denials` sit in the result we already parse. `total_cost_usd` and
`num_turns` per rollout are directly useful for pass@K cost analysis, and now
have somewhere to go (`Contribution.metrics` → `RunRecord.metrics`).

## 5. Proposed taxonomy

Replace the bool with an explicit enum on the harness — `Harness.outcome(sb)`
returning a value, with `completed()` kept as `outcome is FINISHED` — keeping it
**orthogonal** to `RunStatus` (engine), the patch axis, and the grade axis:

```python
class AgentOutcome(StrEnum):
  NO_OUTPUT = "no_output"                    # nothing written
  TRUNCATED = "truncated"                    # partial trace, no result
  TIMED_OUT = "timed_out"                    # our watchdog (needs F3)
  FINISHED = "finished"                      # success, not is_error
  FINISHED_WITH_API_ERROR = "finished_with_api_error"
  MAX_TURNS = "max_turns"
  MAX_BUDGET = "max_budget"
  MAX_OUTPUT_RETRIES = "max_output_retries"
  EXECUTION_ERROR = "execution_error"        # carries errors[]
```

`NOT_STARTED` is deliberately absent: that is `RunStatus.setup_error`, the
engine's axis, and duplicating it would create two homes for one fact.

Sequenced so each step stands alone:

1. **F3 + F4 first** — they are correctness bugs, not enrichment. Thread the
   `ExecResult` into the outcome, and make PROXY-mode completion honest (either
   read the agent's own trace even in proxy mode, or document `complete` as
   "last response complete" and stop treating the two modes as equivalent).
2. **F1** — the enum above, plus the `errors[]` text into `RunRecord.extra`.
3. **F5** — `num_turns` / `total_cost_usd` as metrics.

**Why it matters beyond tidiness:** with pass@K landing (ADR-0004), K samples of
one instance will fail for different reasons, and `attempt` exists precisely so
an *infra* failure can be retried. Retry logic needs to know which of the nine
it hit — `MAX_TURNS` should not be retried, `EXECUTION_ERROR` should. The one bit
cannot support that decision. §6 is that policy.

## 6. Retry policy for a fair eval

This has to be settled **before** `attempt` gets wired up, or the first retry
implementation bakes in whatever was convenient. One principle decides every
case:

> **Retry only failures that are not attributable to the agent.**

If the agent caused the failure *given a fixed budget*, retrying hands that agent
extra attempts a better-behaved agent would not need — the score inflates. If our
infrastructure caused it, not retrying penalizes the agent for our problem — the
score deflates, non-deterministically. Both are unfair. The axis is **causal
attribution, not severity**: a crash is retryable not because it is bad but
because it is ours.

| Outcome | Retry? | Attribution |
|---|---|---|
| `RunStatus.setup_error` (pull / docker / mount) | ✅ yes | never got an attempt |
| `NO_OUTPUT` (SIGKILL, host OOM) | ✅ yes | infra killed it |
| `TRUNCATED` | ✅ yes | infra killed it mid-flight |
| usage / credit limit (an `EXECUTION_ERROR` flavour) | ✅ yes, after waiting | our account, not the agent |
| `FINISHED_WITH_API_ERROR` | ✅ yes | final turn died on an API error |
| `EXECUTION_ERROR` (other) | ⚠️ classify | catch-all — see below |
| `TIMED_OUT` | ❌ **no** | wall-clock *is* the budget |
| `MAX_TURNS` | ❌ no | it spent its own budget |
| `MAX_BUDGET` | ❌ no | same |
| `MAX_OUTPUT_RETRIES` | ❌ no | its own output was invalid |

### The two cases that need care

**Timeout is a result, not a fault.** Wall-clock is a budget exactly like
`max_turns`: an agent that thrashes or re-runs the suite ten times hits it, and
that is a finding — score it **unresolved**, do not re-roll. This is only fair if
the clock measures the *agent*, which ours does: `timeout` reaches
`sb.run_script` via `harness.run`, so it starts after `up()` + `mount()` and
excludes the image pull (which has its own 3600 s budget). Host speed still leaks
in (amd64 emulation, noisy runners), so the budget must be generous enough that
host variance cannot flip a verdict. **A timeout rate above a few percent means
the box is being measured, not the agent.**

**`EXECUTION_ERROR` is not one thing.** It is the catch-all: an API 5xx that
exhausted retries (infra → retry), an auth failure (infra → retry), an internal
agent bug, an OOM. It cannot be classified without the `errors[]` array — a
second reason to capture it (F1). The machinery already exists and should be
reused, not reinvented: `harnesses/claude_code/errors.py::classify_error_text`
separates `UsageLimitError` / `RetryableError` / generic.

### The invariant that protects fairness

**The retry predicate must be decidable without looking at the grade.**

Never "retry because the patch was empty", never "retry because the tests
failed". Outcome-conditional retry re-rolls bad luck and directly inflates
pass@1. Concretely: the predicate is a function of `AgentOutcome` + `RunStatus`
and **never** reads `verdict`. That is testable, so per the quality bar it gets a
named test rather than a comment.

### Retry ≠ resample

These are the two ADR-0004 fields, and blurring them corrupts the metric:

- **Retry** an infra failure → **same `rollout_id`, bump `attempt`**. The pass@K
  denominator is unchanged; a void attempt is being replaced.
- **Resample** for pass@K → **new `rollout_id`, `attempt = 0`**. This *is* the
  denominator.

Retrying by incrementing `rollout_id` silently turns pass@4 into
pass@5-for-unlucky-instances.

### What a fair report must state

- **Cap retries** (2 is plenty) and record the count — an eval where some
  instances got three attempts is not reproducible unless the shards say so, and
  `attempt` already persists exactly that.
- **Report the infra-failure rate.** If the cap is hit and a run is still void,
  either score it unresolved or exclude it — and if excluded, say so, because it
  changes the denominator.
- **Break out `MAX_TURNS` / `TIMED_OUT` separately** from genuine failures. A
  high `max_turns` share is a tight cap, not a weak agent; publishing it inside
  "unresolved" without the breakdown misleads.

## Sources

- `submodules/claude-code/src/entrypoints/sdk/coreSchemas.ts` — result schemas
  (the 5 subtypes, `is_error`, `errors[]`)
- `submodules/claude-code/src/cli/print.ts` — exit-code rule; the synthesized
  `error_during_execution` and its best-effort `catch`
- <https://code.claude.com/docs/en/headless> — SIGTERM → 143, stdin cap,
  `api_retry` error categories
- `cc-reverse-proxy/reverse_proxy.go` — the proxy `complete` flag's semantics
- ours: `sandbox/result.py`, `rollout.py`, `cli/rollout.py`,
  `harnesses/claude_code/{harness,convert,errors}.py`,
  `datasets/swebench_pro/unit_test.py`
