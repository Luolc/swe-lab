# Debate premise sheet v2 — how should process supervision be delivered?

> **Landed 2026-09-01 from the scratch copy the debate actually ran on, with two
> corrections marked in place.** This file is evidence of what both sides were
> shown, so nothing is silently edited: each correction below is annotated where
> it sits, and the original wording is quoted next to it.
>
> 1. **PR #305's cost is `$0.643`, not `$0.59`** — the report recomputed it from
>    the preserved responses, retries included, after this sheet was written.
>    The judge used the corrected figure and noted the lag.
> 2. **"`--max-turns` … currently the only usable fine-grained path" is
>    superseded** by this sheet's own `## RESOLVED` section, which closes the
>    granularity axis. The judge recorded the leftover and ruled that RESOLVED
>    outranks it; both briefs followed RESOLVED.
>
> Nothing else is changed.

Binding shared input. **Where this sheet and any report disagree, this sheet wins** — it carries
corrections the reports have not all absorbed. Read it before the reports.

## What changed since v1 (do not argue from the old framing)

The question is no longer "A vs B". Approach A as originally specified — hook stops the session,
supervisor injects, `--resume` — **is dead**, and the reason has been narrowed. It is replaced by
**A′: deliver the correction as a user message on the stdin of a live
`claude -p --input-format stream-json` process.** No stop, no resume.

## The criterion (this was corrected by the user, and it replaces the old one)

Everyone was judging channels by "is it a clean `user`-role turn". **That is not the criterion.**
The criterion is two questions:

- **(a) Do we take SFT loss on tokens the actor did not generate?**
- **(b) Does this context shape occur at inference time?**

Consequences already derived from it, which you must not re-derive wrongly:

| artifact | (a) | (b) | verdict |
| --- | --- | --- | --- |
| `"No response requested."` — synthetic **assistant** turn | **violates** | — | **this alone kills the resume path** |
| `"Continue from where you left off."` — synthetic **user** turn | passes | likely passes | **not** a disqualifier |
| `<system-reminder>` announcing the hook stop | passes | fails — only appears in supervised setups | a (b) problem, not an (a) one |

Two corollaries, both load-bearing:

1. **Accepting a message because its `role` field says `user` is the same error as rejecting one
   because it isn't a user turn.** Both skip the criterion.
2. **(a) and (b) alone do NOT license deleting a turn.** Deletion adds no tokens, and the
   remaining shape does occur at inference. Deletion stays forbidden by spec §6 and the honesty
   goal. The new criterion **narrows** that rule's scope; it does not replace the spec.

## Measured, with N and design — you may rely on these

