# Feasibility — plan B: per-step rejection sampling in the reverse proxy

**Question asked:** can the oracle sit in the Go reverse proxy, hold the model's
own sample at a step boundary, judge it, and **resample** (never rewrite) until
one is accepted — and at what cost?

**This document answers *can it be done, what changes, what does it cost, what
is unknown*. It does not propose a design.** Every claim below carries a reading
or a code location; anything I could not measure is marked **not verified**.

**Method.** `cc-reverse-proxy` was read **read-only** at HEAD `4d8d7e6` (884
lines, `reverse_proxy.go`); nothing in that repo was modified. Quantities come
from the stopped honesty-scorer pilot (20 attempts,
[`PILOT-DATA.md`](../honesty_scorer/PILOT-DATA.md)) and from wire logs captured
by the proxy on earlier baseline runs.

---

## 1. The proxy as it stands

| fact | location |
| --- | --- |
| single handler for every path | `http.HandleFunc("/", proxyHandler)` :875 |
| server write timeout 10 min | :880 |
| **request body fully buffered** into `body []byte` | `io.ReadAll(r.Body)` :652 |
| it **already rewrites requests** — OpenRouter `provider` injection | :656–673 |
| …and mirrors `Anthropic-Beta` → `X-Anthropic-Beta` | :700–704 |
| **re-sending an identical request upstream is already implemented** | retry loop :682–728, `bytes.NewReader(body)` :685 |
| …but only on transport failure | triggered by `client.Do` error :709–713 |
| response splits two ways | `isSSE(resp)` :302, routed :815 |
| SSE path | `streamSSE` :524 |
| buffered path (`io.ReadAll`) | `handleBuffered` :606–607 |

**The decision material a per-step oracle needs already exists in-process.**
`streamSSE` accumulates every event into `sseEvents` (:526), and after the
stream ends `sseEventsToMessage` (:413) assembles the complete assistant message
(:581); completeness is already defined as *a `message_delta` carrying a
`stop_reason`* (:587–593). The proxy therefore already knows what the full turn
was.

**The obstacle is ordering, not capability.** The bytes are written to the
client *inside* the scan loop (`w.Write(wireBytes)` :560, flushed at event
boundaries :568), so by the time the turn is assembled it has already been
delivered.

## 2. What would have to change to hold a turn before forwarding

1. **Defer the response head.** `w.WriteHeader(resp.StatusCode)` runs at :813,
   *before* `streamSSE`. Buffering requires deferring status and headers until
   after the accept/reject decision.
2. **Split `streamSSE` into collect and replay.** Today one loop scans, records
   and writes. Collection already exists; the write has to move behind the
   decision.
3. **Replay must re-emit the stored wire lines, not re-serialize.** The proxy
   forwards raw `line + "\n"` verbatim (:537, :560). `sseEventsToMessage` is a
   lossy summary — it exists to build a record, not to reconstruct a stream — so
   a replay built from it would not be byte-faithful. Keeping the raw lines is
   the cheap path, and nothing today stores them (only parsed events are kept).
4. **Only the SSE path matters.** Every request in the captured wire log sets
   `stream: true` (71 of 71 records, `baseline-navidrome-rollout-0`), so
   `handleBuffered` is not on the hot path for this design.
5. **Memory is not a constraint.** `max_tokens` is 64000 in the observed
   requests; measured output per request is mean 289, median 161, p90 637,
   max 2200 tokens (71 requests). The existing scanner already allows 4 MB per
   line (:530).

### Is the interruption observable to the Claude Code client?

- **Verified:** the client always requests streaming (71/71 above), so the
  change is from incremental delivery to one delivery at end of turn. The server
  side tolerates the wait — `WriteTimeout` is 10 minutes (:880).
- **Not verified:** whether Claude Code applies its own first-byte or idle
  timeout, and whether anything in it behaves differently when a whole turn
  arrives at once. I found no reading for this and did not run the client.
- **Weak bound from data:** across the pilot, wall time averages
  **11.4 s per request** (8436 s / 740 requests), and that figure *includes*
  tool execution between turns. So the added delay is of the order of one
  turn's generation time, not minutes. This is an average, not a distribution,
  and it does not bound the tail.

## 3. Resampling against the upstream

- **Mechanically it is the existing code path.** Re-issuing the same buffered
  body is what the retry loop already does (:685); what is new is the
  *trigger* — a response that arrived intact and was rejected, rather than a
  transport error.
- **Sampling is stochastic by default, which is what makes B possible at all.**
  The observed request bodies carry no `temperature`, no `top_p`, no seed (body
  keys: `max_tokens`, `messages`, `metadata`, `model`, `output_config`,
  `provider`, `stream`, `system`, `thinking`, `tools`). A resample of an
  identical request is therefore a genuinely different sample. `thinking` is
  `{"type": "disabled"}` in the observed run.
- **No idempotency mechanism exists to interfere.** `grep -in "idempot"` over
  `reverse_proxy.go`: 0 hits. Anthropic's public docs, searched 2026-09-01, do
  not document an idempotency key for `/v1/messages`; the *Message Batches*
  endpoint is the one described as idempotent. So a resample is simply a second
  request — nothing deduplicates it, and nothing needs to be defeated.
- **A rejected sample is billed in full.** The documented no-charge case is a
  request refused *before any output is generated*. A rejected sample is a
  completed generation by construction — the proxy must read it to the end to
  judge it. This is structural rather than measured: I did not test billing.
- **The binding limit is the subscription window, not a per-minute quota.**
  Every one of the 20 pilot attempts emitted `rate_limit_event`: 22 events of
  `rateLimitType: seven_day` with `status: allowed_warning` and utilization
  **0.61–0.65**, plus 7 of `five_hour` with `status: allowed`. A design that
  multiplies request count consumes that same seven-day budget proportionally,
  and the batch was already at ~⅔ of it.

