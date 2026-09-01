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

> ### Read this first: what the rate-limit readings do and do not say
>
> **The one assertable fact: the first `seven_day` utilization observed during
> execution 1 was 0.61.**
>
> Nothing here attributes consumption to this batch — not a share, not a bound
> on a share. That reading was taken *during* execution 1, after requests had
> begun, so no pre-request baseline exists and no starting point was measured.
> Whatever the batch drew, this document cannot say how much.
>
> **Open question, not argued here:** what a batch of this size costs against
> the weekly window, and how much headroom any mechanism has. Answering it needs
> a measured starting point, which no artifact in this batch contains.
>
> **A later reading, 2026-09-01 afternoon:** two agents' status lines read
> `Weekly: 3.0%`, against 61–63% that morning — relayed by `swelab-orchestra`
> and **not independently measured here**, since the figure is visible only to a
> running agent and no rollout has run since. Recorded as a reading. What it
> implies about available headroom is part of the open question above, not
> settled by it.

### How this number drifted, in three steps

Recorded because the number reached a user before it was checked, and because
**no step in the chain felt like an inference**:

| step | what was said | what was added |
| --- | --- | --- |
| the reading | 22 `seven_day` events at `allowed_warning`, utilization 0.61–0.65 | — |
| first restatement | "20 attempts consumed ~⅔ of the weekly window" | **consumption** — a flow, from a stock |
| second restatement | "we spent ⅔ of the week's budget on a cancelled line" | **attribution** — whose consumption |

Nothing was added here that a reader would call an inference: "consumed" and
"by us" each arrived as part of a restatement. The rule this produced lives in
the [experiment playbook](../../../docs/experiments/playbook.md); this section
records only what happened.

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
3. **Replay can be SSE-semantic, not byte-faithful.**
   Two distinct losses, and an earlier draft named only the second. **The
   forward path already normalizes:** `bufio.Scanner` plus `line + "\n"`
   (:537, :560) drops a trailing `\r`, because `ScanLines` strips it — verified
   by round-tripping `"event: x\r\ndata: {…}\r\n\r\n"` through the same
   construction, whose output is LF-only and **not** byte-identical to the
   input. So the proxy does not forward raw bytes verbatim today, and storing
   the lines it sees would not recover them; byte-faithfulness would require
   capturing **below** the line-scanning layer. Separately,
   `sseEventsToMessage` is a lossy summary built for the record, so a replay
   reconstructed from it is further from the wire still. Whether the upstream
   actually emits CRLF is **not verified** — the point holds either way, since
   the design cannot claim byte-faithfulness from this layer.
4. **Only the SSE path matters.** Every request in the captured wire log sets
   `stream: true` (71 of 71 records, `baseline-navidrome-rollout-0`), so
   `handleBuffered` is not on the hot path for this design.
5. **Memory is not a constraint.** `max_tokens` is 64000 in the observed
   requests; measured output per request is mean 289, median 161, p90 637,
   max 2200 tokens (71 requests). The existing scanner already allows 4 MB per
   line (:530).

### Is the interruption observable to the Claude Code client?

- **Verified:** the client always requests streaming (71/71 above), so the
  change is from incremental delivery to one delivery at end of turn.
- **`WriteTimeout: 10m` (:880) is a constraint, not evidence of tolerance.** I
  had it backwards. Per Go's `net/http.Server` documentation the timer is reset
  when the request header is read and bounds *response writes*, so **one
  deadline covers the withheld turn and every rejected resample together**. A
  long generation, or a handful of rejections, can expire it before the first
  response byte is written — a failure mode that does not exist today, because
  today the first bytes leave immediately.
- **Not verified:** whether Claude Code applies its own first-byte or idle
  timeout, and whether anything in it behaves differently when a whole turn
  arrives at once. I found no reading for this and did not run the client.
- **Weak bound from data:** across the pilot, wall time averages
  **11.4 s per request** (8436 s / 740 requests), including tool execution
  between turns. **This is a mean and bounds nothing** — the relevant quantity
  for a 10-minute deadline is the tail, which was not recorded. It is offered
  as an order of magnitude and must not be read as headroom.
- **Not verified:** server and client behaviour under buffered, repeated turns.
  Nothing here has been exercised; it needs a run, not an argument.

## 3. Resampling against the upstream

- **Mechanically it is the existing code path.** Re-issuing the same buffered
  body is what the retry loop already does (:685); what is new is the
  *trigger* — a response that arrived intact and was rejected, rather than a
  transport error.
