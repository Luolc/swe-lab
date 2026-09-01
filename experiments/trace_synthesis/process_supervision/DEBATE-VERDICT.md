# Debate verdict — process-supervision delivery (A′ vs B)

> **Read this first. This is an input to a decision, not a decision.**
>
> **When, and on what.** Adjudicated 2026-09-01 by `swelab-debate-judge`
> (read-only, a third model family) on the binding premise sheet
> [`debate-premises.md`](debate-premises.md) and the two position papers
> below. The evidence base is what was merged that day —
> [#304](https://github.com/Luolc/swe-lab/pull/304) (the stdin channel),
> [#305](https://github.com/Luolc/swe-lab/pull/305) (a guidebook as a
> step-level criterion) and [#306](https://github.com/Luolc/swe-lab/pull/306)
> ([`FEASIBILITY-A.md`](FEASIBILITY-A.md)) — plus
> [`FEASIBILITY-B.md`](FEASIBILITY-B.md). The judge re-derived the identity
> claim in §2 from the committed artifacts rather than from either brief.
>
> **The ruling is conditional, and both of its conditions are unrun.** "A′ now"
> means *spend the next engineering there*, gated on a registered compliance
> test that **has not been run**; B is gated on a reject-then-accept witness
> that **has not been run**. No production traces are authorized by this
> document, and neither experiment existed when it was written.
>
> **It changes nothing in the spec by itself.**
> [`spec.md`](../../../docs/trace-synthesis/spec.md) §5's *steer from a hook —
> not the proxy* stands as the decision of record. A verdict is not an ADR:
> changing an attribution decision takes a new ADR, and writing one while both
> gates are unrun would record an unfinished decision as finished.
>
> **The two sides did not write under the same rules, and that is this
> project's fault, not the debater's.** The debate skill caps a round-1 brief at
> 1000 words; the briefs that spawned the two steelmen **omitted that cap**. A′
> submitted 5,570 words, B 964. The judge did not bounce A′ — it weighted
> labeled, re-runnable claims over volume and recorded the asymmetry (see
> *Procedure notes*). Anyone comparing the two papers by weight is comparing an
> artifact of the brief.
>
> **The files.** [`debate-premises.md`](debate-premises.md) is the binding
> shared input (two landing-time corrections are listed in its header);
> [`DEBATE-POSITION-A.md`](DEBATE-POSITION-A.md) and
> [`DEBATE-POSITION-B.md`](DEBATE-POSITION-B.md) are the round-1 briefs as
> submitted, unedited.

Judge: `swelab-debate-judge`. Read-only. Binding input: `debate-premises-v2.md` including `## RESOLVED — the TUI comparison landed`. Positions: `DEBATE-POSITION-A.md`, `DEBATE-POSITION-B.md`.

---

## 1. The ruling

**A′ now as the delivery mechanism to implement, gated on a registered compliance test; do not build B until a reject-then-accept witness exists.**

That is not “ship production traces.” It is: spend the next engineering on stdin injection, not on hold-then-forward resampling, and kill A′ if the compliance gate fails rather than falling through to B.

---

## 2. Why

Axis 2 is closed by the premise sheet. Mid-turn injection is usable, passes (a) and (b), and costs **0 extra actor API requests** relative to a no-injection control. I re-ran the identity claim from the committed artifacts (not from either brief):

| arm | `api_calls` | `agent_loop_calls` | wire messages | `<system-reminder>` | last role | last text digest |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `proxy-control` | 4 | 3 | 6 | 3 | user | — |
| `tui-control` | 6 | 3 | 6 | 3 | user | — |
| `proxy-midturn` | 4 | 3 | 7 | 4 | system | len 440 / sha256 `3ba88726404a76b865ad2296763ac185ab882ee1052fbe2cc452a12cbffb90c8` |
| `tui-midturn` | 6 | 3 | 7 | 4 | system | same |

**Status: measured, N=1 per arm, 4 arms, one task / correction / timing / model.** Selection is now explicit in the artifact (`selected_record_index`, `selection: last agent-loop request`). Digest equality and `texts ==` both held in this re-run. Headless 4=4 and TUI 6=6 are the speaking-cost measurement: the injection adds no actor-loop request.

Given that, the remaining axes:

**Delivery.** A′ is the only side whose channel has been exercised. Boundary injection is clean of the three resume artifacts (measured, N=25 + a positive control that does fire). Mid-turn is the production TUI shape (measured, N=1, now hash-pinned). Spec §6 is satisfied by construction if the trace is built from the **wire** (premise sheet: the wire is the truth; stream capture of mid-turn is a lie). B has never forwarded a judged, resampled turn to Claude Code.

**Cost I can state.** A′ speaking: 0 extra actor requests (measured, N=1). A′ checking: one guidebook-judge call per step, **inferred** ≈ $0.0093/step from PR #305’s corrected $0.643 / 69 judged steps, ≈ $0.34/trace if a trace has the pilot’s 37 requests — mixed populations, labeled inferred, accepted only as an order of magnitude, **not** as a rate. B’s runtime cost is **unmeasured and has no finite empirical bound** (premise sheet item 1; B stated this honestly). I will not pick a side whose cost I cannot write down.

**Silence (axis 3).** A′ correctly attacked the orchestra inference. Checking is symmetric under user ruling 2: both designs run a judge at every checkpoint. The 30% figure is adjudicable-on-unsupervised-traces, not a speak rate (premise sheet item 5). What remains of axis 3 is **latency placement**: A′’s judge is off the actor’s critical path; B withholds every turn, including silent ones, behind the judge and behind one 10-minute `WriteTimeout` covering generation plus every resample (FEASIBILITY-B §2, code-read; client tolerance **unmeasured**). That asymmetry favors A′ and does not need the 30% number.

**Ownership.** Both need an ADR. A′ widens §5’s “hook” letter to “the harness’s own channels” and rewrites “Not a system-reminder” to criterion (b); B **reverses** “not the proxy” and rewrites §16, because hold-then-forward is exactly the machinery §16 names. B also has a genuine ownership advantage A′ admitted: B is not injection, and §5 currently authorizes no production injecting run. That advantage does not pay for building a mechanism that may not function.

**Durability — B’s strongest remaining argument, and it is real.** Everything load-bearing in A′ that is not the documented Agent SDK transport is an internal fold: queued local-tool messages become a `role=system` `<system-reminder>` with a specific wrapper. There is no contract for that fold (A′ concedes this). Measurements are on host 2.1.257; the shipped pin is 2.1.212 (verified: `PINNED_CLAUDE_CODE_VERSION = "2.1.212"`). In-sandbox behavior is unmeasured. Stream vs wire disagreement and TUI prompt-suggestion pollution are already observed silent-failure modes of **our collector**, not of Claude Code. B’s semantic dependency is the HTTP/SSE the client already consumes. I do not treat that as a win for B today, because B’s *existence* dependency (identical re-send is an independent draw; premise sheet item 2) is also undocumented upstream policy, and unlike A′ it is a “does not work at all” risk. A′’s durability exposure is “re-verify the equality on the binary we ship.” That is a cheap conformance suite, not a reason to prefer an untested resampler.

**Training signal.** A′ leaves the drifted step, the visible correction, and the recovery in the conversation — the shape spec §6 argued for. B delivers a conversation with no visible intervention; rejected bytes exist only in a side ledger. Ruling 3 does **not** ban B (B is online, not post-hoc). A′’s adjacency argument is opinion, and I weigh it as opinion: B’s data product is closer to step-level rejection sampling than to process supervision a reader can point at. It is not a disqualifier. It is a reason not to treat B as the same research object.

**The reason I will not pick B, even as a steelman of durability:** I cannot cost it, and it may not function. A forced choice I cannot cost is the thing I was told not to make.

---

## 3. What would change it

Name the measurement, not a vibe.

1. **A registered A′ compliance test fails** (sparse delivery, user-like wording, tag in the body, N ≥ 10 interventions, criterion fixed before the runs). Then A′ produces poisoned traces at the moments that matter — the failure mode that killed the previous injection arm (spec §11; measured 0/3 micro-test, 6/6 steered-rerun refusals, **different channel**, so this is a risk transfer, not a transferred result). A′ is then dead as a data source. That does **not** automatically adopt B.
2. **The four-arm wire check fails inside the sandbox on the binary we actually ship**, with no clean alternative shape (in particular: local tool calls start interrupting the way CHANGELOG 2.1.246 already describes for MCP). Then A′’s (b) pass is about the wrong artifact.
3. **B produces a reject-then-accept witness** on the production target: an identical re-send that the same guidebook judge accepts, with attempts-to-accept recorded, **and** Claude Code accepts a fully-buffered turn under a redesigned deadline. Then B exists, has a first cost number, and the debate reduces to durability vs visible supervision vs judge-before-vs-after-the-tool-result. I would reopen.

Quota attribution (sheet item 3) would not change the ruling. Granularity cannot change it: the axis is closed.

---

## 4. The cheapest single experiment that discriminates

A′’s speaking cost is already measured (0 extra actor requests, N=1). B’s cost and existence are not. The cheapest experiment that can actually split the two is therefore **B’s existence plus a first attempts-to-accept count**, not another A′ shape test and not a bake-off.

**Name: reject-then-accept witness.**

- **Material.** One live cheap actor step through the current Go proxy, same upstream the harness uses. Prefer a step the existing guidebook judge would reject. If a request body from one of PR #305’s four off-track steps is retained, use that; otherwise generate until the judge rejects, cap 20 steps.
- **Procedure.** Keep the request body bytes. Re-issue that **identical** body K=10 times. Run `judge_steps.py` with the same guidebook on each completion. Stop early on the first accept. Do not introduce temperature, seeds, or body mutations — those would answer a different question.
- **Pre-registered reading of the three outcomes** (fix this before running):
  1. All K bit-identical to sample 0 → **B does not function.** Identical re-send is deterministic or deduplicated. Adopt A′ (still gated on compliance). This is the only outcome in which a pair study is allowed to decide anything; a pass of divergence still decides nothing about cost (FEASIBILITY-B §3; sheet item 1).
  2. Outputs diverge and 0/K accepted → B samples and does not gate. On a step that actually needs intervention, B’s cost has **no measured finite bound**. Stay with A′.
  3. First accept at attempt *k* ≤ 10 → B exists. *k* is the first cost observation (extra actor completions per rejected step, this target, this judge, this step). Reopen the debate under §3.3.
- **What this is not.** It is not a steady-state rejection rate. It is not `1/(1-p)`. It is not a claim about cache, billing of discarded streams, or client timeouts — those stay unmeasured unless the same run happens to record them as side data.
- **Budget.** On the order of 11 actor completions of one step plus 11 judge calls. Using the pilot’s mean $0.0295/request (measured, N=740) plus #305’s ≈ $0.0093/judge (inferred), a full K=10 is roughly **$0.50**, not a rollout.
- **Why this, not an A′ compliance trial.** Compliance is A′’s adoption gate and should be run, but it is more expensive (N ≥ 10 interventions, wording controls) and does not speak to B. This experiment is the one that can kill B for a dollar, or give B a number for the first time.

A second experiment, not a substitute: the four-arm check **inside the sandbox on the pinned (or bumped) binary**. That is A′’s artifact-substitution guard. It does not discriminate B.

---

## 5. Errors caught in either brief

### Rejected quantitative claims (not discounted — rejected)

From **A′**:

- **“the oracle would have *acted* on 4/67 steps ≈ 6% … B resamples ≈ 2 times per 37-request trace ≈ $0.06”** — even though the same paragraph calls 4/67 an existence result. PR #305: “No number here may be cited as a per-step rejection rate.” Premise sheet item 5: the 30% (and this 4/67) is first-intervention on unsupervised traces by a guidebook judge, three qualifiers. Premise sheet item 1: the geometric cost model is unmeasured; A′ then multiplies by $0.0295 “under the geometric assumption the sheet says is unmeasured.” **Rule broken: do not launder a “not established” item; a true existence count used as a rate is the week’s failure mode.** I rejected $0.06, “≈ 2 resamples / 37-request trace,” and 6% as a speak rate. The existence of four reviewable rejections remains.

From **B**: none. Runtime cost was stated as unmeasured with no finite empirical bound. No `1/(1-p)`. No quota share.

### Citation-direction / scope errors (not rejected numbers)

- **A′ §2.2, spec §4.** Reason 1 (judge after the observation) is a fair structural contrast: B sees `tool_use` and not the tool result. Reason 2 A′ themselves marked Neutral. “Every argument §4 makes … is an argument for A′ and against B” overclaims their own table. §4 was written against PreToolUse *denial*, not against resampling. Analogy, not a recorded exclusion of B.
- **A′ §3 checking cost ≈ $0.34/trace.** Labeled inferred, arithmetic checks ($0.643/69 ≈ $0.0093; ×37 ≈ $0.34). Populations are mixed: $0.643 is PR #305 (2 traces / 69 judged steps, retries included) and 37 is the honesty-scorer pilot’s requests/attempt. Accepted only as order of magnitude. The premise sheet still prints $0.59; the corrected report is $0.643. I used the corrected figure and noted the sheet lag.
- **A′ “only measured channel that passes (a), (b), spec §6 and the artifact test at once.”** True as a description of the evidence base, not as a proof that B would fail those tests if run. B has not been run.

### Withdrawn-premise check

Neither brief resurrected “mid-turn is dirty because it is not a user turn.” Neither treated granularity as a discriminator. A′ dropped `--max-turns` as load-bearing and said so in §0. B conceded axis 2 in one sentence. **No error here.**

A leftover in the premise sheet itself still calls `--max-turns` “currently the only usable fine-grained path.” RESOLVED outranks that paragraph; both briefs followed RESOLVED.

### Weaknesses sections

Both have one. **A′’s real worst problem is compliance** (the last injection arm’s kill), and they ranked it 4.1, ahead of the pin/container gap. That ranking matches this project’s history; they did not hide durability — they argued it and then restated the pin (2.1.212 vs 2.1.257, 45 releases) and the untested container as 4.2. **B’s real worst problem is existence**, and they led with it: “B may not exist … This is larger than every durability advantage.” I trust both sections.

### Other named errors

- **A′ §2.5 used a not-established cost model while disclosing that it was not established.** Disclosure does not license the number. See rejected claims.
- **B did not undercost the ADR.** Proxy surgery + swe-lab integration + one ADR rewriting §5 and §16 + invariant tests are stated. No person-day estimate, correctly marked unmeasured.
- **B’s brief is thin on spec §4 / visible-vs-hidden**, which is A′’s best non-durability argument. Thin is not a rule break. I did not treat silence as a concession.

---

## TUI identity — does the `records[-1]` incident raise or lower credibility?

**Net: it raises the credibility of this specific identity claim as it now stands, and it lowers the prior on any author-narrative table that is not pinned to an artifact.**

Why raise, for *this* claim:

- The bug was **request selection**, not the wrapper. `records[-1]` on a TUI session is the prompt-suggestion exchange; on a headless session it is the agent-loop. That comparison should **not** have matched. The report’s 6/7 table was the author looking at the right request; the committed evidence rendered 8/9. Evidence and conclusion were different sources. After the fix, I re-ran the comparison on the artifacts: last agent-loop message, both sides `role=system`, `texts ==`, digest len 440 / sha256 `3ba88726404a76b865ad2296763ac185ab882ee1052fbe2cc452a12cbffb90c8`. The identity **survived being pointed at the right object**.
- Selection is now a field in the artifact (`selected_record_index`, `agent_loop_calls`, `excluded_side_calls`), not a prose claim.
- A regression test now fails if a trailing suggestion request is selected, and it asserts the committed 6/7/4 counts and the digest equality. That is the form AGENTS.md requires for an invariant.

Why not treat “the author makes this kind of error” as decisive against the claim:

- That error predicts **mismatched** artifacts (8/9 vs 6/7), which is what review found. It does not predict a false match. A false match would have been the more damaging failure, and it is not what happened.
- “The process catches this kind of error” is the observation that actually occurred: review found it, the builder was changed, a test that would have been red was added, the conclusion did not move. For a claim whose live risk is silent substitution (the same class as FEASIBILITY-A’s redaction incident), a caught substitution plus a pin is more informative than an uncaught clean table.

Scope, still in force: N=1 per arm. Hash equality is strong evidence that **this** assembly path is deterministic for **this** task/correction/timing/model. It is not evidence about variance, the next binary, or the sandbox.

---

## Procedure notes

- **Word limit.** Debate skill says round-1 ≤1000 words. Orchestra’s steelman briefs did not. A′ is 5570 words; B is 964. I did not bounce A′. I weighted labeled, re-runnable claims over volume. Named so it is not a silent pass.
- **Re-run.** TUI/headless identity: independent load of the four `evidence.json` files from `origin/exp/stream-json-input`; counts, roles, and sha256 verified as in §2. Pin version verified on `origin/main` (`2.1.212`). A′ arithmetic for the **rejected** $0.06 chain also recomputed (4/67×37 ≈ 2.21; 2×$0.0295 = $0.059) and then discarded as a rate. I did not re-run Claude Code, the guidebook judge, or B’s same-body test — none of those is a committed script attached to a brief claim I needed in order to rule.
- **Documentary claims I did not re-fetch:** Agent SDK streaming-input page, `claude-agent-sdk-python` `subprocess_cli.py`, CHANGELOG 2.1.246 / 2.1.234 / 2.1.257. Treated as labeled [D], not as measurements.
- No repo files were modified, committed, or pushed.
