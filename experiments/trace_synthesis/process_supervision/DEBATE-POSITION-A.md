# Position A′ — deliver the correction as a user message on the stdin of the live `claude -p --input-format stream-json` process

> **Round-1 brief as submitted, unedited (5,570 words against B's 964).** The 1000-word cap the debate skill sets was missing from the briefs that spawned both steelmen; see the *Procedure notes* in [`DEBATE-VERDICT.md`](DEBATE-VERDICT.md) for how the judge handled the asymmetry.

Author: `swelab-debate-a` (steelman for A′). Written 2026-09-01 against
`debate-premises-v2.md` **including its `## RESOLVED` section**. Where this
document and any report disagree, the premise sheet wins; where the brief that
spawned me disagrees with the sheet, the sheet wins too — see §0.

Label key, used on every number: **[M]** measured, with N and design ·
**[I]** inferred (arithmetic or reading on a measurement, stated) ·
**[D]** documentary (a document read on the date given; a doc is not a
measurement) · **[U]** unmeasured.

---

## 0. Premise alignment — what changed under my feet, and what I dropped

My brief said "checkpointing with `--max-turns`". The premise sheet's RESOLVED
section closes that: a mid-turn stdin line is the production shape [M: N=1 per
arm, 4 arms, byte-identical wrapper], so segmentation is unnecessary. **This
brief argues A′ without `--max-turns`.** Two consequences I own rather than
bury:

- The `--max-turns` / `error_max_turns` / `event_stream_outcome` chain the brief
  asked me to address is **no longer on A′'s path**. I still address it (§2.7),
  because part of it is a latent collector defect that survives regardless.
- A′ now depends on a **more internal** assembly path (the "user sent a new
  message while you were working" fold) than the clean boundary turn did. That
  makes the durability argument (§2.6) harder, not easier, and I argue it on
  the harder version.

I do **not** resurrect the withdrawn "mid-turn is dirty because it is not a
user turn" reasoning, in either direction: the criterion is (a) no loss on
tokens the actor did not write, (b) a context shape that occurs at inference.

---

## 1. The design, in one paragraph

The actor runs exactly as today's rollout runs it — the pinned, checksummed
Claude Code binary inside the instance container, API traffic through the
in-sandbox Go capture proxy (ADR-0012) — with three changes to the invocation
script: `--input-format stream-json` so stdin is a live channel, stdin fed from
a FIFO instead of `< prompt.txt`, and stdout kept as `stream-json` in proxy
mode. A host-side supervisor (the steered re-run's `supervisor.py` with the
hook half removed) tails the proxy log through the bind-mounted workspace — the
same file-drop transport the steered re-run already uses because the host
firewall denies inbound from the Docker bridge — judges each completed
assistant message against the guidebook **asynchronously, off the actor's
critical path**, and, rarely (user ruling 2), drops one correction file; an
in-container relay appends it to the FIFO as one stream-json `user` line
carrying a `<oracle_hint>`-style tag in its body (spec §11 criterion 2) and
whichever provenance field the user chooses (§5 below). Because the actor is
almost always mid-turn, Claude Code absorbs the line into the running turn and
delivers it to the model inside its own `<system-reminder>` — "The user sent a
new message while you were working: …" — which is byte-identical to what the
interactive TUI produces when a person types while the agent works [M: N=1 per
arm, 4 arms]. No stop, no `--resume`, no segmenting, no `control_request`, and
**zero additional API requests** [M: proxied mid-turn arm 4 calls = proxied
control 4; TUI mid-turn 6 = TUI control 6; N=1 each]. The training trace is the
wire capture converted by `proxy_log_to_conversation`; the stream rendering is
never used for this channel (premise sheet: "the wire is the truth").

---

## 2. The case

### 2.1 It is the only *measured* channel that passes (a), (b), spec §6 and the artifact test at once

| requirement | A′ mid-turn | status |
| --- | --- | --- |
| (a) no loss on tokens the actor did not write | the correction is a `system`-role context message; no assistant turn is synthesized | [M: wire role sequence, proxy-midturn + tui-midturn, N=1 each] |
| (b) shape occurs at inference | TUI mid-turn interjection produces the same 7-message, 4-reminder wire with a `==`-identical trailing `system` message | [M: N=1 per arm, 4 arms] — **scope:** one task, one correction text, one interjection timing (during a 30 s Bash), one model; says nothing about variance |
| the three resume artifacts absent | `Continue from where you left off.` / `No response requested.` absent in transcript and stream across 25 non-resume runs; the grep is two-sided (fires on the SIGKILL+`--resume` positive control) | [M: N=25 + 1 positive control] |
| nothing else added on the wire | reminder count 4 vs control 3 — exactly the one wrapper carrying our text; boundary arms 3 vs 3 | [M: N=1 proxied mid-turn; N=1 proxied boundary with 2 injections] |
| §6 "conversation, unedited" | nothing removed; the one thing added is what the actor actually received, and it stays in the trace | by construction, given proxy capture |
| no fabricated user-role speech | contrast: `control_request` interrupt writes "The user doesn't want to proceed…" and "[Request interrupted by user for tool use]" — excluded by the sheet | [M: N=1] |

**What this table is about and not about.** It is about *delivery shape* under
Claude Code 2.1.257 on the host binary. It is **not** about whether the actor
*complies* (§4.1), **not** about the pinned 2.1.212 rollout binary (§2.6, §4.2),
and **not** measured inside the container (#304 §11: "In-sandbox behavior …
I did not verify it").

### 2.2 It is the design the spec's own reasoning already argued for; B is the one §4 argues against

Spec §4 gives three structural reasons for steering **after** the tool result:

1. *"Judging after the observation is a far easier problem than predicting
   before it."* **B judges before execution by construction**: the proxy holds
   the completed assistant message — thinking plus `tool_use` blocks — before
   the client has run the tool, and must decide "will this call turn out
   badly?" blind. A′ judges once the tool result is on the wire, and its
   correction lands in the request that carries that result or the one after
   (§2.3 timing). Every argument §4 makes for post-hoc judgment is an argument
   for A′'s seam and against B's.
2. *"No fabricated tool failures."* Neither design fabricates one; B resamples
   rather than denies (FEASIBILITY-B §6). Neutral.
3. *"A user turn is a natural conversational shape. People really do interrupt
   an agent with 'I think you're going the wrong way'."* That sentence
   describes A′ mid-turn literally, and the TUI measurement is what turns it
   from an assertion into a shape match.

Spec §5's owner row *"Steer from a Claude Code hook — not the proxy, not our
own agent loop"* carries the rationale *"hug the harness we actually want
traces of."* A′ steers through the harness's **own user-input channel** — the
one the Agent SDK is built on (§2.6) — which is neither of the two excluded
things and is the most literal way to hug the harness. B builds steering into
the component the row excludes **by name**. Both need spec text changed (§2.8);
only one needs a recorded exclusion reversed.

### 2.3 Speaking costs nothing, checking costs nothing on the critical path

**Speaking.** A mid-turn message adds **0 API requests** [M: see §1] and a
context increment of the wrapper plus the correction — the wrapper is ~70 words
[D: quoted verbatim in #304 §5/§14.3]; appended at the tail of the prefix, so
prompt cache is preserved for everything before it [I: from the boundary case's
95–575 cache-creation tokens per injected turn, M N=2 turns; the mid-turn case's
own cache delta is U]. For comparison, a **boundary** injection after task
completion costs +1 request at $0.039 / $0.049 [M: N=2 turns] — A′ never does
that (the supervisor does not write after the terminal `result`; §2.7).

**Checking.** The judge reads the proxy log, which the in-sandbox proxy already
writes; nothing is added to the actor's request path and the actor never waits.
Judge cost per step: $0.643 over 69 judged steps [M: PR #305, sonnet-5 via
OpenRouter, retries included] ≈ **$0.0093 per step** [I: division]. At the
pilot's 37 requests per trace [M: 740 requests / 20 attempts, identity] that is
≈ **$0.34 per trace** [I: multiplication; the judge's input in #305 was the
step plus up to 8 summarized preceding steps, so a judge with a different
context window costs differently], against a rollout's $1.093 mean / $0.698
median [M: 20 pilot attempts]. This checking cost is **the same for B** — both
designs run the judge on every step (user ruling 2). What differs:

- **A′'s judge is asynchronous.** The actor proceeds while the judge thinks; a
  slow judge costs *lag*, not wall time.
- **B's judge is on the critical path of every one of ~37 requests per trace**
  (FEASIBILITY-B §4: "one round trip per step, on the critical path of every
  turn"), and the held turn plus every resample share **one 10-minute
  `WriteTimeout`** (FEASIBILITY-B §2, code-read), against a client whose
  tolerance for a whole turn arriving at once is **unverified** (B §2).

**When the correction lands.** Judged from the measured fold placement [M: a
line written ~2 s into a 30 s Bash call arrived as the trailing `system`
message of the *next* request, N=3 headless + N=1 TUI — in all four the next
request was also the last, so depth is U]: if the judge decides from assistant
message *k* alone, its message reaches the model in the request that produces
*k+1*, alongside *k*'s tool results — the actor sees both the outcome of what it
did and the correction. If the judge waits for *k*'s results (the §4 posture),
the correction reaches the request producing *k+2*. Either is "after each
assistant message, give or take one", which user ruling 1 accepts; the cost of
the lag is §4.3.

### 2.4 The trace carries the supervision; B's trace hides it

Under A′ a corrected trace contains: the drifted step, the visible correction,
and the actor's recovery — every assistant token justified by visible context,
which is exactly spec §6's argument for keeping hints. Under B a corrected trace
contains **no trace of the correction**: the rejected sample never reaches the
actor, so the delivered conversation is a clean trajectory whose *form* is
indistinguishable from step-level best-of-N. Three consequences:

- **Legibility.** A′'s intervention is auditable in the trace itself; B's lives
  only in a side log, and spec §11 already records (measured, the steered
  re-run) that a host-side log stops without recording its own death. B needs
  the three-way reconciliation §11 demands *more* than A′ does, because under
  B nothing in the trace can contradict a lying log.
- **What the model learns.** A′ trains "respond well to a redirect you can
  see" — an inference-time skill, since users do interject (§4 reason 3). B
  trains on filtered steps. Whether one is better SFT data is a research
  question this debate cannot settle; what I can say is that only A′'s data
  contains a *process-supervision signal a reader can point at*.
- **Adjacency to ruling 3.** B is online, not post-hoc, so ruling 3 does not
  exclude it. But its **data product** is step-granular rejection sampling, and
  the user has classed rejection sampling as a known-good baseline rather than
  research. This is my argument, not a ruling; the judge should weigh it as
  such.

### 2.5 Axis 3 (silence): the orchestra's inference is half right, and the half that is wrong favors A′ less than it thinks

The inference: "B runs the oracle on every completion to obtain an opinion 30%
of the time." Two corrections:

- **Checking is symmetric.** A′ also judges every step (ruling 2). Silence
  costs both designs one judge call per step [I: $0.0093, above]. The
  asymmetry is not tokens; it is that B's silent check sits on the critical
  path and A′'s does not (§2.3).
- **30% is the adjudicable rate, not the speak rate.** Of 20 adjudicable steps,
  16 were on-track — silence in effect. The oracle would have *acted* on
  4 / 67 steps ≈ 6% [M: N=2 traces, first-intervention on unsupervised traces
  by a guidebook judge — three qualifiers, all of which change the number; this
  is an existence result, not a rate]. At that density B resamples ≈ 2 times
  per 37-request trace ≈ $0.06 [I: 2 × $0.0295, **under the geometric
  assumption the sheet says is unmeasured**]. So B's *token* cost of speaking is
  small too. **The honest statement of axis 3 is that neither design pays much
  for silence in dollars; A′ pays nothing in latency and B pays the judge's
  latency on every step.** I decline the stronger version of the inference.

### 2.6 Durability — the strongest argument against A′, answered head-on

The claim: "everything A′ relies on is undocumented CLI behavior in a binary
that ships updates constantly; B depends only on the HTTP API." I answer it in
five parts and concede the sixth.

**(i) What A′ actually depends on, itemized.**

| dependency | status | evidence |
| --- | --- | --- |
| `--input-format stream-json` exists and accepts NDJSON `user` lines | **documented flag** | [M: `claude --help` on this box, 2.1.257, line 117]; [M: 26 sessions, none rejected] |
| a live process keeps reading stdin (does not consume once and exit) | **documented SDK behavior** — "Streaming Input Mode (Recommended) … a long lived process that takes in user input … **Queued messages**: send multiple messages that process sequentially, with ability to interrupt" | [D: code.claude.com Agent SDK "Streaming Input" page, read 2026-09-01]; [M: 5 multi-message sessions] |
| the SDK's own transport is this exact channel | the Python Agent SDK spawns `claude` with `--input-format stream-json --output-format stream-json --verbose` and writes NDJSON to stdin; `MINIMUM_CLAUDE_CODE_VERSION = "2.0.0"` | [D: `claude-agent-sdk-python` `subprocess_cli.py`, main, read 2026-09-01] |
| a message during a **local** tool call queues rather than interrupts | **measured, not documented** | [M: N=3, 30 s Bash]; the 2.1.246 changelog shows the opposite for **MCP** calls — see (vi) |
| the queued message is folded as a `system` `<system-reminder>` with that wrapper text | **internal assembly path; no contract** | [M: N=3 headless + N=1 TUI] |
| headless fold == TUI fold | **measured equality** | [M: N=1 per arm, 4 arms, `==`] |
| absence of the resume/interrupt artifacts | a negative property | [M: two-sided grep, 25 runs + positive control] |
| `--replay-user-messages` (driver convenience only; not needed for the trace) | documented flag | [M: `--help` line 176] |

So "everything undocumented" is false as stated. The transport and its
multi-message semantics are the **Agent SDK's documented, recommended mode**,
whose breakage would break every SDK application. What is genuinely
contract-free is the **fold**: its role, its wrapper text, and its equality
with the TUI. That is the real exposure, and I argue it in (ii)–(v).

**(ii) The fold is what we *want* to track, so a change to it is not a silent
failure but a re-asked question.** Criterion (b) asks whether the trace's shape
is the deployed harness's shape. A′'s shape is *defined* as "what Claude Code
does with a mid-turn user message." If a release changes the fold, the
inference distribution changes **with it** — for the TUI and for A′ together,
because they share the assembly path (that is what byte-identity says [M: N=1
per arm]). What must be re-verified per release is therefore the *equality*,
not a fixed shape. B has the mirror-image property: its shape never moves, so
B never has to ask (b) — but B's actor is **the same binary**, and B's
untested dependency (client tolerance of a whole turn delivered at once, B §2)
is also a per-release question.

**(iii) The binary is pinned and checksummed, so "ships updates constantly"
does not reach a rollout batch.** `binary.py` pins `PINNED_CLAUDE_CODE_VERSION`,
downloads one native binary through Anthropic's release manifest, verifies its
sha256, and says "bump deliberately" [D: `src/swe_lab/harnesses/claude_code/binary.py`].
Every run records its version. Nothing moves under a batch unless we move it.

**(iv) The conformance check exists, is cheap, and gates the bump.** The #304
harness (`driver.py` + `evidence.py` + `analyze.py`, on branch
`exp/stream-json-input`) already runs the four arms and the two-sided artifact
greps. Cost: $2.93 / 26 headless sessions ≈ $0.11 per session [M; I for the
mean]; the four-arm check is ≈ $0.25 of headless spend plus two unpriced TUI
sessions [I], minutes of wall time. A version bump that breaks equality is
caught *before* a trace is produced, and the two failure directions are both
benign-or-visible: if a release starts delivering queued messages as standalone
user turns at the next boundary, that shape is **also measured clean** [M: 21
boundary runs, 3 vs 3] and A′ degrades to a different clean shape; if a release
starts *interrupting* local tool calls, the shape may acquire interrupt
artifacts and the check goes red. There is no failure direction that produces a
wrong trace silently, given the check runs.

**(v) "B depends only on the HTTP API" is a scope error of the kind this week
was about.** B depends on the documented **format** of `/v1/messages`, and on
four **undocumented upstream policies** and one **client behavior** that
FEASIBILITY-B itself lists as unverified: whether an identical re-send is an
independent draw (the existence risk — "does not work at all" if not); whether
identical calls are deduplicated (public docs document idempotency for Batches,
not `/v1/messages`); whether a completed-but-unforwarded stream is billed;
whether the prompt cache behaves on an immediate re-send; and whether Claude
Code tolerates buffered whole-turn delivery. The first two are *existence*
questions; none of A′'s dependencies is. The production harness, meanwhile,
**already** rests on an undocumented CLI behavior for its safety guard:
`--max-turns` is not in `--help` on 2.1.257 [M: `--help` grep] and the harness
comment records it was "Undocumented in --help on 2.1.220, but accepted"
[D: `harness.py`]. B inherits that dependency unchanged.

**(vi) What I concede.** Three things, plainly:

1. **The pinned rollout binary is 2.1.212; every A′ measurement is on 2.1.257
   — 45 releases apart** [D: `binary.py`; M: run `meta.json`]. Nothing in this
   brief is known to hold on the pinned binary. Adopting A′ means bumping the
   pin and re-running the headless smoke the harness needs plus the four-arm
   check. That is a cost (§3), and until it is paid A′ is *measured on a
   binary we do not ship*.
2. **The direction of travel is toward interrupting.** CHANGELOG 2.1.246: "Fixed
   MCP tool calls interrupted by an incoming message in headless/remote
   sessions being reported to the model as 'completed with no output' instead
   of an explicit interrupted error" [D: `anthropics/claude-code` CHANGELOG.md,
   read 2026-09-01]. So for **MCP** tools an incoming stdin line already
   interrupts in headless sessions, with a wire shape nobody has measured. A′
   is safe only for local tools (Bash/Read/Edit…) [M: N=3, Bash] — a rollout
   running A′ must load **no MCP servers**, or measure that shape first. The
   harness pins a fresh `CLAUDE_CONFIG_DIR` and passes no `--mcp-config`, so
   the default is none [I: from `harness.py`; U: not verified inside a run].
   Across the 46 releases 2.1.212–2.1.257 I found one entry touching
   `--input-format stream-json` directly (2.1.257, about client-injected
   *assistant* tool calls, not user messages) and one moving other
   between-turn content *into* `<system-reminder>` tags (2.1.234) [D: same
   file] — the surface moves, in small steps, roughly once a month.
3. **No wire contract exists for the fold, and none will.** The SDK docs promise
   queueing; they do not promise a role or a wrapper. A′'s (b) pass is an
   empirical equality re-verified per release, not a guarantee. If the user
   wants a guarantee, A′ cannot give one, and neither can B on its existence
   questions.

### 2.7 The `event_stream_outcome` defect — off the path, but not gone

With no segmentation, a mid-turn message creates **no `result` event** [M:
N=3, "No second `result` event ever arrived", waited 120 s], so a run still ends
with exactly one terminal `result` — the runaway guard's `error_max_turns`, a
`success`, or an error — and `event_stream_outcome`'s last-`result` read stays
correct **for A′**. Two things remain true and belong in the adopting PR:

- The supervisor must **never write after the terminal `result`**: a post-task
  line opens a new turn with its own `result` [M: boundary arms, 3 stdin
  messages → 3 results], which then becomes the run's outcome. Enforce it: the
  collector asserts exactly one `result` per A′ trace, or folds them.
- The masking bug itself — an `error_during_execution` hidden by a later
  `success` [M: proxy-interrupt, N=1] — is a latent collector defect independent
  of any mechanism. Fixing it (fold, or fail on more than one `result`) is
  cheap and is the same fix either way.

Also for the adopting PR: in proxy mode the harness today discards stdout and
asks for `--output-format json` [D: `harness.py`]; A′ needs `stream-json` kept,
both so the outcome classifier has its input and so the driver can see events
— one line.

### 2.8 Ownership — what text each side has to change

Both designs collide with spec §5 as written; the collisions are not the same
size.

| | A′ | B |
| --- | --- | --- |
| §5 "Steer from a Claude Code hook — not the proxy, not our own agent loop" | steers from neither excluded thing; the row's **rationale** ("hug the harness") is satisfied; the row's **letter** ("a hook") is not → widen the row to "through the harness's own channels (hook or SDK stdin)" | **reverses** the exclusion by name → new ADR, rewrite the row and its rationale |
| §5 "Not a system-reminder" (rationale: *ours* would be indistinguishable from machine noise) | the reminder is the **platform's**, attributes the text to the user, and is the production shape; still, the row bans the channel by name → rewrite the row to state criterion (b) | unaffected |
| §16 "a proxy-based design that **could** rewrite the assistant turn is a different design" | unaffected | B builds exactly the hold-then-forward machinery §16 describes (B §6 item 2) → rewrite §16 |
| §12 "No banned channel is reachable in a hook response" | no hook response exists → row unaffected | unaffected |
| §5 preamble: "nothing in this section authorizes a production run that injects" | A′ **is** injection; the terminated arm's kill was on credibility of the tool-result channel → the reopening must be recorded (ADR) and A′ inherits the credibility question (§4.1) | B is not injection; unaffected — **this is B's genuine advantage on ownership** |

Net: A′ needs one ADR that *widens* two rows and records the reopening; B needs
one ADR that *reverses* a recorded exclusion and rewrites §16. Both are real;
the sheet is right that costing B without its ADR undercosts it, and the same
sentence applies to A′.

---

## 3. Costs, with units

| item | A′ | unit / status |
| --- | --- | --- |
| speaking, per intervention | 0 API requests; ~70-word wrapper + correction in context; cache preserved before the tail | requests [M N=1 proxied + N=1 TUI]; words [D]; cache [I] |
| checking, per step | 1 judge call ≈ $0.0093; 0 actor requests; 0 s on the actor's path | $ [I from M $0.643/69]; path [by construction] |
| checking, per trace | ≈ $0.34 at 37 steps, ≈ 31 % of a $1.093 mean rollout | $ [I]; both inputs M |
| quota | judge on OpenRouter (separate account, as in #305 and the steered re-run); **0 extra actor requests** on the actor's account | structural; attribution of the actor's own window is U (sheet item 3) |
| plumbing | invocation script: FIFO + relay (~20 lines); harness: 2 flags + keep stdout (~5 lines); supervisor: reuse `steered_rerun/supervisor.py` poller + file drop, replace hook I/O with proxy-log tail + FIFO write (~100–150 lines); collector: single-`result` assertion (~10 lines) | lines [I: my estimate]; days not weeks [I] |
| pin bump | 2.1.212 → ≥ 2.1.257; headless smoke + four-arm conformance check ≈ $0.25 headless + 2 unpriced TUI sessions; minutes | [I from M $0.11/session] |
| spec / ADR | 1 ADR widening two §5 rows and recording the reopening; §5 rewritten in the same PR | 1 ADR + 2 paragraphs |
| converter constraint | proxy capture only (already required by ADR-0012 on the OpenRouter path); inherits `proxy_log_to_conversation`'s last-thread defect (subagent / compaction) — shared with B | 0 new; 1 shared known defect |
| §11/§12 reconciliation (boundary list ⨝ host log ⨝ conversation; gap records) | required; **shared with B**, and B needs it more (§2.4) | not A′-specific |
| policy stamp naming the mechanism (FEASIBILITY-B §7) | required; shared with B | not A′-specific |

For comparison, B's stated costs (from FEASIBILITY-B, which "proposes running
nothing"): per rejection 1 actor request ≈ $0.0295 [M mean] × an unmeasured
multiplier; judge on the critical path of ~37 requests/trace; proxy surgery
(defer `WriteHeader`, split `streamSSE` into collect/replay, new resample
trigger; egress question for an external judge); a client-tolerance test; a
same-body divergence **existence** test; one ADR reversing §5 and rewriting
§16.

---

## 4. Where A′ is weakest

I list these in the order I think the judge should weigh them.

### 4.1 Compliance is unsolved, and it is the axis that killed the previous injection arm

Delivery and compliance are orthogonal (spec §11), and every measurement of
this channel says so again. Observations, **none a rate**: boundary-shaped
corrections accepted 4 / 6 pooled across arms with differing flags [M]; headless
mid-turn 1 / 3 [M]; TUI mid-turn 1 / 1 [M]. Refusals across arms quoted the
correction's own wording — "Correction from the operator" — as a
prompt-injection tell [M, confound stated by the report]. The prior arm died
0 / 3 on a pre-registered credibility test, and the steered re-run's actor
refused 6 / 6 tool-result hints and wrote a security note about tampering
[M: N=1 rollout]. **A refused correction is not a wasted intervention; it is a
poisoned trace** — assistant turns arguing with the user about provenance are a
behavior prior in the data (§11 records this). B has **no compliance problem at
all**: the actor never sees a rejection. This is B's strongest substantive
advantage and A′ cannot argue it away; it can only say:

- ruling 2 removes the trigger the actor named — *repetition* ("appeared in
  EVERY single tool result") — because a sparse supervisor speaks once or twice
  per trace;
- the mid-turn wrapper is the platform's own attribution ("The user sent a new
  message while you were working"), a frame the tool-result channel never had;
  whether it changes compliance is **[U]** — the N=4 mid-turn observations
  cannot be ranked;
- the wording confound is ours to remove — a correction phrased as a user
  phrases one, not as an "operator", is **[U]**.

None of that is evidence. A registered compliance test with realistic wording,
sparse delivery, and N ≥ 10 per arm is the first thing A′ must buy, and if it
fails, A′ fails the way the last arm did.

### 4.2 The fold has no contract, the pin is 45 releases behind the measurement, and the container was never tested

Spelled out in §2.6 (vi). Add: the shapes were measured on the **host** binary
with the operator's user-level `CLAUDE.md` loaded; the rollout runs a pinned
empty `CLAUDE_CONFIG_DIR` inside a container with `IS_SANDBOX=1` [D:
`harness.py`]. #304 says the shapes "should not depend on that, but I did not
verify it." Until the four-arm check runs **inside the sandbox on the pinned
binary**, every [M] in this brief is about a different artifact than the one
that would produce traces — the exact substitution FEASIBILITY-A's redaction
incident warns about.

### 4.3 A′ corrects after the damage; B prevents it

A stdin line cannot truncate an in-flight tool call [M: N=3]; delivery waits
for the current tool batch. A drifted `Edit` is **written to disk** before the
correction arrives; the actor must undo it, and the trace shows the bad edit,
the correction, and the repair. B's rejected step never executes. Spec §4
prefers post-hoc judgment for the *judge's* sake, but the cost of letting a
bad step execute is real and A′ pays it every time it speaks. The lag itself is
bounded by the remaining in-flight tool time; the per-request wall mean is
11.4 s [M: mean over 740 requests, including tool execution] — **a mean bounds
nothing about the tail**.

### 4.4 The evidence is thin exactly where the design leans

TUI equality N=1 per arm; mid-turn fold N=3 headless; longest run 3 turns;
untested: interjection while the model is mid-stream rather than mid-tool,
several queued messages, a message arriving between two calls of a parallel
batch, depth beyond turn 3, any model but `claude-sonnet-5` [D: #304 §11,
§14.6]. An exact byte match on N=1 is strong evidence for a deterministic
assembly path and no evidence about variance.

### 4.5 The platform attributes our message to "the user", and we cannot remove that

The wrapper text says the **user** sent it, regardless of which provenance
field we set [M: wrapper verbatim, N=4]. Under (a)/(b) that is fine — it is
the production framing. Under the honesty goal it means the trace asserts an
authorship we did not make and cannot edit out (§6 forbids deleting it). The
tag in the body is our only handle on §11 criterion 2. B makes no attribution
claim of any kind.

### 4.6 Shared with B, listed so nobody thinks A′ escapes them

The distribution shift of any intervention (FEASIBILITY-B §7) and the
mechanism-naming stamp; the §11 three-way reconciliation, unimplemented; the
converter's last-thread loss on subagents and compaction; the unmeasured
steady-state intervention rate (sheet item 5).

---

## 5. Provenance — what each of the user's three options costs A′ (not choosing)

| option | what it does | cost to A′ | status |
| --- | --- | --- | --- |
| omit `origin` ("unattributed") | body clean; transcript marks `promptSource='sdk'`, indistinguishable from the run's own first prompt; wire shows a plain message | nothing in the trace text; asserts nothing; compliance 2 / 3 at a boundary — **cannot be ranked** | [M: N=3 boundary]; mid-turn interaction **[U]** |
| `origin: {"kind":"human"}` | adds a transcript-only field asserting a human wrote it | an honesty cost in our own metadata for no trace benefit (the wire is what we train on); wire effect **[U]** — the arm was not proxied | [M: N=3 boundary, transcript]; wire [U] |
| `isSynthetic: true` | Claude Code prefixes the **body** with `[MESSAGE FROM NON-USER SOURCE - NOT USER INPUT]` | that string enters the trained context; occurs at inference only when an SDK caller sets it (frequency **[U]**, a (b) risk); 0 / 3 compliance, all three refusals citing provenance | [M: N=3 boundary]; mid-turn interaction **[U]** |

All three provenance arms were **boundary** injections; whether the field
changes the mid-turn fold at all is unmeasured.

---

## 6. What evidence would change my mind

1. **A registered compliance test that A′ fails.** Sparse delivery, user-like
   wording, tag in body, N ≥ 10 per arm, pre-registered acceptance criterion.
   If the actor refuses or argues in a majority of interventions, A′ produces
   poisoned traces at the moments that matter, and B's invisibility wins on
   data quality regardless of every other axis.
2. **The four-arm check failing inside the sandbox on the pinned (or bumped)
   binary** with no clean alternative shape — in particular, a release that
   interrupts local tool calls on an incoming message the way 2.1.246 already
   does for MCP. That converts A′'s durability exposure from "re-verified per
   release" to "broken".
3. **B's existence questions answered yes, and its client tolerance measured.**
   If a same-body re-send diverges *and* a step-level oracle accepts within a
   few tries *and* Claude Code tolerates a buffered turn under the 10-minute
   deadline, then at ruling-2 densities B's token cost is small (§2.5), it has
   no compliance problem (§4.1), and the debate reduces to §2.2 (judge before
   vs after), §2.4 (visible vs hidden supervision) and §2.8 (which spec text
   to reverse). I would still argue A′ on those, but no longer as a clear win.
4. **Evidence that judging the tool call *without* its result is as accurate as
   judging with it** at step level. That would neutralize §4 reason 1, which is
   the spec-grounded half of §2.2.
5. **A ruling that mid-turn user interjections are rare enough at inference
   that training on them is a mixture problem** (spec §9) rather than a shape
   problem. (b) would still pass; the case for making every correction take
   that shape would weaken, and the boundary-turn variant would need costing
   again.

---

### Appendix — sources read, and their branches

- `debate-premises-v2.md` (with `## RESOLVED`), the binding sheet.
- `experiments/trace_synthesis/process_supervision/FEASIBILITY-B.md` — `origin/main`.
- `experiments/trace_synthesis/process_supervision/FEASIBILITY-A.md` incl. `## Amendment` — `origin/exp/process-supervision-research` (PR #306).
- `experiments/trace_synthesis/streamjson_input/REPORT.md` §1–§14 and `runs/{maxturns1,proxy-maxturns1}/evidence.json` — `origin/exp/stream-json-input` (PR #304).
- `experiments/trace_synthesis/process_supervision/guidebook_as_step_criterion/REPORT.md` — `origin/exp/guidebook-as-step-criterion` (PR #305; the brief's branch name for it was stale).
- `docs/trace-synthesis/spec.md` §4, §5, §6, §11, §12, §14, §16 — `origin/main`.
- Code, working tree at `main`: `src/swe_lab/harnesses/claude_code/{harness,binary,convert,constants}.py`, `src/swe_lab/sandbox/backends/host.py`; `experiments/trace_synthesis/steered_rerun/{README,REPORT}.md`, `supervisor.py`.
- Documentary, read 2026-09-01: Agent SDK "Streaming Input" page (code.claude.com); `anthropics/claude-agent-sdk-python` `subprocess_cli.py` (main); `anthropics/claude-code` `CHANGELOG.md` (main); `claude --help` on this box (2.1.257).
- Nothing in the repo was modified, committed or pushed.