> **Plan B's central premise is unverified.** Everything below concerns the
> *upstream's* behaviour, and I have observed only the **caller's** side of it.
> If an identical re-send is deterministic, or deduplicated, then a resample
> returns the rejected response again and **the mechanism does not work at all**
> — not "costs more", but does not function. This is the single assumption on
> which the whole design rests, and it is the one with the least evidence
> behind it.

- **What is observed (caller side):** the request bodies carry no
  `temperature`, no `top_p`, no seed (body keys: `max_tokens`, `messages`,
  `metadata`, `model`, `output_config`, `provider`, `stream`, `system`,
  `thinking`, `tools`); `thinking` is `{"type": "disabled"}`.
- **What that does *not* establish — not verified.** Absent sampling
  parameters say what the caller did not request; they say nothing about the
  target's default decoding, and nothing about whether two identical completed
  calls are independent draws. A pinned target and version would be needed even
  to state the property, and the observed run went to
  `anthropic/claude-sonnet-5` via an OpenRouter-shaped body — a different
  target from the pilot's.
- **Deduplication — not verified.** `grep -in "idempot"` over
  `reverse_proxy.go` returns 0 hits, and Anthropic's public docs (searched
  2026-09-01) document idempotency for *Message Batches*, not `/v1/messages`.
  Neither reading constrains the upstream: **absence of a documented key is not
  absence of deduplication**, and a grep of this proxy says nothing about a
  server it does not implement.
- **Billing of a completed but unforwarded stream — not verified.** Whether a
  rejected sample is charged is an upstream **policy**. I previously called it
  structural and then inferred it from the documented refusal-only exemption;
  neither stands. **Confirming it needs the provider's billing line items, or a
  controlled observation on an account nothing else is using** — this account is
  shared, so a usage delta cannot be attributed to particular calls.

**What a same-body pair check answers, and the only thing it answers:** whether
the upstream returns the same output again. That rules out **one necessary
blocker** — strict determinism or deduplication, either of which would make
resampling impossible. It is **not** the question of whether the mechanism
works: divergence is necessary, not sufficient. If every variant an upstream
produces is still rejected by the oracle, plan B never reaches an accepted turn,
and a divergent pair says nothing about that. So the check can **falsify** the
design; passing it establishes nothing. It also **bears on the cost model not at
all**, because no oracle and no judgement are involved in it.

`p`, the oracle's rejection rate, is a different quantity and is measured only
by an oracle judging real steps. It remains among the unknowns. **No pair study
of any size may be cited in support of the cost model.**

This document proposes running nothing.
- **The binding limit is the subscription window, not a per-minute quota** —
  `seven_day` is the type that reached `allowed_warning`, while `five_hour`
  stayed at `allowed`. The reading is in the callout at the top of this
  document; how much window a resampling design would consume is the open
  question stated there.

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

`1/(1-p)` is the expected number of requests per accepted step **only under a
geometric assumption**, and a stationary *marginal* `p` is not enough for it.
What it requires is that the acceptance probability be **constant conditional on
the history of rejections at that step** — that the second resample after a
rejection is no more and no less likely to be accepted than the first. If a
rejected sample makes the next one more likely to resemble it, the count is not
geometric and this expression understates the cost, possibly without bound. That
is a strictly stronger condition than the marginal rate, it is untested, and it
is related to the independence question retracted in §3.

Stated with that assumption made explicit rather than buried:

| approach | cost multiplier over an unfiltered rollout |
| --- | --- |
| per-step resampling at rate `p`, **assuming geometric counts** | `1/(1-p)` — 2× at `p = 0.5`, 5× at `p = 0.8` |
| trace-level best-of-N | `N` |

so per-step is cheaper whenever `1/(1-p) < N`. At the pilot's measured price a
2× multiplier is **$2.19 per kept trace**; best-of-4 is **$4.37**.

Three things this model does **not** include, all of which push the real number
up:

- **The oracle's own cost.** If judging a step is itself an LLM call, it adds
  roughly one request per step — which on its own is close to a 2× multiplier
  before any resampling. Not modelled here.
- **Two separate unmeasured assumptions, not one.** *Across* steps: rejecting a
  step changes the trajectory, so later steps are not the steps that would have
  occurred. *Within* a step: the geometric count above needs acceptance to be
  constant **conditional on the rejections already seen at that step**. The
  step-level accept rate has **never been measured** — no oracle has ever run at
  step granularity — so neither assumption has evidence either way. **Not
  verified.**
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

