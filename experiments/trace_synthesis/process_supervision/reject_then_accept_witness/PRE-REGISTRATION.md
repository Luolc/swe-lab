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

### The material is bound by digest, not by an address

Naming a line index in an **off-repo, mutable** capture fixes an *address*, not
*content*: replace that line after this merge and the run still produces equal
per-attempt hashes on different material, which would defeat the whole
before-data commitment. Per-attempt hashes show only that one run reused whatever
it selected.

So the content itself is pinned here, and asserted **before any proxy, judge or
API work**:

| | canonical sha256 |
|---|---|
| request body | `072544ccd33384d33b280bdafed44b159685cebf5af661426654a37b0d41fd45` |
| original completion (attempt 0's material) | `e12278e8927ef3100498462c19218a8946bda1517bc910103403abf60aad877a` |

Canonical form is `json.dumps(value, sort_keys=True, separators=(",", ":"))`;
the body is 115,500 bytes in it, with 31 messages, 26 tools, model
`anthropic/claude-sonnet-5`. A mismatch terminates the run as **`void`** before
anything is started or spent.

Note what the per-attempt `sent_body_sha256` does **not** do: it shows that one
run reused whatever body it selected, and nothing about *which* body that was.
The two questions — *what does this check prove* and *what can it not prove* —
are separate, and a mechanism added to close one gap is not thereby exempt from
the second. A digest of a request body carries no credential —
the captured `metadata.user_id` is already `<redacted>` and headers are not
hashed. The observed digests are written into the results, so a mismatch is
visible rather than merely fatal.

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

**Scope of the word "identical".** The body sets
`thinking: {"display": "omitted", …}`, so hidden reasoning is not in the
response. "Identical" therefore means **identical in the part we and the judge
can see**. Two generations may reason differently and emit the same visible
output; this run does not observe that and makes no claim about it. This is not a
defect in the design — it is the word's scope written down, which is what most of
this week's errors were missing.

## 4. Pre-registered reading of the three outcomes

Quoted from `DEBATE-VERDICT.md` §4; fixed before the run:

1. **All K bit-identical to sample 0** → **B does not function.** Identical
   re-send is deterministic or deduplicated. Adopt A′ (still gated on
   compliance). *This is the only outcome in which a pair study is allowed to
   decide anything; a pass of divergence still decides nothing about cost.*
2. **Outputs diverge and 0/K accepted** → B samples and does not gate. *(This
   requires K **readable** verdicts: an unreadable answer is not a rejection, and
   a run containing one is `judge-unparseable`, never outcome 2. Outcome 1 is
   unaffected — identical completions is a property of the actor, not the
   judge.)* On a step
   that actually needs intervention, B's cost has **no measured finite bound**.
   Stay with A′.
3. **First accept at attempt *k* ≤ 10** → **B exists.** *k* is the first cost
   observation (extra actor completions per rejected step, this target, this
   judge, this step). *(§4's sentence continues "Reopen the debate under §3.3";
   that clause is quoted here as source wording only and is **not** operative —
   see immediately below.)*

**Operative reading of outcome 3, which governs:** it establishes **only the
witness half** of §3.3. §3.3 is a conjunction — the witness **and** Claude Code
accepting a fully-buffered turn under a redesigned deadline — and the second
condition is **not tested by this run at all**. Outcome 3 therefore **cannot
reopen the debate** and must be reported as *one of two conditions met*. No
sentence in this document, quoted or otherwise, may be cited for a stronger
reading.

### Additional obligation on outcome 3

Every attempt records `X-Provider-Name`, `X-Generation-Id`, `request-id` and
`Cf-Ray`. `provider.require_parameters: true` in the body already narrows routing to
providers supporting every parameter sent — but **narrowing is not elimination**,
so the header is recorded regardless. If outcome 3 occurs, the report **must**
state whether the accepted attempt and the rejected attempts hit the **same
provider**. If they differ, the
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

**Stop rule, declared now:** if cumulative measured cost exceeds **$2.00**, stop.
A run stopped that way is reported as **inconclusive** — explicitly *not* as
outcome 2, since "0/K accepted" requires all K attempts.

### Five terminations, five words, none readable as another

Every way this run can end has its own name, because the failures that matter
this week all came from two different states sharing one sentence:

| word | trigger | what it means |
|---|---|---|
| `outcome-1` / `outcome-2` / `outcome-3` | §4 | the pre-registered readings |
| `inconclusive` | cumulative cost exceeds the ceiling | the run did not finish; **not** outcome 2, which requires all K attempts |
| `void` | a pre-registered digest does not match | **the material is not ours; this run did not happen.** Not a result of any kind, and nothing is spent — the check precedes every paid call |
| `material-retired` | attempt 0's completion is no longer judged off-track | the step is retired and the next named one is used; reported as a result in its own right, since it is an observation about judge stability |
| `judge-unparseable` | a judge answer cannot be read — at attempt 0, or on any resend | distinct from `material-retired`, which asserts the judge now *accepts* the step; an unreadable answer asserts nothing, so the premise cannot be established. #305 measured 2 of 69 judgements unparseable, so this is an ordinary path |
| `unreproduced-accept` | an accept that does not survive re-judging | **not** outcome 3, because §8.3 says an accept that does not reproduce is not a witness — and **not** outcome 2 either, since the run stopped at the accept and never completed K attempts |

`void` in particular is neither "we ran and got nothing acceptable" nor "we ran
out of budget". It is *there was no run*.

**One ledger covers every billable call**, not just the K attempts: attempt 0's
judge call, each attempt's actor and judge calls, both repeat judgements of a
first accept, and the cache-off confirmation. The ledger is persisted, the
reported total is derived from it, and **the ceiling is checked before issuing
each further call** — so an accept arriving at $1.95 cannot buy two more
judgements and print a witness whose true cost crossed the ceiling. Once the
observed cumulative cost exceeds the ceiling, no further authorized call is made
and the run is classified inconclusive.

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
the completion improving** — and *k* is the one number this run contributes, so
undisclosed judge variance would corrupt the only cost observation it produces.

So: **on the first accept, the same completion is judged twice more**, and the
report states **"accepted n of 3"**. Cost is two judge calls, **counted in the
ledger of §7**. An accept that does not reproduce is not a witness — and even a
reproduced one establishes only the witness half of §3.3, per §4.

**An unreadable repeat counts as non-agreement**, never as an error that ends the
run: paid calls must not be left without a named terminal state.

**The bar is unanimity, fixed now: outcome 3 requires n = 3.** An accept with
1 ≤ n < 3 is classified **`unreproduced-accept`**, its own ending — it is not
outcome 3, and it is not outcome 2 either, because the run stopped at the accept
and never completed K attempts. Unanimity rather than a majority because outcome
3 is the only reading that reopens work, so the expensive claim takes the strict
bar; with three samples, a 2-of-3 rule would let a judge answering at random
qualify as a witness a substantial fraction of the time. No data exists at the
time this threshold is written.

### 8.4 Attempt 0 — re-judge the original completion with *this* judge

The claim that this step is off-track comes from #305's run. Carrying it into
this run unexamined would be using one run's verdict to support another run's
premise.

So the original recorded completion is judged as **attempt 0**, by the same judge
module and guidebook this run uses:

- still **off_track** → the material holds, continue;
- now **on_track** → the material is **`material-retired`** (not `void`, which
  is reserved for a digest mismatch): stop, move to the next step named in §2,
  and report this as a result in its own right, since it is an observation about
  judge stability.

## 9. Judge variance — a precondition, pre-registered before its data

Attempt 0 returned **on_track** on the completion #305 judged **off_track**. The
material's digests both matched, so the input was identical.

**Measured before drawing anything from that:** the judge's request carries
`{model, max_tokens, messages}` and **no `temperature`, `top_p`, `top_k` or
`seed`** — in either run, since today's code imports #305's module. Sampling is
the provider's default and was never recorded. So disagreement across runs is
**expected**, and the claim "the judge is unstable" is not available; what is
available is "**this gate's sampling was never pinned**".

Two further differences are **permanently unresolvable**, and that is a cost of
this study, not a footnote: neither run recorded the model id the *response*
reported (only the alias sent), and #305's compared verdict ran at
`max_tokens = 700` against today's `2000`. Whether the alias resolved to the same
served model cannot be recovered. **We do not attempt to repair this by adding
one variable; a missing variable does not return another.**

### Why this blocks the witness, not merely informs it

B's loop is *judged reject → resend → judged accept → stop*. **If the gate is a
random function of its input, that loop can terminate on a coin flip** — the
second completion need not be better; the judge need only land the other way.
As designed, the witness therefore **cannot distinguish "resampling produced a
better completion" from "the judge flipped"**. "The accepted resend was better"
is only meaningful against a measured variance, and none exists.

The 3-of-3 rule in §8.3 reduces that probability without removing it, and it
guards only the accepting side: **a rejection is still a single judgement**.

### The judge's whole input is bound, not just the completion

The material digests in §2 cover the request body and the original completion.
They do **not** cover the two other things the judge's prompt is built from: the
**guidebook** and the **preceding-steps rendering**. Either can drift without a
material digest noticing, and the run would then spend money producing results
that look ordinary and are no longer about the pre-registered material.

So the entire judge input — instructions, guidebook, preceding steps and the
completion summary, exactly as sent — is pinned and asserted **before any paid
call**, in both this measurement and the witness's attempt 0:

| | canonical sha256 |
|---|---|
| judge input (system + user) | `57d9cb24dc0b220fe366377e8d6757aa15843679da6af5a374311f77f5fbb661` |

A mismatch is `void`: there was no run. Both scripts render that prompt through
**one implementation**, so the digest cannot bind text a script no longer sends.

### The measurement

The completion fixed in §2 is judged **25 times, all at `max_tokens = 2000`**:

- **20 at the provider's default sampling** — the gate as it runs, and as B would
  ship it. **This arm is the object of study.**
- **5 at `temperature = 0`** — **one-directional**, see below.

**Why 20 and not 5.** The risk of a small n is not in the noisy case but in the
**quiet** one: a handful of identical answers reads like a stable gate while
excluding almost nothing. More trials strictly increase the chance of observing a
flip if flips occur at all, and the extra calls cost about twenty cents.

**The zero-disagreement reading, fixed now so it cannot be written after the
fact:**

> **20 identical answers ⇒ `observed 0 disagreements in 20 calls`. That is the
> whole result**, and it is **not** "the gate is stable".

**No confidence bound is asserted from it.** The familiar ≈ 3/n bound (≈ 0.15 at
n = 20, ≈ 0.6 at n = 5) requires **independent, identically distributed trials at
a stationary rate** — and this design **deliberately does not establish that**:
routing and the served model vary freely and are only *recorded*, so the calls
are not known to be exchangeable. The bound may be quoted **only** as an explicit
conditional — *were the trials iid at a stationary rate, 0/20 would correspond to
a 95% upper bound near 0.15* — and never as a property of this run.

The decision-relevant reading does not depend on any of this: **a single
disagreement in the default arm confounds the witness**, and that needs no
distributional model at all.

**The `temperature = 0` arm is hard in one direction only**, and must be reported
that way:

> **A flip at `temperature = 0` falsifies "pinning the temperature fixes this
> gate".** That conclusion needs no distributional assumption: one counterexample
> is enough.
>
> **Five quiet calls at `temperature = 0` confirm nothing** — the same
> conditional applies, and `temperature = 0` is not determinism on a hosted
> endpoint anyway.

A test that is sharp in one direction read as sharp in both is the error this
pre-registration keeps catching, so the asymmetry is written beside the arm
rather than left to the reader.

No arm at 700: that cap only serves the #305-versus-today comparison, which is
already unresolvable for want of a recorded model id.

### Reading, fixed before the run — one, and it is not causal

**The only claim this measurement supports:**

> **Does the gate disagree with itself on a byte-identical input?** Any
> disagreement within the **default arm** is sufficient to establish that the
> witness, as designed, **cannot separate "resampling produced a better
> completion" from "the judge landed the other way"** — because a reject→accept
> transition is then available without the completion changing at all.

That is decision-relevant and needs **no attribution**: it does not matter *why*
the gate disagrees for the witness to be confounded by it.

**What this measurement cannot do, stated so no later reader tries:** it cannot
decide *pin the sampling* versus *fix the guidebook*.

- `temperature = 0` **is not determinism** on a hosted endpoint. Batching,
  serving-stack nondeterminism and mixture routing all survive it.
- **Routing and served model are uncontrolled.** Provider and the model id the
  response reports are *recorded per judgement* (§9's instrumentation) but are
  **not held fixed**, so an arm's behaviour is not attributable to its
  temperature.
- **No convergence statistic is defined, and none can be at n = 5 per arm.**
  "Converges" is not an observation this design can make. Agreement counts are
  reported as counts, with n, and nothing is inferred from them about a rate.

The `temperature = 0` arm is therefore **descriptive only**: it is reported
beside the default arm, with provider and model id per call, as *context for
where variance might sit*. Any sentence in the report that turns it into a cause
is out of bounds.

The stage-5 clause split — the same stage cited by both verdicts, read once as
ordering ("run it *before* editing anything") and once as command form — remains
**a description of where the observed disagreement concentrated**. This
measurement cannot promote it to an independent finding, and the report must not.
Establishing a semantic source needs a design that controls routing and served
model, which is a separate pre-registration.

**Not repaired here.** Fixing the guidebook to make the judge steadier and then
measuring whether the judge is steady is circular. That question is a separate
pre-registration belonging to `guidebook_as_step_criterion/`, and no conclusion
here may be rescued by "it would be fine once fixed".
