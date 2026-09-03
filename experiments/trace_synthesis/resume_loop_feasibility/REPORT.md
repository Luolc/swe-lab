# Phase 0 report: is a segmented resume loop a viable supervision carrier?

**Design and criteria:** [`README.md`](README.md), frozen in `987273c` before any
API call. Every criterion below was registered there; none was chosen after
seeing a number. One amendment was made mid-run and is labelled as such
(*Amendment 1*).

| | |
| --- | --- |
| Harness | Claude Code **2.1.259** |
| Model requested | alias `sonnet`; **the response reported `claude-sonnet-5`** on every call |
| Sampling parameters | none sent — the CLI exposes no temperature/top-p knob; *recorded as absent* |
| Ran | 2026-09-03, ~15:30–16:10 Z, one host, no container |
| Spend | **$0.70 of the $5.00 ceiling** (`runs/ledger.jsonl`, 24 non-zero entries summing to $0.6989024) |
| Task | the 5-step nonce fixture in `toy_task.py`, not a SWE-bench instance |

---

## Verdict

**Adopt-worthy as a parallel implementation. The seam can be made clean — but
only with `--resume-session-at`, and the plain `--resume` seam is not.**

The result that decides this arrived last and reversed the reading the earlier
arms supported. Both are reported, in the order they happened, because the
reversal is the finding.

1. **`--max-turns N` is a usable cut point.** A turn is exactly one model
   round-trip (measured). The cut lands after a `tool_result` with no dangling
   `tool_use`, the early exit is distinguishable from natural completion on
   **four** independent fields, the count **resets per invocation**, and the
   session resumes with full context (7/7 positive chain, including a
   fresh-session negative control).
2. **The seam is not a cache miss.** Each resumed segment re-reads the prefix
   from cache and creates only **127–329** new cached tokens — about 1 % of the
   prefix. Segmentation's marginal cost is one extra request per seam plus that
   delta, not a re-priced context.
3. **The actor does not re-orient.** Across five seams: no `REDO`, no `DONE`.
4. **Plain `--resume` produces a dirty seam** — and, contrary to what the
   `--max-turns` path was expected to buy, **byte-for-byte the same dirty seam
   as SIGKILL+resume**. Captured on the wire, the resumed request carries
   `Continue from where you left off.` as a text block on the message holding
   the last `tool_result`, plus a synthetic **assistant** message
   `No response requested.` Deleting them removes the *cause* of text the trace
   keeps (`Q3b`), and deletion alone does not even restore the shape — it leaves
   **two adjacent `user` messages**, which no unsegmented run produces.
5. **`--resume-session-at <message id>` removes both default-resume
   artifacts — and introduces a third shape of its own.** Measured on the wire
   across a four-segment loop: **0** `Continue from where you left off.` blocks,
   **0** synthetic assistant messages, `<system-reminder>` count unchanged at the
   baseline 3, role sequence growing `user, system, assistant, user, …`.

   **But the correction lands as `[tool_result, text]` — a block layout neither
   control produces**, and one seam accumulates per segment (mixed messages at
   indices `[3]`, `[3,5]`, `[3,5,7]`):

   | path | how the correction appears | measured in |
   | --- | --- | --- |
   | unsegmented, same task | last message `user [tool_result]` | `q3wire-evidence.json` |
   | **A′ mid-turn — `-p` stdin *and* the real TUI** | a separate trailing **`system`** message, `[text]`, `len 440`, `sha256 3ba88726…` | `streamjson_input/runs/{proxy,tui}-midturn` |
   | **anchored resume seam** | `user [tool_result, text]` | `q7loop-evidence.json` |

   The correction blocks **accumulate one per seam** — `correction_text_blocks`
   reads 0, 1, 2, 3 across the four main-loop requests — so a 30-turn rollout
   ends with 29 of them (one per seam, and 30 segments have 29 seams), not
   one.

   Three separate statements, and none of them substitutes for another:

   1. **Established.** The two default-`--resume` artifacts — the synthetic
      assistant turn and `Continue from where you left off.` — **do not appear**
      on the anchored path.
   2. **Not established.** That the correction reaches the model in a shape
      inference produces.
   3. **Observed — and this is a result, not a gap.** The supervised shape
      (`user [tool_result, text]`) and the measured inference-time shape (a
      separate trailing `system` message, pinned at `len 440`,
      `sha256 3ba88726…` by `tests/test_streamjson_input_evidence.py`) **are
      different**. A trace produced by this loop therefore contains a layout
      that **occurs under supervision and did not occur in either control** —
      which is what owner criterion **(b)** asks about: *a context shape that
      does not occur at inference time.* **The anchored shape differs from every
      inference control we have measured, and (b) is therefore uncleared — this
      sample does not show the shape cannot occur at inference, nor that (b) is
      violated.** On the first end-to-end run's wire (§9.3) — a real SWE-bench
      instance, no `--safe-mode` — `[tool_result, text]` occurs **0 times in the
      fullest conversation's 31** tool-result-carrying user messages, and 0
      across **496 cumulative wire instances** of them. One rollout, one
      instance.

   So the segmented loop trades one criterion violation for a different one. It
   fixes **(a)** — no assistant tokens the model never wrote — and on this
   evidence it **does not clear (b)**. It was not previously reported this way:
   this report called the anchored seam "the harness's own mid-turn fold", and
   the repository's own pinned control says it is not.