**Plan B is therefore not a purely engineering question.** Item 1 is an
owner decision of record, and superseding it takes **a new ADR**, not an
implementation. That belongs in the premises of any comparison between
candidate mechanisms — otherwise the comparison happens at the wrong level,
weighing engineering cost against engineering cost while one side also carries a
decision that has to be reopened.

## 7. A question every process-supervision mechanism has to answer

**This section is not about plan B.** It states a consequence that follows from
intervening in the process at all, and any candidate mechanism has to answer it.

An uninterfered rollout is a sample from the model's own policy. **Any mechanism
that intervenes during the process produces a sample from a different
distribution** — the policy *conditioned on whatever the mechanism accepted,
stopped, or steered*. Every token can still be the model's own, and the
delivered conversation can still be unedited (spec §6), so this is **neither a
violation nor free**.

What it costs is legibility. §14's **policy stamp** exists precisely so that
contamination is recorded rather than inferred, and today's stamps describe
oracle-guided **injection** — the mechanism that was terminated. A run produced
under any new mechanism is contaminated by a *different* mechanism, so:

- each mechanism needs its **own stamp**, naming what conditioned the sample;
- runs under different mechanisms are **not poolable**, by the same rule that
  already makes aggregation across differing stamps an error rather than a
  warning (§14.1);
- and the stamp has to distinguish **mechanisms**, not merely mark
  "not-uninterfered" — otherwise the two are pooled by the very field meant to
  keep them apart.

**A label of insufficient granularity is worse than no label**, and the reason
is not that it carries less information. It **announces that the distinction has
been made**, so nobody makes it. A missing stamp is an open question that
someone eventually asks; a stamp reading "intervened" is an answered one. This
is the same object as a check that cannot fail — an announcement that the work
was done, whose effect is that the work is not done — with metadata as the
carrier instead of an assertion.

Whichever mechanism is chosen, this work exists and is not part of its
implementation cost as usually estimated.

### A note on how the §5 collision was missed, and the rule that catches it

Worth recording because the evidence was **read and then not re-read**, not
missed. §5's table was read in full while answering a different question — *is
the production default per-step or whole-trace rejection?* — and the "not the
proxy" row was in that output. The red-line assertion followed a few lines
later, by which point the question had become *does B cross a red line?*, under
which that row is directly on point.

So the accurate description is not that the row was unread, or that the
question had not yet formed: evidence taken under one question was reused under
another. The rule drawn from it is in the
[experiment playbook](../../../docs/experiments/playbook.md).

### Three of these happened in one document, and they are one class

Separately each looks like an oversight. Together they are a single failure
mode: **one reading admits two modalities, and ordinary language does not
distinguish them.**

| instance | the two readings the same words support |
| --- | --- |
| `Beta(2,2)` rationale | a statement about the **parameter** θ vs one about the **observation** `c` — *"does not assume X"* parses for both |
| §5 red-line check | evidence taken under **question A** reused under **question B** — the same table, the same reader, a few lines apart |
| `seven_day` utilization | a **stock** (level right now) vs a **flow** (what this batch consumed) — *"utilization 0.65"* is true either way |

In all three the sentence stays grammatical and plausible under the wrong
reading, so nothing downstream trips on it — which is why none was caught by
re-reading, and all three were caught by someone asking *which quantity is
this?* The obligation that follows is recorded once, in the
[experiment playbook](../../../docs/experiments/playbook.md), not here.

## 8. What remains unverified

Ordered by what they would cost if they went the wrong way.

- **That an identical re-send is an independent draw** (§3) — the premise plan B
  rests on. If it is false the mechanism does not work, at any price. Everything
  observed about it is caller-side.
- **Whether the upstream deduplicates identical calls**, and **whether a
  completed but unforwarded stream is billed** (§3) — both are upstream
  policies; I have documentation absence and an inference, not observations.
- Claude Code's client-side timeout and its behaviour when a turn arrives all at
  once, and whether the 10-minute write deadline survives a buffered turn plus
  several resamples (§2). Nothing here has been exercised.
- The sandbox's egress rules, which decide whether an external oracle is
  reachable at all (§4).
- **The step-level accept rate** — the single largest driver of the cost model,
  and the one quantity no run of any kind has produced (§5). It is **not
  specific to plan B**: any mechanism that acts at process granularity is priced
  by how often it has to act, so this is a **shared unknown** of the candidates.
  What to do about that is a question for the comparison, not a recommendation
  of this document.
- Prompt-cache behaviour on an immediate identical re-send (§5).
