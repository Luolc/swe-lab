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
| Spend | **$0.49 of the $5.00 ceiling** (`runs/ledger.jsonl`) |
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
5. **`--resume-session-at <message id>` removes the seam entirely.** Measured on
   the wire across a four-segment loop: **0** seam text blocks, **0** synthetic
   assistant messages, `<system-reminder>` count unchanged at the baseline 3,
   and the role sequence growing `user, system, assistant, user, …` — the shape
   of an uninterrupted run. The correction arrives as a **text block appended to
   the message carrying the `tool_result`**, which is the harness's own
   mid-turn fold.

   **So `Q3a` passes with no post-processing, and `Q3b` does not arise.** The
   structural disadvantage this experiment believed it had found in §6.2 is
   removed by §9 — for this flag, at this build, at N=1 per shape.

**Recommendation.** The segmented loop is mechanically sound, markedly simpler
than A′ (no concurrency barrier, no descendant freeze, no judge cancellation,
no read-gate), and — *with `--resume-session-at`* — produces a wire shape
indistinguishable from a continuous run. It is worth carrying forward as the
parallel implementation it was commissioned as.

**Two conditions on that recommendation, and they are not decoration:**

- **`--resume-session-at` is an undocumented flag** (`hideHelp()`), so the
  clean seam rests on behaviour that carries no compatibility promise. A build
  that changes it silently returns the design to §6.2's dirty seam, which is
  *not* a loud failure — it is a quiet change in the training data. Any
  implementation must **assert the seam shape on the wire**, not assume it.
- **`Q5b` found a cost cliff, and it is closer than the field name suggests**
  (§8.2). A seam after ~6 minutes of supervisor deliberation cost **3.7×** a
  seam taken immediately.

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
| `--max-turns 1` | **1** | 1 | 1 | `error_max_turns` | — | 1 | no |
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
was the same file, larger (41 509 bytes). The only other id on disk belongs to
the control's own fresh session.

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
  This yields the harness's own mid-turn shape.

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
a retention that contradicts reading it as a one-hour TTL**, and this sentence
must not be cited on its own as evidence about how long a prefix survives.

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

Two arms identical but for the pause before the final segment (`N=1` per arm):

| arm | pause | `cache_read` | `cache_creation` | cost of that segment |
| --- | --- | --- | --- | --- |
| `q5b-nodelay` | ~0 s | **24 058** | **205** | **$0.00765** |
| `q5b-delay` | **380 s** | 18 428 | **5 901** | **$0.02857** |

**A seam taken after ~6.3 minutes cost 3.7× a seam taken immediately.** The
conversation-specific prefix had to be re-created (`cache_creation` 205 →
5 901); the 18 428 tokens still read from cache are the system prompt and tool
definitions, which are shared across sessions and survived.

**A correction I owe to my own earlier reading.** Every `cache_creation` in this
experiment is reported under `ephemeral_1h_input_tokens`, with
`ephemeral_5m_input_tokens` at 0 throughout, and I read that mid-run as "the
TTL is an hour, so the supervisor's latency budget is generous". **The measured
behaviour does not support that**: the conversation prefix was gone at 380 s.
The field name and the observed retention disagree, and this report goes with
the observation. What the field means is **not** established here.

**Limits.** `N=1` per arm, and the two arms are different sessions. Unrelated
runs of this experiment were in flight on the same account during the pause,
which is a confound that cannot be excluded from an `N=1` pair. What is safe to
carry is the direction and the rough size, not the exact boundary: **the cliff
exists and is nearer than an hour**. The eventual design should measure its own
number rather than inherit this one.

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

### 9.1 `--resume-session-at` produces a clean seam — the measurement

A four-segment loop, `--max-turns 1` per segment, segment *n* resuming with
`--resume-session-at <uuid of the last message record>`, captured on the wire.
Credential gate first, both directions: `[REDACTED]` **25** occurrences,
credential shapes **0**.

Five requests were captured for the four segments; the two smallest are
auxiliary calls (1 and 2 messages) rather than main-loop turns, and the table
below is **captured requests**, not a per-segment mapping:

| captured request | messages | role sequence | seam text blocks | synthetic assistant | `<system-reminder>` |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | `user` | **0** | **0** | 0 |
| 2 | 2 | `user, system` | **0** | **0** | 3 |
| 3 | 4 | `user, system, assistant, user` | **0** | **0** | 3 |
| 4 | 6 | `user, system, assistant, user, assistant, user` | **0** | **0** | 3 |
| 5 | 8 | `user, system, assistant, user, assistant, user, assistant, user` | **0** | **0** | 3 |

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

The correction's landing shape, from the request body:

```
5 user [tool_result, text]   [tool_result] + "Continue."
```

It is appended to the message carrying the `tool_result` — **the harness's own
mid-turn fold**, no wrapper and no synthetic turn. This is the same *kind* of
shape A′ was shown to produce; **it was not compared byte-for-byte against A′'s
`len 440 / sha256 3ba88726…fb90c8` block, and no such equality is claimed here.**

Per-segment cost with the anchor, which is *cheaper* than the dirty seam because
there are no seam records to cache:

| segment | anchor | `cache_read` | `cache_creation` | cost |
| --- | --- | --- | --- | --- |
| 1 | no | 31 375 | 8 422 | $0.04408 |
| 2 | yes | 39 797 | **195** | $0.00980 |
| 3 | yes | 39 992 | **127** | $0.00959 |
| 4 | yes | 40 119 | **163** | $0.00953 |

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
- **Counting events is not counting messages** (§2).
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
- **`N=1` on every wire shape.** A clean seam at N=1 is *possibility, not
  reliability*.
- **No byte-level comparison against A′'s injected block** was made.
- **Deep loops.** The deepest run here is five segments; a real rollout is
  30–100.
- **`--resume-session-at` under compaction, parallel tool calls and sub-agents**
  is untested, and those are exactly where a turn's boundary is least like the
  simple case measured here.