> ### Criterion (b) was relaxed on 2026-09-03 — by the owner, and here is why
>
> **Who:** the project owner. **When:** 2026-09-03, after the measurements below
> were taken and reported. **Why:** a first-principles argument that this is
> **SFT data generation** and rich post-processing is available, so a trace need
> not match the shape an interactive user produces — a bare or dirty record is
> acceptable. The `bare minimum` named was: stop every N turns (N=5), let a
> judge decide inject-or-not, continue to the end.
>
> **Nothing measured in this report changed.** Statement ③ is still true and its
> numbers are untouched. What changed is its **status**: from a *blocking
> objection* to a *measured cost*. Recorded in full because a relaxed criterion
> with no record of who relaxed it, when, and on what argument reads six months
> later as though the problem was never found.
>
> **One requirement was not relaxed, and it is now the only hard one:** the
> synthetic assistant record must never be trained on. See §12.

**Recommendation (revised 2026-09-03 under the ruling above).** Take the
segmented loop forward and get it running end to end. It is mechanically sound,
markedly simpler than A′ (no concurrency barrier, no descendant freeze, no judge
cancellation, no read-gate), and the objections that stood against it in the
previous revision are objections under a criterion its owner has now set aside.

Under the ruling, these are **costs to record, not blockers**:

- `[tool_result, text]` differing from A′'s trailing `system` message (§9.1);
- the correction blocks accumulating 0/1/2/3 per seam (§9.1) — still tracked as
  a **context cost**, since it grows with rollout length;
- the plain-`--resume` dirty seam (§6), which no longer has to be cleaned to be
  usable;
- `Q8` (§13), which drops from *possibly decisive* to *nice-to-have* and should
  not hold up bring-up.

**The one thing that does gate it** is §12's filter — a filter with a test,
rather than a sentence in a document.

**Conditions that survive the relaxation** — setting (b) aside does not touch
these:

- **The synthetic assistant record must not reach training.** The owner's one
  remaining hard requirement, now carrying a named filter and a two-armed test
  (§12). This is not a caveat; it is the gate.

- **`--resume-session-at` is an undocumented flag** (`hideHelp()`), so the
  artifact-free seam rests on behaviour that carries no compatibility promise.
  A build
  that changes it silently returns the design to §6.2's dirty seam, which is
  *not* a loud failure — it is a quiet change in the training data. Any
  implementation must **assert the seam shape on the wire**, not assume it.
- **`Q5b` observed one confounded pair at 3.7×** (§8.2) — a seam after ~6
  minutes of deliberation against one taken immediately. Whether delay *caused*
  it, and where any boundary lies, is **could not determine**; the registered
  perturbation control was not run. Any design that depends on a deliberation
  budget must measure its own.

**What this does not say.** Nothing here measures whether supervision helps.
That is the paired-arm resolved-rate question, and it is untouched by which
channel carries the correction.

## 1. Q0 — coordinates and flag existence (free)

The design rests on `--max-turns` existing, and the commissioning brief had
recorded it as **not existing** on the strength of `claude --help | grep`.

```sh
claude -p --max-turns              # error: option '--max-turns <turns>' argument missing
claude -p --definitely-not-a-flag  # error: unknown option '--definitely-not-a-flag'
```

**The two arms print different errors — that is what makes it a check.** Neither
invocation reaches the API. `--help | grep` cannot support this proposition
(the flag is `hideHelp()`), and neither can `claude --max-turns 1 --version`,
because `--version` short-circuits before option validation and prints the same
thing for a real flag, a fake flag and no flag.

Same probe, same session, other hidden flags — establishing that each option is
**defined** and what its definition calls its argument, and **nothing about
semantics**:

| flag | declared argument |
| --- | --- |
| `--max-turns` | `<turns>` |
| `--resume-session-at` | `<message id>` |
| `--resume-drops-turn` | `<message id>` |
| `--task-budget` | `<tokens>` |
| `--rewind-files` | `<user-message-id>` |
| `--system-prompt-snapshot` | `<on\|off>` |

Regenerate: `python run_matrix.py q0` → `runs/q0/evidence.json`.

## 2. Q2′.1 — what does `--max-turns` count?

The registered kill condition was: if `--max-turns 1` completes the task, the
unit is the whole exchange and the flag buys no granularity.

| arm | assistant messages | `tool_use` | `tool_result` | `subtype` | `terminal_reason` | exit | task complete |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `--max-turns 1` | **1** | 1 | 1 | `error_max_turns` | `max_turns` | 1 | no |
| `--max-turns 2` | **2** | 2 | 2 | `error_max_turns` | `max_turns` | 1 | no |
| `--max-turns 3` | **3** | 3 | 3 | `error_max_turns` | `max_turns` | 1 | no |
| unbounded (control) | **12** | 11 | 11 | `success` | `completed` | 0 | **yes, 5/5 nonces** |

Assistant messages equal N exactly, and the unbounded control is strictly larger
on all four counts *and* finishes. **A turn is one model round-trip.**

**`N=1` per arm** (four arms, one task, one model). The arms differ by more than
sampling noise could produce — 1 vs 2 vs 3 vs 12 — so a single run of each
carries the counting claim; it does not establish that the count never varies.