## 4. Where the oracle runs — the two placements differ in more than latency

|  | in the Go process | external service the proxy calls |
| --- | --- | --- |
| latency per decision | in-process call | one round trip per step, on the critical path of every turn |
| language | the judge must be Go, or embedded | any; the existing guidebook tooling is Python |
| failure mode | a panic in the judge takes the proxy — and the rollout — with it | can time out; the proxy can fall back to *accept*, degrading to today's behaviour |
| **reachability** | no constraint | **constrained: the capture proxy now runs *inside* the sandbox** (spec §16, citing task 10 / ADR-0012), so an external oracle must be reachable from inside it |
| blast radius on change | every change redeploys the proxy binary | judge can be changed without touching the proxy |

The reachability row is the one that actually decides this, and **the sandbox's
egress rules are not verified** — I read the spec sentence, not the sandbox
configuration. If nothing may be reached from inside, "external" means "in-Go or
nothing"; if a host-side callback is permitted, the failure-mode and language
columns favour external strongly.

## 5. Cost model

**The unit that a step rejection repeats is one request.** Measured over the
pilot (20 attempts, 740 distinct assistant message ids):

| per request | value |
| --- | --- |
| input tokens incl. cache read + creation | 46,837 |
| output tokens | 284 |
| nominal cost | **$0.0295** |

Corroborated independently by the wire log (71 requests, different runs, and a
different derivation): output mean **289**, median 161, p90 637, max 2200;
cache read mean 79,459.

**The unit that a trace rejection repeats is one attempt:** $1.093 nominal,
37 requests.

The 37× ratio between them is an **identity, not a finding** — cost per attempt
divided by cost per request *is* requests per attempt. The empirical content is
the denominator: **a trace averages 37 requests**.

With a stationary per-step rejection rate `p`, expected requests per accepted
step is `1/(1-p)`, so:

| approach | cost multiplier over an unfiltered rollout |
| --- | --- |
| per-step resampling at rate `p` | `1/(1-p)` — 2× at `p = 0.5`, 5× at `p = 0.8` |
| trace-level best-of-N | `N` |

so per-step is cheaper whenever `1/(1-p) < N`. At the pilot's measured price a
2× multiplier is **$2.19 per kept trace**; best-of-4 is **$4.37**.

Three things this model does **not** include, all of which push the real number
up:

- **The oracle's own cost.** If judging a step is itself an LLM call, it adds
  roughly one request per step — which on its own is close to a 2× multiplier
  before any resampling. Not modelled here.
- **Stationarity is assumed and unmeasured.** Rejecting a step changes the
  trajectory, so later steps are not the steps that would have occurred. The
  step-level accept rate has **never been measured** — no oracle has ever run at
  step granularity. **Not verified.**
- **Prompt caching.** A resample re-sends an identical prefix and should hit the
  cache (cache reads dominate input: 46,837 of which the overwhelming majority
  is cache read). Whether the cache entry behaves identically on an immediate
  re-send is **not verified**.

## 6. Red lines — argued independently

**On the narrow question, resampling does not violate the ban on rewriting.**
The ban's own recorded rationale (§5) is measured and specific: *a rewritten
call is not reflected back in the assistant turn, so the actor finishes the turn
believing it did something it did not do, and every later step reasons from a
false premise.* Under resampling no such gap exists — the actor's context
contains exactly one turn, which the actor produced, and nothing is fabricated,
edited or hidden from the conversation it went on to reason from. §6 ("the trace
is the conversation, unedited") also survives: the delivered conversation is
unedited.

**But the spec collides with plan B in two other places, and neither is a
technicality.**

1. **§5 decides against exactly this location.** A separate owner-decided row
   reads: *"**Steer from a Claude Code hook** — not the proxy, not our own agent
   loop"*, with the rationale that the proxy is already complex and folding
   steering into it couples the two badly. Plan B is proxy-based steering. That
   is a decision of record, and it is not answered by showing that the proxy
   *can* do it — the row already assumes it can, and rejects it on coupling.
   Superseding it takes a decision, not a feasibility argument.

2. **§16 is written about capability, not use.** *"Rewriting the actor's tool
   calls or its assistant turns … a proxy-based design that **could** rewrite
   the assistant turn is a different design, not a later phase of this one."*
   The change described in §2 above — hold the complete turn, decide, then
   forward — is precisely the machinery that creates the ability to rewrite. B
   would not use it, but the sentence as written is not about use. The same
   paragraph draws the current proxy's line in exactly those terms: it modifies
   *requests* and "still does not touch assistant turns".

**And one consequence that is neither a violation nor free.** What resampling
changes is the **distribution**: the trace is no longer a sample from the
model's own policy but from that policy *conditioned on the oracle accepting*.
Every token remains the model's, so this is not an edit — but §14's policy stamp
exists to make contamination legible, and today's stamps describe oracle-guided
*injection*. A resampled run is contaminated by a different mechanism and would
need its own stamp, or aggregation would silently pool two different things.

## 7. What remains unverified

- Claude Code's client-side timeout and its behaviour when a turn arrives all at
  once (§2).
- The sandbox's egress rules, which decide whether an external oracle is
  reachable at all (§4).
- The step-level accept rate — the single largest driver of the cost model, and
  the one quantity no run of any kind has produced (§5).
- Prompt-cache behaviour on an immediate identical re-send (§5).
- Billing for a completed stream the proxy never forwards; reasoned from the
  documented refusal-only exemption, not tested (§3).