**Channel behavior** (Claude Code 2.1.257, `claude-sonnet-5`, PR #304, 26 sessions, $2.93):

- **Turn-boundary injection is clean.** 21 runs. Three artifact strings absent from transcript and
  stdout; **wire `<system-reminder>` count identical to the no-injection control (3 vs 3)**. A
  positive control in the same harness (SIGKILL + `--resume`) **did** reproduce the repair pair,
  so the detector fires — the negative is informative. *Note: the transcript grep for
  `<system-reminder>` is never True in any arm including the positive control, so its False is not
  evidence and has been withdrawn from the report. Only the wire count answers this.*
- **Granularity, default flags:** the number of `result` events equals **the number of stdin
  messages that triggered a turn — not the number of tool calls.** A control with two tool calls
  emitted **one** result after 36 s. So by default the only clean injection point is **after the
  whole task**, which is multi-turn conversation, not process supervision. (N=21, zero cost.)
- **`control_request` interrupt: fine-grained and clean of the three strings, but excluded.** It
  truncates an in-flight Bash call (a user line cannot — it only queues). But it **writes two
  user-role records that speak for a user who did nothing**: a tool_result saying "The user doesn't
  want to proceed with this tool use… STOP what you are doing", and "[Request interrupted by user
  for tool use]". **Same failure class as the resume artifacts**, not a different one. (N=1.)
- **`--max-turns` segmentation: clean.** *(Landing correction: this bullet
  originally read "clean, and currently the only usable fine-grained path";
  `## RESOLVED` below supersedes that half — granularity is free at any moment.)*
  Same
  `session_id` throughout; each segment ends `result subtype=error_max_turns`; wire
  `<system-reminder>` count 3 = control; the injected message is a clean independent user record.
  **Granularity is "after each assistant message", NOT "after each tool call"** — the model batches
  parallel `tool_use` blocks into one message and that cannot be prevented today. (N=2 runs /
  4 segments.)
- **`shouldQuery: false` works as documented**: no assistant turn, **0 API requests, 0 tokens**,
  19 ms; content merges into the next user message. Actor complied 2/3.
- **Provenance is a controllable field, and the mechanism matters more than the rates**
  (N=3 per arm — **underpowered, the arms cannot be ranked**): `isSynthetic:true` writes
  `[MESSAGE FROM NON-USER SOURCE - NOT USER INPUT]` **into the message body**, so it lands in the
  trace text; `origin:{"kind":"human"}` only adds a transcript field and asserts a human wrote it;
  **omitting `origin` leaves the body clean and asserts nothing** ("unattributed").

**Oracle density** (PR #305, N=**2 traces** / 67 steps, **$0.643** — *landing
correction; this sheet said `$0.59` during the debate, and #305's report
recomputed it from the preserved responses, retries included* — on
already-paid-for traces):

- **20/67 steps adjudicable (30%); 47 silent (70%).** 16 on-track, 4 off-track. Citations 20/20
  traceable.
- **Stage coverage is structurally skewed** — one stage cited 12×, another **zero** — because
  guidebook stages differ in whether they map to an observable action at all ("run the neighbouring
  tests" does; "turn the prose into a checklist" does not). So **a guidebook is a step-level oracle
  only over the subset of its stages that map to observable actions.**
- Two of the four rejections caught the exact trap the guidebook was written for, on the
  **unsteered** trace. **This establishes existence — that such a judgment can be made and
  reviewed — not a rate.** N=2 traces is the sample size; steps cluster within a trace.

**A defect that sits on top of the recommended path:** `event_stream_outcome` reads the **last**
`result`. Every `--max-turns` segment ends in `error_max_turns`, and an interrupted turn's
`error_during_execution` was observed being masked by a later successful result. A green outcome
therefore has more than one cause.

**A capture defect that is ours, not Claude Code's:** stream capture **drops the injected message
entirely** by default (needs `--replay-user-messages`); and with replay on, mid-turn's shape
**disagrees between the two captures** — stream renders it `user`, the wire says `system`.
**The wire is the truth**: build training examples from the wire shape, never the stream rendering.

## Not established — do not launder these into arguments

1. **B's cost model is unmeasured.** `E[requests per accepted step] = 1/(1-p)` requires a *stable
   conditional acceptance probability after each rejected history*. An upstream can return a
   different completion every time (divergence 30/30) while the oracle rejects every one — perfect
   divergence, infinite cost. The divergence-pair sample-size arithmetic measures a different
   quantity and was deleted from the report.
2. **B may not function at all.** Whether an identical re-send to the upstream is deterministic or
   deduplicated was only ever observed from the caller's side. If it is, B does not work — an
   existence question, not a cost question. **This is B's largest single risk.**
3. **Quota attribution is unknown.** The only fact: *the first observed seven-day utilization
   during execution 1 was 0.61.* No pre-request snapshot, shared account, **no bound on any
   batch's own share.**
4. **"Byte-faithful replay" is false today**: `streamSSE` uses `bufio.Scanner`, stripping line
   endings including CR, then reconstructs `line + "\n"`. At most SSE-semantic replay.
5. **The steady-state rejection rate is unmeasured.** The 30% above is a *first-intervention rate
   on unsupervised traces by a guidebook-based judge* — three separate qualifiers, all of which
   change the number.

## USER RULINGS — these are settled, do not argue against them

**1. Granularity is a tunable, not a requirement.** The user has ruled explicitly: per-tool-call
supervision was never required. **Any checkpoint short of "after the entire agent loop finishes"
is acceptable** — after each assistant message, or after every ten of them. Granularity is
therefore a **cost/latency knob, not a discriminator between the designs**. Do not argue that one
design wins because it can intervene more finely; argue about what that finer granularity would
actually buy, given this ruling.

**2. Intervention is expected to be rare.** In the user's words, the injection mostly will not
fire — only when the actor has drifted far enough that the supervisor judges it will not recover
otherwise. **Design for sparse intervention.** Note the consequence that both sides must cost:
*checking* happens at every checkpoint, *speaking* happens rarely. Those are two different costs
and a design can be cheap in one and expensive in the other. The measured 30%-adjudicable /
70%-silent figure (N=2, weak) is about the checking side.

**3. Post-hoc filtering / best-of-N stays permanently out of scope.** Already settled: it is a
known-good baseline, not research.

## The axes the debate must actually decide

1. **Delivery mechanism.** A′ (stdin user message into a live process) vs B (oracle inside the Go
   proxy, resampling rejected completions).
2. **Granularity — now demoted by user ruling 1.** A′ can checkpoint after each assistant message
   (`--max-turns`, measured clean) or after any number of them. B checkpoints per completion.
   **Since anything short of "after the whole loop" is acceptable, finer is not automatically
   better** — state what B's per-completion granularity buys that per-assistant-message does not,
   or concede the axis.
3. **Silence.** The oracle has nothing to say on ~70% of steps (N=2, weak). *Orchestra's inference,
   labeled as inference, not measurement:* B runs the oracle on every completion to obtain an
   opinion 30% of the time; a "speak when you have something to say" shape does not pay for
   silence. **Attack this inference if it is wrong — it rests on N=2.**
4. **Ownership and durability.** Spec §5 already decided: "Steer from a Claude Code hook — **not
   the proxy**." B contradicts it and is adoptable only with a new ADR that rewrites that spec
   paragraph in the same PR; costing B without costing that ADR undercosts it. **The strongest
   argument on B's side, which A′ must answer:** everything A′ relies on is *undocumented CLI
   behavior in a binary that ships updates constantly* (`--max-turns` segmentation semantics,
   stdin queueing, absence of artifacts). B depends only on the HTTP API. Argue durability
   explicitly; do not let it stay implicit.
5. **Provenance.** Which of the three origin options we adopt is **the user's call, not the
   debate's**. State what each option costs your side; do not pick one.

## RESOLVED — the TUI comparison landed, and it changes axis 2

**Measured: a mid-turn interjection typed in the real interactive TUI produces a wire shape
BYTE-IDENTICAL to a mid-turn stdin line in `-p` mode.** Driven through a pty against a real TUI,
same task, same correction, same Go proxy, compared against both no-injection controls:

| arm | wire messages | `<system-reminder>` count |
| --- | ---: | ---: |
| headless control / TUI control | 6 / 6 | 3 / 3 |
| headless mid-turn / TUI mid-turn | 7 / 7 | 4 / 4 |

The injected `system` message is byte-for-byte the same on both sides (`==` compared).
**N=1 per arm, 4 arms** — an exact shape match is a strong signal that the assembly path is fixed,
but it says nothing about variance across tasks, models, or interjection timing.

**Consequences, all binding:**

1. **Criterion (b) holds for mid-turn injection.** The `role=system` `<system-reminder>` is the
   *production* shape, not a headless-only synthetic form. It enters context, not loss, and
   fabricates no assistant turn — so it passes (a) as well. **Mid-turn injection is usable.**
2. **The earlier "mid-turn is dirty" verdict is WITHDRAWN.** Its reason — "it is not a real
   `user` turn" — was not the criterion, and spec §11 had already recorded that wire-level role is
   not the criterion. Do not resurrect it.
3. **Granularity is free.** Injection is possible at any moment, at **zero extra API requests**.
   The whole `--max-turns` / `control_request` apparatus is unnecessary, and with it the
   `error_max_turns` segmentation and the `event_stream_outcome` collector defect that sat on top
   of it. **Axis 2 is closed: A′ can intervene at any point, as cheaply as not intervening.**

**Two engineering consequences that any A′ design must carry (measured):**

- A proxy capturing a **TUI** session sees two extra API exchanges — a startup quota probe and a
  post-turn prompt-suggestion request whose body is the whole conversation plus a
  `[SUGGESTION MODE: …]` user message. **A collector must discard it**, or a user message that
  never existed enters the corpus.
- The two captures disagree on mid-turn shape: stream capture (with `--replay-user-messages`)
  renders it as a **user** message; the wire says **system**. **The wire is correct.** A
  stream-derived trace asserts a user turn the model never saw. This channel requires proxy
  capture.

Not tested: interjection outside a tool call (mid-stream); multiple queued messages; a TUI with
permission prompts enabled; whether prompt-suggestion can be disabled.

## House rules

- **Every quantitative claim carries its status**: measured (with N and design) / inferred /
  unmeasured. This project has produced six instances of a reading restated as a stronger claim;
  the judge must **reject**, not discount, any number without that label.
- **State a test's direction before citing it.** If a pass establishes nothing, say so, and never
  cite that pass as support.
- **When a check reports a violation, the checker is a candidate explanation** — usually the
  likelier one, because the checker is the newer half.
- **Do not argue for an option whose cost you cannot state.** "Cheaper" without a unit is not an
  argument.
- **Say plainly where your own side is weakest.** A steelman that hides its worst problem is
  useless to the judge.
