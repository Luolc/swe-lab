# Position B — proxy-resident per-completion rejection sampling

> **Round-1 brief as submitted, unedited (964 words against A′'s 5,570).** The 1000-word cap the debate skill sets was missing from the briefs that spawned both steelmen; see the *Procedure notes* in [`DEBATE-VERDICT.md`](DEBATE-VERDICT.md) for how the judge handled the asymmetry.

## The design in one paragraph

`cc-reverse-proxy` holds each complete upstream SSE response before Claude Code
can observe it, parses the candidate assistant message, and asks the oracle to
accept, reject, or stay silent. Silence passes through; acceptance replays the
original upstream bytes; rejection discards those bytes and reissues the
identical request. A hard attempt/timeout budget terminates and discards the run
rather than silently accepting a rejected candidate. Every decision and
discarded candidate goes to an audit ledger, while the training conversation
contains only the accepted, actor-generated turns and carries a distinct policy
stamp.

## The case

**Durability is B's strongest case.** A′ is demonstrated behavior of Claude
Code 2.1.257, not a contract: a line queued during work must be absorbed into
the current turn; the binary must assemble the exact `role=system`
`<system-reminder>`; headless and TUI paths must keep matching; and the
collector must distinguish the real agent-loop exchange from TUI quota and
prompt-suggestion exchanges. The TUI comparison can establish only today's
shape equality, not stability. It matched exactly (measured, N=1 per arm, four
arms, same task/correction/proxy) and says nothing about other tasks, timings,
models, or the next binary. The stream already misrenders the interjection as
`user` while the wire says `system`; a TUI proxy capture already contains a
prompt-suggestion user message that never existed in the task conversation.
These are concrete silent failure modes: an update can still deliver the
correction while changing the training example.

A′ therefore needs a fail-closed wire conformance suite at every Claude Code
upgrade: no-interjection controls, mid-turn interjections through both front
ends, exact wrapper comparison, a directional positive control, and explicit
exclusion of auxiliary exchanges. B's semantic dependency is narrower: the
HTTP request and SSE response that Claude Code already consumes. The proxy
already fully buffers the request body, reissues it on transport failure,
accumulates SSE events, and assembles a message at `stop_reason`. B changes the
ordering: defer the response head, collect, judge, then replay. Store raw bytes
for replay while parsing a side copy; the current Scanner path is only
SSE-semantic and cannot honestly claim byte fidelity. If the HTTP schema moves,
our parser can fail at a version-controlled boundary instead of silently
changing an undocumented conversation assembly path.

B also enforces rather than persuades. A′ lets a drifting completion enter the
actor's state and asks the actor to recover; B prevents a rejected tool call
from executing or becoming history. The accepted trace still consists only of
actor-generated assistant tokens and real tool results. The guidebook pilot can
establish existence of local judgments, not a rejection rate or general oracle
quality. Measured on N=2 traces and 67 parsed steps, this guidebook/judge
produced 20 reviewable adjudications and four rejections; two caught the
guidebook's exact trap. B converts such a judgment into a gate; silence remains
acceptance.

Granularity is conceded. The proxy call-count comparison can reveal overhead
only in its tested arms; it measured zero additional API requests for mid-turn
A′ (N=1 injected arm and N=1 control arm), and its TUI shape passes both
training criteria. B gets no credit for intervening more finely.

Provenance does not disappear. B needs an out-of-band rejection ledger and a
mechanism-specific policy stamp. If the owner additionally requires an in-band
record, `isSynthetic:true` costs B extra non-actor trace text; `origin:human`
costs an unsupported authorship assertion (its wire effect is unmeasured); and
omitting `origin` costs attribution. Those are owner choices, not a choice made
here.

## Costs, with units

Runtime cost is **unmeasured and presently has no finite empirical bound**.
Structurally, every candidate costs one oracle judgment; every rejection costs
one additional full upstream completion and its latency; every accepted
completion is withheld for judge latency even when the judge is silent. A
historical pilot gives only a unit scale, not B's cost: measured over N=740
requests, the mean request carried 46,837 input tokens including cache traffic,
284 output tokens, and $0.0295 nominal cost. The conditional acceptance process,
cache behavior, billing of discarded streams, steady-state rejection rate,
quota share, and dollars/accepted trace are all unmeasured. No geometric
multiplier is justified. The proxy's measured code-level write deadline is ten
minutes for the withheld generation plus all retries, so timeout behavior must
be redesigned and load-tested.

Engineering cost, in inferred deliverable units, is at least two coordinated
repository changes: proxy buffering/raw replay/oracle gating/budgets/audit, and
swe-lab configuration/capture/policy-stamp integration. Governance adds one new
ADR that supersedes spec §5's proxy ownership decision, with same-PR rewrites of
§5 and §16, plus invariant tests proving rejected bytes never reach the client,
accepted raw bytes are replayed unchanged, and every failure is recorded. No
person-day estimate has been measured.

## Where this side is weakest

B may not exist. Identical resend may be deterministic or deduplicated; then it
returns the rejected completion forever. Even if outputs diverge, the oracle may
reject every variant. This is larger than every durability advantage. Whole-turn
buffering may also trigger an unmeasured Claude Code first-byte/idle timeout,
and placing judgment in the proxy enlarges the blast radius of oracle failure.
Finally, B reverses an explicit owner decision and creates machinery capable of
rewriting assistant turns even though this design promises only selection.

## What evidence would change my mind

A same-body test has one useful direction: any divergent response rules out
strict determinism/deduplication for that exact target and version; passing that
test establishes nothing about eventual acceptance or cost. B needs an
end-to-end observed rejection followed by a distinct oracle-accepted resample,
then representative measurements of attempts, tokens, dollars, cache behavior,
latency tails, and timeout failures per accepted completion. Failure to produce
that existence witness rejects B. Conversely, a documented Claude Code contract
for live interjection and auxiliary-request classification, plus stable
wire-conformance results across upgrades, would erase B's durability advantage;
given A′'s already measured zero-request intervention, I would then prefer A′.