The binary's schema string `Maximum number of agentic turns (API round-trips)
before stopping` says the same thing; it is a docstring, it was not treated as
evidence, and this table is what the claim rests on.

**A counting trap worth recording.** `type: assistant` **events** outnumber
assistant **messages** — thinking and `tool_use` arrive as separate events
sharing one `message.id`. Counting events gives 2/4/5/15 and would have made the
unit look wrong. The table counts distinct message ids; both are in
`meta.json`.

Regenerate: `python run_matrix.py q2a --turns 1 2 3 none`.

*(Ran before Amendment 1, so these runs carry the budget readout — see §8.)*

## 3. Q2′.2 — is the early exit distinguishable from completion?

The registered criterion was one differing field. There are **four**:

| field | truncated | completed |
| --- | --- | --- |
| `result.subtype` | `error_max_turns` | `success` |
| `result.terminal_reason` | `max_turns` | `completed` |
| `result.is_error` | `true` | `false` |
| process exit code | `1` | `0` |

A loop driver must not read exit 1 as failure: **the supported early exit is
reported as an error.**

## 4. Q2′.3 / .4 / .5 — dangling calls, resume, and the count resetting

- **No dangling `tool_use`.** `tool_use == tool_result` in every arm, and the
  transcript confirms the cut lands *after* the tool result record. The
  registered risk — an unpaired `tool_use` making the conversation unsendable —
  **did not occur** at any N tested.
- **A property worth naming:** the cut lands where the actor has *just seen* a
  tool result and has *not yet reacted*. That is where a supervisor most wants
  to speak.
- **The count resets per invocation.** A resumed segment with `--max-turns 1`
  produced exactly one assistant message and exited `success`. The registered
  failure — a cumulative count making segment 2 exit having done nothing — did
  not occur; the witness is the ledger advancing, not the exit code.

## 5. Q1 — does resume carry context? (positive chain, 7/7)

| # | condition | result |
| --- | --- | --- |
| 1 | segment 1 ran and was truncated | ✅ `error_max_turns`, 5 turns |
| 2 | segment 1 read `step2.txt` | ✅ seen in the tool inputs |
| 3 | `step2.txt` absent when segment 2 started | ✅ deleted, `test ! -e` |
| 4 | segment 2 produced the nonce **verbatim** | ✅ |
| 5 | segment 2 issued **zero** tool calls | ✅ 0 — it came from context |
| 6 | fresh-session control, same question | ✅ **did not** produce the nonce |
| 7 | segment 2 exited cleanly | ✅ `success` |

**`N=1`** for the chain and `N=1` for the control arm. Seven conditions holding
together is not seven samples.

Arm 6 is what makes arm 4 informative; arm 5 is what makes it *context* rather
than a tool finding the value again.

**The session grows; it does not fork.** After resume the original session file
was the **same file**, and the only other id on disk belongs to the control's
own fresh session — two session files for three invocations. **No before/after
snapshot was captured**, so *"grew"* rests on the resumed session having one
file rather than on any size delta; the locator was broken during the run and
`runs/q1-session-evidence.json` is a labelled after-the-fact re-derivation.

Regenerate: `python run_matrix.py q1` → `runs/q1-summary.json`.

## 6. Q3 — the seam ★

### 6.1 What the session file contains

```
32 attachment  max_turns_reached  {'type': 'max_turns_reached', 'maxTurns': 5, 'turnCount': 6}
…
38 user        isMeta=true        'Continue from where you left off.'
39 assistant   (no isMeta)        'No response requested.'
40 user        promptSource=sdk   <our message>
```

**Records 38 and 39 are byte-for-byte the two that `streamjson_input` §3.3
observed on a SIGKILL+resume seam.** `--max-turns` buys a *distinguishable
exit*; it does **not** buy a cleaner seam. The two were conflated in the brief
and they are separate properties.

**The `max-turns-note-forgery` guard's note is real and was observed** — as an
`attachment` record of type `max_turns_reached`, not as a user or assistant
message.

**The synthetic assistant record is mechanically identifiable**, not
heuristically: `message.model == "<synthetic>"`, it lacks `requestId`,
`apiBlockIndex` and `effort` which every real assistant record carries, and it
adds `isApiErrorMessage`.

**Resume does not re-inject the session preamble.** No repeated system prompt,
skill listing or agent listing; the resumed segment added only
`total_tokens_reminder` and `budget_usd` attachments.

### 6.2 What reaches the API — the measurement that decides Q3b

Captured with the repo's Go `cc-reverse-proxy` (the only build with header
redaction). **Credential gate, both directions, before anything was read:**
`[REDACTED]` occurred **20** times (the positive arm — redaction demonstrably
fired) and `sk-ant-`, `bearer ey` and JWT-shaped strings occurred **0** times.
A zero on the negative arm alone would not have been a check.

The resumed segment's request body:

| # | role | blocks | |
| --- | --- | --- | --- |
| 4 | assistant | `thinking`, `tool_use` | |
| 5 | user | `tool_result`, **`text`** | ← `Continue from where you left off.` |
| 6 | **assistant** | `text` | ← `No response requested.` |
| 7 | user | `text` | our `Continue.` |

**Both seam records reach the wire.** So the actor's next output is generated in
a context containing them, and removing them from the trace removes the cause of
text the trace keeps. `Q3b` exists **on this path**.

**`N=1`** — one capture, one resumed segment.

> **Read §9 before citing this section.** Everything in §6.2 and §6.3 is a
> measurement of the **plain `--resume`** seam. `--resume-session-at` removes
> both records, and with them this whole subsection's problem. §6 is kept in
> full because it is what the default path does, and because a build that
> changes `--resume-session-at` returns us to exactly here.

Two further results from the same capture, one of which contradicts a guess:

- **The seam adds zero `<system-reminder>` blocks.** Three in the unsegmented
  requests and three in the resumed one. This detector demonstrably fires (it
  reads 3, not 0), so its equality here is informative.
- **The `max_turns_reached` note does not reach the wire** — it is
  transcript-only. Our shared expectation that it would appear as a
  `<system-reminder>` was wrong.

### 6.3 Q3a — can deletion restore the shape? Not by itself

| | role sequence |
| --- | --- |
| unsegmented control | `user, system, assistant, user` |
| resumed, raw | `user, system, assistant, user, assistant, user, assistant, user` |
| resumed, after deleting both seam items | `user, system, assistant, user, assistant, user, **user**` |

Deletion leaves **two adjacent `user` messages**, a shape the control never
produces — so *"delete the seam records"* is **not** sufficient, and the
registered criterion fails as written.

Two repairs exist and both are edits beyond deletion, so the report states them
as options rather than as a solved problem:

- **Merge** the two adjacent user messages.
- **Substitute** — write the correction into the text block where
  `Continue from where you left off.` sits, and drop the synthetic assistant.
  This yields the layout the harness itself writes on the resume path — which
  is **not** the shape inference produces: §9.1 and the committed A′/TUI
  control show inference emits a separate `system [text]`.

**Neither repair touches `Q3b`; both make it worse**, because the actor's output
was conditioned on the correction arriving *after* a synthetic assistant turn,
not inline. What is reported here is one reading of one capture at one revision,
by someone who knew what to look for.

## 7. Q4 — does the actor re-orient? (labels fixed before running)

Depth 5, `--max-turns 1` per segment, neutral `Continue.`:

| segment | label | first assistant text | tools |
| --- | --- | --- | --- |
| 1 | `RESTATE` | "I'll read the five step files in order, sleeping 8 seconds between reads, then write the collected TOKEN values to result.txt." | `Bash` |
| 2 | `CONTINUE` | *(none)* | `Read` |
| 3 | `RESTATE` | "I've read step1.txt (TOKEN 1582e487…). Now sleeping 8 seconds before reading step2.txt." | `Bash` |
| 4 | `CONTINUE` | *(none)* | `Read` |
| 5 | `RESTATE` | "Read step2.txt (TOKEN d5f604c8…). Sleeping 8 seconds before step3.txt." | `Bash` |

**No `REDO`, no `DONE`.** The actor advances the task every segment and never
repeats completed work or declares premature victory. The tool sequence is the
task's own alternation (`Bash`, `Read`, `Bash`, `Read`, `Bash`).

`RESTATE` here is **accurate progress narration, not re-planning** — which is a
judgement, stated as one, with the verbatim text above so a reader can disagree.

**And that narration is `Q3b`'s concrete instance.** "I've read step1.txt …
Now sleeping …" is exactly the shape of a reply to *"Continue from where you
left off."* Delete that record and the narration remains with its prompt gone.

**Limits.** `N=1` arm at depth 5, one task, one model. This steers a judgement;
it supports no rate, and no percentage is reported. The registered `Y` arm — the
same neutral continue delivered on a *live* stdin without a process restart,
which would isolate the restart from the message — **was not run**, so
`RESTATE` cannot presently be attributed to segmentation rather than to being
told "Continue."

## 8. Q5 — what the seam costs

| segment | `cache_read` | `cache_creation` | `input` | `output` | cost |
| --- | --- | --- | --- | --- | --- |
| 1 | 18 428 | 5 627 | 2 | 179 | $0.02906 |
| 2 | 24 055 | **323** | 2 | 177 | $0.00788 |
| 3 | 24 378 | **329** | 2 | 142 | $0.00762 |
| 4 | 24 707 | **261** | 2 | 109 | $0.00708 |
| 5 | 24 968 | **262** | 2 | 139 | $0.00744 |

Per-segment rows, not a mean: a mean would read identically whether every seam
cost the same or the fifth cost twice the first.

**Unsegmented control, same task:** 12 turns, `cache_read` 292 909,
`cache_creation` 7 827, output 1 385, **$0.1048**, 56 s.

**Reading.** A seam creates ~260–330 new cached tokens, roughly 1 % of the
prefix it sits on. **It is not a cache miss** — if it were, `cache_creation`
would be ~24 000. Segmentation's marginal cost over an unsegmented run is
therefore the extra request per seam plus that ~300-token delta, not a
re-priced context. The quadratic Σ(prefix) growth in the table is **common to
both arms** and is a property of any agent loop, not of segmenting.

**All cache creation landed in `ephemeral_1h_input_tokens`; `ephemeral_5m` was
0 on every call.** That is the field's value and nothing more — **§8.2 measures
one confounded pair whose delayed arm re-created its prefix**, and this
sentence must not be cited on its own as evidence about how long a prefix
survives. **No TTL interpretation is supported in either direction.**

### 8.1 Amendment 1 — the guard that was a treatment

`--max-budget-usd` writes a running readout **into the actor's context**:

```
attachment budget_usd {'used': 0.0567628, 'total': 0.25, 'remaining': 0.1932372}
```

The actor can see it is on a budget and how much is left — a shape production
does not have, whose plausible effect (wrapping up as budget falls) is *exactly*
the dependent variable Q4 measures. **A guard the actor can see is a treatment,
not a guard.** It was removed from every behavioural arm; cost is bounded
instead by `--max-turns`, the pre-launch ledger check and a wall-clock timeout —
no free parameter, no context footprint.

Q0 is unaffected. Q2′.1 and Q1 ran with it, and are not re-run, on **positive
evidence rather than argument**: the unbounded control arm carried the same
readout and still ran 12 turns to completion with 5/5 nonces. If a visible
budget caused early wrap-up, that arm is where it would show, and it did not.

This generalizes past this spike: it is true of **any** rollout that passes
`--max-budget-usd`, A′ included.


### 8.2 Q5b — how long may the supervisor deliberate?

Two arms **intended** to differ only in the pause before the final segment
(`N=1` per arm). They do not: see the confounds listed below the table — they
differ in session, working directory, nonce and tool-result content, with
unrelated traffic in flight.

| arm | pause | `cache_read` | `cache_creation` | cost of that segment |
| --- | --- | --- | --- | --- |
| `q5b-nodelay` | ~0 s | **24 058** | **205** | **$0.00765** |
| `q5b-delay` | **380 s** | 18 428 | **5 901** | **$0.02857** |

**Observed: one pair, differing 3.7×**, with the conversation-specific prefix
re-created in the delayed arm (`cache_creation` 205 → 5 901) while the 18 428
tokens still read from cache are the system prompt and tool definitions.

**That is the whole of the result, and it is less than a cliff.** `loop_arm()`
gives the two arms **different sessions, different working directories and
different random nonce and tool-result content**, and unrelated runs of this
experiment were in flight on the same account throughout. With `N=1` the pause
is not isolated from any of that. So:

- **Reportable:** one confounded pair at 3.7×.
- **Could not determine:** whether deliberation time *caused* it, and where the
  boundary lies. In particular **this report does not establish that the
  boundary is below one hour** — an earlier revision said it was.

**A correction I owe to my own earlier reading.** Every `cache_creation` in this
experiment is reported under `ephemeral_1h_input_tokens`, with
`ephemeral_5m_input_tokens` at 0 throughout, and I read that mid-run as "the
TTL is an hour, so the supervisor's latency budget is generous". **The measured
behaviour does not support that**: the conversation prefix was gone at 380 s.
The field name and the observed retention disagree, and this report goes with
the observation. What the field means is **not** established here.

**A registered control arm was not run, and that is a protocol deviation.**
README.md's Q5a registered a perturbation arm — deliberately changing the
dynamic system-prompt inputs between segments — whose job was to show that this
instrument can *see* a cache miss at all. `loop_arm(perturb=True)` exists and
**has no caller**; no ledger row corresponds to it. It was not run, and its
absence was not previously reported.

What partly covers that gap arrived by accident rather than by design: the
delayed arm above **did** move the fields (`cache_read` 24 058 → 18 428,
`cache_creation` 205 → 5 901), so the reader is not blind to a miss. That is
weaker than the registered control in the way that matters — it was not
constructed to vary one thing, so it demonstrates the instrument responds while
leaving *what it responds to* confounded.

**The eventual design must measure its own number.** Nothing here supports
inheriting one.

## 9. Q7 — the hidden flags ★

Free probes first, no API. Each flag was given an invalid argument and the CLI's
own validation answered:

| flag | what the CLI enforces |
| --- | --- |
| `--resume-session-at` | `Error: --resume-session-at requires --resume` |
| `--resume-drops-turn` | `Error: --resume-drops-turn requires --resume-session-at` |
| `--rewind-files` | `Error: --rewind-files requires --resume` |
| `--task-budget` | `must be a positive integer` |

These are **observed dependency constraints**, not inferences from the names.

### 9.1 `--resume-session-at` removes both default-resume artifacts — the measurement

A four-segment loop, `--max-turns 1` per segment, segment *n* resuming with
`--resume-session-at <uuid of the last message record>`, captured on the wire.
Credential gate first, both directions: `[REDACTED]` **25** occurrences,
credential shapes **0**.

Five requests were captured for four segments. **Exactly one is auxiliary** —
it carries no `tools` array; the other four each carry 25 tools and correspond
to the four segments at 2, 4, 6 and 8 messages. An earlier revision called the
2-message request a second auxiliary call, which discarded the capture's own
within-run baseline; `has_tools` is now recorded in the witness rather than
guessed from message counts, following `streamjson_input`'s selection rule.

| request | kind | msgs | last message blocks | seam text | synthetic assistant | `<system-reminder>` |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | auxiliary (0 tools) | 1 | `[text]` | **0** | **0** | 0 |
| 2 | agent-loop (25 tools) | 2 | `[text]` (role `system`) | **0** | **0** | 3 |
| 3 | agent-loop | 4 | **`[tool_result, text]`** | **0** | **0** | 3 |
| 4 | agent-loop | 6 | **`[tool_result, text]`** | **0** | **0** | 3 |
| 5 | agent-loop | 8 | **`[tool_result, text]`** | **0** | **0** | 3 |

Mixed `tool_result`+`text` messages accumulate across the loop — `[3]`, then
`[3,5]`, then `[3,5,7]` — so each seam leaves one permanently in the history.

**Both detectors demonstrably fire, and on this same code path.** The zeros
above are produced by `run_matrix.wire_shapes()`; running *that same function*
over the plain-`--resume` capture of §6.2 returns `seam_user_text_blocks: 1`
and `seam_synthetic_assistant: 1` on the resumed request, and the reminder
count reads 3 rather than 0 everywhere. So a broken detector and a genuine zero
are distinguished here by evidence rather than by assumption.

**This was not true when §6.2 was written.** Those numbers came from an inline
script, so "the detector fires" compared *two different pieces of code* — which
establishes nothing about the one reporting the zeros. The cross-check above is
the repair; regenerate it with:

```sh
python -c "import run_matrix, pathlib; print(run_matrix.wire_shapes(pathlib.Path('<scratch>/wire/proxy.jsonl')))"
```

### 9.1a The landing shape — and why the earlier reading was wrong

```
5 user [tool_result, text]   [tool_result] + "Continue."
```

An earlier revision called this "the harness's own mid-turn fold". **That was
wrong, and the instrument used to support it could not have seen the error.**
`wire_shapes()` compares role sequences and counts two fixed literals; block
composition *within* a message is invisible to both, and block composition is
exactly where these paths differ:

| path | last message | measured in |
| --- | --- | --- |
| unsegmented, same task | `user [tool_result]` | `q3wire-evidence.json` req 3 |
| A′ mid-turn, `-p` stdin | trailing `system [text]`, `len 440`, `sha256 3ba88726…` | `streamjson_input/runs/proxy-midturn` |
| A′ mid-turn, **real TUI** | trailing `system [text]`, same digest | `streamjson_input/runs/tui-midturn` |
| **anchored resume seam** | `user [tool_result, text]` | `q7loop-evidence.json` |

The TUI row is the one that matters: it is the **inference-time** path, it is
committed evidence, and `tests/test_streamjson_input_evidence.py` pins its role
and digest. **Our seam is not that shape.**

**Established:** the two default-resume artifacts are absent under
`--resume-session-at`. **Not established:** that the correction arrives in a
shape inference produces.
`tests/test_resume_loop_evidence.py::test_the_three_correction_shapes_are_distinct`
pins all three so a later revision cannot quietly re-merge them.

Per-segment cost with the anchor, which is *cheaper* than the dirty seam because
there are no seam records to cache:

| segment | anchor | `cache_read` | `cache_creation` | cost |
| --- | --- | --- | --- | --- |
| 1 | no | 31 375 | 8 422 | $0.04408 |
| 2 | yes | 39 797 | **195** | $0.00980 |
| 3 | yes | 39 992 | **127** | $0.00959 |
| 4 | yes | 40 119 | **163** | $0.00953 |

### 9.3 The inference-time control, on a real rollout ★

The previous revision left an open question that could have overturned §9.1's
reading: the unsegmented control was `N=1`, one toy task, `--safe-mode`, and in
it the `<system-reminder>` blocks happened to attach to the opening messages. If
a production session attaches one to a **tool result**, `[tool_result, text]`
occurs with no supervision involved and it is not a supervision-only shape at
all.

That question is answerable from data already on this machine, at **zero API
cost**: the first end-to-end run's wire capture — a real SWE-bench instance, a
real task, no `--safe-mode`, 3 064 215 bytes — pinned by
`pipeline_end_to_end/WITNESS.md` at
`sha256 701808d7ee9941eaa9de11a87277ca9d5be49305fd59ab8981d0e57f347c9136`,
which this reading verified before use. (The two paths named for it are
**byte-identical**; it is one capture, copied.)

**Why it can answer.** That run was supervised, but A′'s correction lands as a
*separate trailing `system` message* — confirmed here: the fullest request ends
`…, user, assistant, user, system`, with `system [text]`. So **any
`[tool_result, text]` in this capture could not be A′'s correction**; it could
only come from a reminder or another harness attachment. Both outcomes are
results, and the reading was done without a preference between them.

Regenerate every number in this section with one command:

```sh
python evidence.py \
  ~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0/claude_code.proxy_log.jsonl \
  runs/first-e2e-control-evidence.json --aggregate --label first-e2e-inference-control
