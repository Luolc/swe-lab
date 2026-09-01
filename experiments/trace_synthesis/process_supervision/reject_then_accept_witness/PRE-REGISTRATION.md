# Pre-registration — reject-then-accept witness

**Committed before any data is collected.** The readings in §4 are fixed here and
may not be revised after the run. Source: `DEBATE-VERDICT.md` §4 (*the cheapest
single experiment that discriminates*), which this file implements without
reinterpreting.

## 1. The question, and what it decides

A′'s speaking cost is measured (0 extra actor requests, N=1). B's **existence**
and cost are not. This run asks one thing: **does re-issuing an identical actor
request eventually produce a completion the same guidebook judge accepts, and at
which attempt?**

It is the only experiment that can kill B for about a dollar, or give B its first
cost number.

## 2. Material

| | | provenance |
|---|---|---|
| step | `baseline-qutebrowser-rollout-0`, position 14, `step_index` 15 | judged **off_track** (stage 5) in PR #305 |
| why this one | first of #305's four off-track steps in `(rollout, position)` order | fixed here, before the run |
| request body | retained in the capture log, recoverable at that line index | measured |
| upstream | `https://openrouter.ai/api` | **measured** — `PROVENANCE.json` of both #305 traces records `actor_base_url`, not read from a constant |
| model | `anthropic/claude-sonnet-5` | measured, same file |
| transport | the current Go proxy (`cc-reverse-proxy`), built from the sibling checkout and run on the host | §4 requires "through the current Go proxy" |

The other three off-track steps are `baseline-…-0` positions 26 and 36, and
`steered-…-11` position 12. They are not used; naming them here prevents a later
switch from looking like a choice made after seeing a result.

**Scope, fixed now: this conclusion is about this upstream only.** No upstream
migration is in flight — the harness default is `https://api.anthropic.com`, but
the trace-synthesis driver overrides to OpenRouter and refuses to start otherwise;
#264/#286 moved the proxy's *location* into the sandbox, not the upstream.

## 3. Procedure

1. Load the body from the capture log at that line index.
2. Serialize **once**, with one serializer, and reuse that exact string for every
   attempt. Record its sha256.
3. Send it through the Go proxy K = 10 times, sequentially.
4. Judge each completion with `judge_steps.py` and the same guidebook.
5. **Stop at the first accept.**
6. No temperature, no seed, no body mutation — those answer a different question.
   `provider` stays exactly as captured (`allow_fallbacks: false`, `ignore:
   ["amazon-bedrock"]`, `require_parameters: true`).

### The one deviation, and why it is the more correct model

§4 says *keep the request body bytes*. The capture log stores request bodies as
**parsed JSON**, so the original bytes do not exist to be kept.

This is not a concession. **B, in production, re-sends the bytes in its own
buffer** — so the question is *what happens when one byte string is sent
repeatedly*, not *whether that string matches what the client first sent*. K
attempts identical to each other is B's actual behaviour. Failing to match the
original bytes does not touch the question.

Made checkable rather than asserted: every attempt's outgoing body is hashed and
**all K hashes must be equal**, so "the same bytes each time" is a recorded
observation, not a claim about my serializer.

### The criterion for "identical"

**The assembled completion**, not the raw SSE stream. Both are stored.

Reason: B judges completions, not transport frames. Chunk boundaries, timing and
keep-alives can differ while the completion is identical — that difference is not
the difference B needs, and counting it as divergence would misread **outcome 1
as outcome 2 or 3**. Outcome 1 is the only one that can kill B, so the criterion
is set where it cannot be inflated away.

## 4. Pre-registered reading of the three outcomes

Quoted from `DEBATE-VERDICT.md` §4; fixed before the run:

1. **All K bit-identical to sample 0** → **B does not function.** Identical
   re-send is deterministic or deduplicated. Adopt A′ (still gated on
   compliance). *This is the only outcome in which a pair study is allowed to
   decide anything; a pass of divergence still decides nothing about cost.*
2. **Outputs diverge and 0/K accepted** → B samples and does not gate. On a step
   that actually needs intervention, B's cost has **no measured finite bound**.
   Stay with A′.
3. **First accept at attempt *k* ≤ 10** → **B exists.** *k* is the first cost
   observation (extra actor completions per rejected step, this target, this
   judge, this step). Reopen the debate under §3.3.

**Outcome 3 satisfies only half of §3.3.** That clause requires the witness
**and** that Claude Code accepts a fully-buffered turn under a redesigned
deadline. The second condition is untested here, so outcome 3 must be reported as
*one of two conditions met*, never as "the debate reopens".

### Additional obligation on outcome 3

Every attempt records `X-Provider-Name`, `X-Generation-Id`, `request-id` and
`Cf-Ray`. If outcome 3 occurs, the report **must** state whether the accepted
attempt and the rejected attempts hit the **same provider**. If they differ, the
mechanism behind "B exists" is **routing variation, not sampling variation**, and
it may not hold under a pinned-provider deployment. Reporting only "k = 3" would
hide that.