```

Read with **the same `evidence.reduce_capture()`** the rest of this report uses
— the record shape turned out to be identical, so **no adapter and no second
analyzer** were introduced. That matters here specifically: a second detector is
what produced this report's P0.

| quantity | count |
| --- | --- |
| captured requests | **33** |
| main-loop (carries `tools`) | **32** |
| auxiliary (no `tools`) | 1 |
| messages in the fullest conversation | **71** |
| user messages there | 32 |
| — of those, carrying a `tool_result` | **31** |
| — of those, `[tool_result, text]` | **0** |
| user message *instances* across all 32 main-loop requests | 528 |
| — carrying a `tool_result` | **496** |
| — `[tool_result, text]` | **0** |

**The two denominators are different quantities and both are reported.** Every
request re-serializes the whole conversation, so the per-request tool-result
counts run exactly **0, 1, 2, … 31** and sum to 496. The 496 is a count of
**wire instances**, not of 496 independently generated messages and certainly
not of 496 rollouts; the **31** in the fullest history is the un-correlated
figure. Quoting only the larger number would present correlated
re-serialization as a much bigger sample than it is. Both are regenerated by
`evidence.aggregate_capture()` and pinned in
`tests/test_resume_loop_evidence.py`, including the `0..31` progression that
demonstrates the correlation.

**Every one is `[tool_result]` alone** — the block-shape histogram over those
instances is exactly `{"tool_result": 496}`, the only shape observed.

**The observation, and separately the explanation — they are not the same
strength and the count's hardness must not bleed into the account of it.**

- **Observation (a count, same standing as the table above).** In this capture,
  every `<system-reminder>`-bearing text block sits either on the **opening user
  message** or on a `role: system` message. None sits on a message carrying a
  `tool_result`.
- **Explanation (a mechanism claim, and weaker).** A candidate reading is that
  *this harness delivers reminders as separate `system` messages rather than
  appending them to a tool-result message*. **That does not follow from the
  count**, and it is deliberately **not** asserted as a property of Claude Code.
  It is an induction from where reminders sat in **one capture, one instance,
  one harness version** — `N=1` at the level of *harness behaviour*, whatever
  the message counts inside it — while this report leaves compaction, parallel
  tool calls, sub-agents, other instances and other versions explicitly open.
  **Scoped statement: in this capture, every reminder-bearing message was the
  opening `user` message or a separate `system` message, and none carried a
  `tool_result`.** The coordinates are committed
  (`reminder_bearing_message_coordinates`) so the observation is auditable
  rather than only described.

A reader may rely on the first bullet. The second is the fragile half, and it is
labelled so that a later citation cannot borrow the count's hardness for it.

**How this must be worded, and how it must not.** The result is **0 in 496
tool-result-carrying user messages, across one rollout on one instance** — it is
**not** "never". One run cannot support that, and the count is quoted with its
denominator precisely because a bare zero is not evidence.

**What it does to §9.1's conclusion.** It **strengthens** statement ③ without
promoting it: the anchored layout differs from every inference control measured,
so criterion (b) stays **uncleared**. It does **not** establish that the layout
cannot occur at inference — one rollout on one instance cannot carry that — and
the report does not say (b) is violated. The open question is narrowed, not
closed, and it moved in the direction *worse* for the design this experiment
was commissioned to explore.

One detail worth keeping straight: this run's trailing correction is
`system [text]` at **`len 458`**, not the `len 440` of `streamjson_input`'s
control. Those are different correction texts. **The match being claimed is the
structural position — a separate trailing `system` message — not the digest.**

### 9.2 What is not established

- **`--resume-drops-turn` was not exercised.** Its dependency on
  `--resume-session-at` is observed; its behaviour is **could not determine**.
- **`--task-budget` and `--rewind-files`: could not determine.** Argument shapes
  and dependencies only.
- All of these are `hideHelp()` flags. Their existence and constraints are
  measured; **their stability across builds is not something this experiment can
  speak to at all.**

## 10. Method notes this experiment produced about itself

- **`ls <the directory I expected>` is not a search.** This experiment reported
  the redacting wire proxy as absent from the machine after listing
  `.cache/bin/`. It was present, two levels down. *"Not where I expected"* and
  *"not on this machine"* print the same thing under `ls`; `find … -name` tells
  them apart, and with a control name that is known to be absent it is a check.
  **The decisive measurement in this report (§9.1) is the one that mistake very
  nearly cost.**
- **A guard the actor can see is a treatment** (§8.1).
- **When a check reports a violation, suspect the checker first.** `evidence.py`
  refused to write every witness, reporting an Anthropic-key shape. The shape
  was in **its own output**: the credential gate keyed its counters by the regex
  patterns themselves, so the artifact reporting "0 occurrences" contained the
  pattern text and tripped the scan. The raw captures contain 0. A checker that
  fires on every input is worth exactly as much as one that never fires, and
  both look like diligence.
- **A sound instrument aimed at a proposition it cannot observe.** This is a
  *different* failure from the repo's usual one, and the difference is the
  point. The usual entry is an observation that gives the **same output in both
  worlds** — a broken instrument. Here the instrument is **good**:
  `wire_shapes()` was independently confirmed to return 1/1 on the plain-resume
  request, 0/0 on every anchored one, and to flip 0/0 → 1/1 on a constructed
  seam mutant. It counts two fixed literals and compares role sequences, and it
  does that correctly.

  **The defect is that its output was used to support a block-shape claim it
  cannot observe at all.** `[tool_result]` and `[tool_result, text]` have
  identical role sequences and contain neither literal, so every number the
  instrument produced was true and none of them bore on the question.

  **This is the more dangerous of the two**, because the proof of discriminating
  power is *real*, and it lends the over-reaching inference a backing that looks
  earned. The `040670c` repair — running the same function over the dirty
  capture — was correct and necessary, and it established *"this function
  fires"*, never *"equal role sequences imply equal shape"*. **Widening a
  detector's sensitivity never widens the set of propositions it can speak to.**
- **Counting events is not counting messages** (§2).
- **Every control arm is also an existence proof, and we spend only half of
  it.** *"X differs from Y"* and *"Y is reachable"* are two halves of one
  observation. A control is constructed in order to negate, so the negating
  half has someone waiting for it and the affirming half does not — and what
  nobody is waiting for does not get collected.

  The instance is this report's own. A′'s separate trailing `system` message was
  used throughout §6 and §9 as the **contrast** that proved our seam was the
  wrong shape. The same observation says, just as plainly, that **a correction
  arriving as a separate `system` message is achievable in this harness** —
  which makes *"can a segmented loop get there too?"* the obvious next question.
  It was not asked here; it was asked by a reader, and it is now `Q8` — the open
  question with the most leverage over this report's conclusion.

  This is the same family as the entries above and points the other way. Those
  describe an observation **mistaken for something it is not** — undiscriminating,
  or sound but aimed past its proposition. This one describes an observation
  **used for only half of what it says**. The first is misuse and announces
  itself eventually, because a wrong conclusion eventually collides with
  something. The second is waste, and it is silent: nothing ever collides with a
  question that was not asked. Here it came within one review round of leaving a
  design marked down on a property that may not be the mechanism's at all.

- **The reversal is the shape to expect.** §6.2 was a correct measurement whose
  scope was one flag wider than the sentence it was about to be written into.

## 11. Cost

**$0.70 of the $5.00 ceiling**, enforced by `driver.guard_budget()` refusing to
launch rather than by anyone remembering it. Per-run figures are in
`runs/ledger.jsonl`; every `total_cost_usd` is read from the run's own terminal
`result` event.

## 12. Open questions

- **Everything here is a toy task on a host**, `sonnet`, `--safe-mode`, no
  container. ADR-0013's refutation condition — host measurements may not
  describe the pinned build inside the sandbox — applies to every number here.
- **The `Y` arm was not run.** Without the same neutral continue delivered on a
  live stdin, `RESTATE` cannot be attributed to segmentation rather than to the
  word "Continue."
- **`N=1` on every wire shape.** An artifact-free seam at N=1 is
  *possibility, not reliability*.
- **No byte-level comparison against A′'s injected block** was made.
- **Deep loops.** The deepest run here is five segments; a real rollout is
  30–100.
- **`--resume-session-at` under compaction, parallel tool calls and sub-agents**
  is untested, and those are exactly where a turn's boundary is least like the
  simple case measured here.
- **Whether `[tool_result, text]` ever occurs at inference time.** Narrowed by
  §9.3 and not closed: 0 in 496 on one real rollout, and the mechanism that
  would have produced it — a reminder appended to a tool result — is not how
  this harness delivers reminders. What remains open is other instances, other
  harness versions, and paths this capture does not exercise (compaction,
  parallel tool calls, sub-agents).
- **What `--resume-drops-turn` does**, which is the one flag that might make the
  seam A′-shaped rather than merely artifact-free.
- **`Q8`, below — whether the seam's shape is a property of the mechanism or of
  our delivery choice.** It is the open question with the most leverage over
  this report's conclusion.
- **The `ls` incident in §10 has no committed trace.** It is disclosed
  self-report, not evidence, and is recorded as a method note rather than a
  finding.


---

# 13. `Q8` — is `[tool_result, text]` forced, or is it how we chose to deliver? ★

**Not run. Registered here so the question is not lost, and so that running it
later cannot be mistaken for having run it now.**

### The question

§9.3 establishes that `[tool_result, text]` is a **supervision-only** shape (0 in
496 on the inference path). It does **not** establish that a segmented loop can
**only** produce that shape — and those differ by exactly one thing: whether the
layout is a property of *the resume mechanism* or of *the delivery channel we
happened to use*.

There is a counter-example in hand. **A′'s correction lands as a separate
trailing `system` message** — so making a correction arrive that way is
demonstrably possible in this harness; A′ does it on every intervention,
including in the real rollout read in §9.3. A′ delivers on the stdin of
`--input-format stream-json`.

> **Can `--resume-session-at` be combined with `--input-format stream-json`, so
> that a segmented loop's correction is delivered on that channel and lands as a
> separate `system` message rather than appended to the `tool_result` user
> message?**

### Why there is no evidence either way, right now

Every anchored arm in §9 delivered its correction as an **ordinary positional
prompt** (`claude -p "Continue."`). Every stream-json arm in `streamjson_input`
ran **without** `--resume-session-at`. **The combination has never been run**, so
neither answer has support — including the answer that would rescue the design.
The two flags are not exotic together in principle: `harness.py` already adds
`--input-format stream-json` conditionally on its own stdin mode.

### The arms

Two arms, differing **only** in the delivery channel, both captured on the wire:

- **Arm S** — anchored resume (`--resume-session-at <last message id>`) **plus**
  `--input-format stream-json`, correction written on stdin.
- **Arm P** — anchored resume, correction as a positional prompt. This is what
  §9.1 already ran, re-run alongside so the comparison is within one session of
  the machine rather than against an older capture.

**The criterion is a contrast, not a single reading.** Arm S must show the
correction as a separate trailing `system` message **and** arm P must show
`[tool_result, text]` in the same batch. If both come out the same, the delivery
channel is not the lever and the question is answered *no* — which is equally a
result. Recorded per arm: which message the correction lands in, its role, its
block list, and whether `correction_text_blocks` still accumulates 0/1/2/3.

**Three things must be re-checked in arm S rather than assumed to carry over:**

1. **The two default-resume artifacts.** Changing the delivery channel may
   reintroduce `Continue from where you left off.` and the synthetic assistant
   turn. §9.1's zeros were measured on the positional-prompt path and do not
   transfer.
2. **Whether the flags even compose.** `--resume-session-at` requires
   `--resume`; whether it is accepted alongside `--input-format stream-json` is
   unknown, and a rejection is a fast, cheap *no*.
3. **Structural position, not digest.** A′'s block is `len 440` for its own
   correction text; matching means *a separate trailing `system` message*, never
   a byte equality — the distinction §9.3 already had to make once.

### What each answer does to this report

- **Yes** — criterion **(b)** is cleared, the `[tool_result, text]`
  disadvantage disappears, and the per-seam accumulation goes with it (the
  correction would no longer pile into the tool-result message). The
  recommendation returns to roughly where §9.1 stood before the P0.
- **No** — the shape is a property of the mechanism, statement ③ is the final
  characterisation, and the current recommendation stands unchanged.

**Neither answer is preferred here.** The pull in both directions is worth
naming, because both are real: this question could rescue a design the report
has just marked down, and it arrives immediately after a result that went
against it. Neither is a reason to run it, or not to.


---

# 12. The one hard constraint, as a filter with a test

Criterion (b) was relaxed (see the Verdict). **This was not:** the model must not
take loss on tokens it never generated, and the resume seam inserts exactly such
a record — an `assistant` message reading `No response requested.` that no model
wrote (§6.1). Under the relaxation it is the **only** blocking requirement left,
which is precisely why it may not live as a sentence in a document.

`synthetic_filter.py` implements it as a **positive chain** — a record is kept
only if it can be *shown* to be model-authored:

1. it is an `assistant` record;
2. `message.model` is present, a non-empty string, and not `<synthetic>`;
3. it carries `requestId`, which a record built from a real API response has.

It never asks *"does this look synthetic?"*. An exclusion list keyed on the
literal `<synthetic>` marker would cover only the cases its author thought of,
and that marker is promised by no interface — a build that renamed it would
silently start training on the fabricated turn with every existing check green.

**Both arms are tested**, because a filter that drops everything passes the
positive arm exactly as well as a correct one:

| test | arm | what it catches |
| --- | --- | --- |
| `test_the_synthetic_assistant_turn_is_removed` | positive | the record surviving |
| `test_a_real_assistant_turn_is_kept` | **control** | a filter that drops everything |
| `test_the_filter_keeps_order_and_passes_other_records_through` | control | collateral damage to the rest of the transcript |
| `test_the_chain_is_positive_not_an_exclusion_list` | positive | a marker-only check |
| `test_the_committed_shape_fixture_matches_what_the_filter_reads` | — | the filter drifting from the records it was written for |

**The control arm was verified to discriminate, not assumed to.** Replacing the
filter body with `return []` fails `test_a_real_assistant_turn_is_kept` and
`test_the_filter_keeps_order_and_passes_other_records_through` while the
positive arm stays green (2 failed, 13 passed). **A two-armed check whose
control has never been observed to fail is one arm.**

`runs/assistant-record-shapes.json` is the committed fixture — key names, the
`model` field and block types reduced from a real resumed session, with no
content, ids or paths.

**This lives in the experiment directory, not the product path.** Phase 1 must
move it into the trace-synthesis code **with its tests**; a filter whose test
stayed behind is a filter nobody will notice breaking.