## 5. What this is not

Quoted from §4: *It is not a steady-state rejection rate. It is not `1/(1-p)`. It
is not a claim about cache, billing of discarded streams, or client timeouts —
those stay unmeasured unless the same run happens to record them as side data.*

## 6. Recorded per attempt

Decided before the run, because outcomes 1 and 2 look alike in the moment
(neither yields something acceptable) and the evidence separating them may appear
once:

- assembled completion: bytes, sha256, `stop_reason`
- raw SSE stream, verbatim
- outgoing body sha256 (equality across attempts is the check above)
- `X-Provider-Name`, `X-Generation-Id`, `request-id`, `Cf-Ray`
- usage: `cost`, `input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`
- judge verdict and its own usage
- proxy `--max-retries`, pinned and recorded, since an upstream retry inside the
  proxy would otherwise be invisible in the attempt count

**Cache is recorded as side data, not as a claim.** A warm prompt cache is a
plausible mechanism behind outcome 1, and §5 forbids turning that into a
statement about caching — recording it keeps the mechanism visible without
asserting anything.

## 7. Budget

- **Measured:** the original step cost **$0.0158909**, with 72,122
  `cache_read_input_tokens` and 2 fresh input tokens — the recorded usage of the
  exact request being resent.
- **Inferred:** a cold-cache attempt costs roughly 10× that prompt component
  (≈ $0.15), so attempt 1 is the expensive one and attempts 2…K should read the
  cache it writes. A full K = 10 plus 11 judge calls lands near the verdict's
  ≈ $0.50.
- **Unmeasured:** the actual resend cost, which depends on cache state at run
  time. Every attempt's real cost is recorded and the cumulative total reported.

**Stop rule, declared now:** if cumulative measured cost exceeds **$2.00** before
K = 10, stop. A run stopped that way is reported as **inconclusive** — explicitly
*not* as outcome 2, since "0/K accepted" requires all K attempts.

## 8. Four additions, fixed before the run

Each closes a way the run could finish and still not be readable.

### 8.1 Sampling parameters — checked before the run, not assumed

We kept saying *no temperature, that answers a different question*, without ever
asking what it already was. **Checked, measured:**

| field | value |
|---|---|
| `temperature` | **absent** |
| `top_p`, `top_k`, `seed` | **absent** |

Absent in this request **and in all 37 agent turns of the rollout**. So sampling
is whatever the provider defaults to; **the value is unknown to us**.

This is the "absent" branch, so the run proceeds — but it fixes a limit on
**outcome 1**: identical completions would be evidence about *this upstream at
its default sampling*, not about re-sending in general, and a deployment of B at
non-zero temperature is not excluded by it. Had the body pinned `temperature: 0`,
outcome 1 would have been guaranteed by our own setting and the design would have
gone back for redesign.

Two further fields are recorded for the same reason, both able to bear on
determinism and neither part of the debate's vocabulary:
`output_config: {"effort": "high"}` and
`thinking: {"display": "omitted", "type": "adaptive"}`. Also
`provider.require_parameters: true`, which restricts routing to providers
supporting every parameter sent. **None of these is modified.**

### 8.2 Outcome 1 has two mechanisms, and they must be separable

The request carries `cache_read_input_tokens = 72122` and **three
`cache_control: {"type": "ephemeral"}` markers** — at `messages[30].content[0]`,
`system[1]` and `system[2]`. If all K are identical, at least two mechanisms
explain it:

- the upstream is deterministic or deduplicates identical requests → **B does not
  work**; or
- a cache layer returned a memoized completion → **B might work with caching
  off**.

These mean opposite things for B. So: cache usage fields and any cache-related
response header are recorded per attempt, and **if the run lands on outcome 1, one
additional attempt is made with those three `cache_control` markers removed**.
That extra attempt is **authorized here, in advance**; it is not a design change
made after seeing the result.

**Outcome 1 must be worded "under this run's caching conditions"** unless that
confirmation attempt was performed.

### 8.3 The judge has variance too, and outcome 3 is the expensive one

The judge is an LLM. A first accept at attempt *k* may be **the judge moving, not
the completion improving** — and outcome 3 is the only outcome that reopens a
debate.

So: **on the first accept, the same completion is judged twice more**, and the
report states **"accepted n of 3"**. Cost is two judge calls. An accept that does
not reproduce is not a witness.

### 8.4 Attempt 0 — re-judge the original completion with *this* judge

The claim that this step is off-track comes from #305's run. Carrying it into
this run unexamined would be using one run's verdict to support another run's
premise.

So the original recorded completion is judged as **attempt 0**, by the same judge
module and guidebook this run uses:

- still **off_track** → the material holds, continue;
- now **on_track** → the material is **void**: stop, move to the next step named
  in §2, and report this as a result in its own right, since it is an
  observation about judge stability.
