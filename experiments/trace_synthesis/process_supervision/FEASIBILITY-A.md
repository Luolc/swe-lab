# Feasibility A — stop → edit / inject → resume

**Question asked.** Can a hook stop a running Claude Code session, can a
supervisor then edit that session (delete the last turn, or append a real
`user` turn), and can `--resume` restart it? What does it cost, and does it
collide with [`spec.md` §6](../../../docs/trace-synthesis/spec.md#6-the-trace-is-the-conversation-unedited)?

This document answers **can it be done, what would have to change, what it
costs, and what is still unknown**. It does not propose a design.

| | |
|---|---|
| Author | `swelab-resume-research` (read-only) |
| Date | 2026-09-01, machine `dev-*` (this box), `America/Los_Angeles` |
| Harness under test | Claude Code **2.1.257**, `/home/ubuntu/.local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe` |
| Model under test | `claude-sonnet-4-5` → `claude-sonnet-4-5-20250929` |
| Repo commit | `exp/process-supervision-research` @ `0e593e1` |
| Raw artifacts | [`runs/`](runs/) — 20 real `claude` invocations (16 reached the API), 2 redacted proxy captures |
| Probes | [`probes/`](probes/) — the hooks and settings files, verbatim |
| Regenerate every number | `uv run python experiments/trace_synthesis/process_supervision/analyze.py` |
| Measured spend | $0.92 — the sum of the 16 `result` events' `total_cost_usd`; a resumed segment's figure appears to be session-cumulative, so this over-counts |

Every row below is one of three: **measured** (a run in `runs/`),
**first-party schema** (a `describe()` string read out of the shipped binary by
raw-byte `grep -a`, per [§10](../../../docs/trace-synthesis/spec.md#10-what-is-measured-about-hooks)'s
rule), or **未核 / not measured**. Nothing here is inferred from documentation.

---

## Verdict

**The mechanism is feasible and far cheaper than the owner predicted. The
*trace* it produces is not clean, and cannot be cleaned without doing the thing
[§6](../../../docs/trace-synthesis/spec.md#6-the-trace-is-the-conversation-unedited)
forbids.**

Three findings carry the verdict:

1. **All three steps work, and none of them is a heavy mechanism.** Stopping is
   one hook field (`{"continue": false}`); it fires at **every** tool boundary
   including `Edit`, where the terminated `updatedToolOutput` arm was blind.
   Injecting a real `user` turn needs **no session-file editing at all** — a
   headless `claude --resume <id> -p "<hint>"` *is* a genuine user turn.
   Resuming costs **zero extra API requests**, ~0 extra tokens, and ~1 s of
   wall clock.

2. **The default capture loses the hint entirely.** Measured with this repo's
   own converter: stitching the two `stream-json` segments and running
   `event_stream_to_conversation` yields 20 messages and **zero** hints — a
   trace in which the actor pivots hard for no visible reason. That is
   *precisely* the fatal failure mode
   [§11](../../../docs/trace-synthesis/spec.md#11-open-questions) names. Only
   `proxy_log_to_conversation` preserves it (21 messages, hint present).

3. **Mid-run stop→resume forcibly plants three artifacts in the trace**, and
   two of them are turns nobody produced:
   - a `<system-reminder>` appended inside the last tool result saying
     `PostToolUse:Bash hook stopped continuation: …` — **unsuppressable**
     (omitting `stopReason` only substitutes a default string);
   - a synthetic **`user`** turn `"Continue from where you left off."`;
   - a synthetic **`assistant`** turn `"No response requested."`.

   All three are persisted in the transcript as real messages and all three
   reach the model. Keeping them means SFT takes loss on an assistant turn the
   actor never wrote. Removing them is trace surgery. **There is no third
   option**, and this applies to the *injection-only* variant too — it is not
   avoidable by declining to delete anything.

So: A is buildable in a week of plumbing, and what it produces is a trace with
a fabricated assistant turn in it at every intervention point. That is the
finding, and it is a negative one.

---

## The six questions

### 1. Can a hook terminate a running session?

**Yes — measured.** The common hook-output schema carries
`continue: bool` (*"Whether Claude should continue after hook (default: true)"*)
and `stopReason: string` (*"Message shown when continue is false"*) —
first-party schema, 2.1.257. A `PostToolUse` hook printing
`{"continue": false, "stopReason": "…"}` ends the run.

| What happens | Reading |
|---|---|
| The tool **runs to completion** and its real `tool_result` is in the trace, `is_error: false` | `runs/r4.stream.jsonl` — no fabricated tool failure, [§4](../../../docs/trace-synthesis/spec.md#4-why-judging-after-the-fact-and-not-before)'s objection to denial does not apply |
| The process exits **0**, terminal `result` event is `subtype: "success"`, `stop_reason: "tool_use"` | `runs/r4.stream.jsonl` last line |
| The remaining steps never run (2 of 4 executed) | `runs/r4.hooklog.jsonl` = 2 hook calls |
| It works at an **`Edit`** boundary — the file is written, then the run stops | `runs/rE.*`; `widget.py` on disk was modified |
| **`PostToolUseFailure` cannot stop the run** — the hook fires and returns `continue:false`, and the actor keeps going to completion | `runs/rF.hooklog.jsonl` (1 call) vs `runs/rF.stream.jsonl` (`end_turn`) |

The `Edit` row matters: `updatedToolOutput` was measured **blind at exactly the
commit points** (3 of 3 unappendable). `continue` is a top-level field, not tied
to a tool's output schema, so the stop channel has no such blind spot. The
`PostToolUseFailure` row is the counterweight: **the supervisor cannot stop the
actor right after a failed call**, which is when it is most likely spinning.

**Session state after termination is clean and resumable.** The transcript ends
on a well-formed `user`/`tool_result` turn; the stop itself lands as
`type: "attachment"`, `attachment.type: "hook_stopped_continuation"` chained
into the `parentUuid` chain (`runs/` transcripts, session `…120`). That
attachment is **absent from `stream-json`** and **present in the model's context
on resume** (below).

### 2. What does `--resume` read, and is it editable?

**A plain JSONL transcript at `$CLAUDE_CONFIG_DIR/projects/<slug>/<session-id>.jsonl`.
It is editable, and `--resume` performs no validation — measured.**

- Records are a `parentUuid`-linked chain of `user` / `assistant` messages
  interleaved with non-message records (`queue-operation`, `attachment`,
  `ai-title`, `atis-latch`, `mode`) and a `last-prompt` record carrying
  `leafUuid` — the resume head pointer.
- **No checksum, no signature, no schema rejection.** A hand-edited file
  resumed cleanly (`runs/rB.*`).
- **Hard prerequisite, measured the hard way:**
  `CLAUDE_CODE_SKIP_PROMPT_HISTORY=1` **disables transcript persistence
  entirely** — no `projects/` directory is created and `--resume` answers
  *"No conversation found with session ID"* (`runs/r1b.stderr.txt`). This
  variable is set by our own herdr temporary-agent convention
  (`~/.agents/AGENTS.md`), so any rig that adopts A must unset it.

### 3. Delete the last turn — what does the model then see?

**The deletion takes effect; residual references are silently repaired rather
than rejected — measured, and the silence is the hazard.**

| Edit | Result |
|---|---|
| Delete a matched (assistant `tool_use` + `user` `tool_result`) pair, repoint `last-prompt.leafUuid` | Resume succeeds; the deleted turns are gone from the reconstructed context (`runs/rB.*`, proxy rec 7) |
| Delete **only** the assistant `tool_use`, leaving an orphaned `tool_result` | **No error.** Claude Code silently dropped the orphaned `tool_result` *and* the preceding assistant `thinking` turn (`runs/rC.*`, proxy rec 13) |

So the danger is not rejection, it is that **what you edited is not necessarily
what the model sees**, and nothing tells you. The only way to know is to capture
the request.

**Cache cost of a deletion, measured.** Editing invalidates the prompt-cache
suffix from the edit point onward:

| Resume kind | `cache_read` | `cache_creation` |
|---|---|---|
| Unedited resume (append-only) | 35,453 | **224** |
| Same prefix, one turn deleted ~26 % back | 26,225 | **9,499** |

A 42× increase in cache-write tokens for one deletion at a 36 k prefix.

### 4. Can a real `user` turn be injected, and does it survive our converters?

**Injecting one is trivial. Surviving conversion is where it fails.**

**Injection needs no file surgery.** `claude --resume <id> -p "<text>"` appends
a genuine `user` turn: the transcript records `type: "user"`, `role: "user"`,
and the proxy shows it as the last `user` message of the next request
(`runs/r5b.*`, `runs/rD.*`). This is the *direct* replacement for the channel
that failed on provenance — a real user turn, not a segment appended to a tool
result.

**Converter survival (this repo's own `convert.py`, run by `analyze.py`):**

| Capture | messages | `<oracle_hint>` present? |
|---|---|---|
| `event_stream_to_conversation` over segment A + segment B concatenated | 20 | **No** |
| `proxy_log_to_conversation` over the run's proxy log | 21 | **Yes** |

The stream capture behaves exactly as [§10](../../../docs/trace-synthesis/spec.md#10-what-is-measured-about-hooks)
already records for the *initial* user message (0 of 3): a headless prompt —
first or resumed — is **never** emitted as a `user` event. Measured here on
three resumed segments: `user_events` are **all** `tool_result` turns,
`user_text_turns: 0` (`analyze.py`, first block). A resumed segment's stream
also carries **only** that segment — no prefix — so a run's trace is N files
that have to be stitched, and the stitching drops every hint.

**Verdict on criterion 3 of [§11](../../../docs/trace-synthesis/spec.md#11-open-questions):
the resume-injected user turn passes under proxy capture and fails under the
default stream capture.** Criteria 1 and 2 (the actor sees it; it is tagged)
pass.

**Weak, suggestive evidence on the credibility axis, offered as such.** Across
**5** resumed segments carrying an `<oracle_hint>` (`r3`, `r5b`, `rB`, `rC`,
`rG2`) the actor acted on the hint in **5 of 5** and objected on provenance in
**0 of 5** — its own `thinking` reads *"The user is giving me a hint…"*. That is
a striking contrast with the tool-output channel's 6-of-6 refusal, but it is
**not** a controlled comparison: trivial tasks, ~4-step horizons, one hint per
run, no pre-registration, no replicates, and a different prompt. It is a reason
to run a registered test, not a result.

### 5. What does resume cost?

**Essentially nothing in tokens; ~1 s of wall clock per intervention — measured.**

| Quantity | Reading |
|---|---|
| Extra API requests per resume | **0.** A *new* session fires one ~950-token title request (proxy recs 0, 20, 23); a **resume fires none**, and the first resumed request is the request the actor would have made anyway |
| Prompt cache on the resumed request | **Fully hit.** `cache_read` 35,453 vs 35,225 on the preceding in-session request; `cache_control` on the system block is `ttl: "1h"` |
| Extra cache-write at the resume boundary | `224` tokens, indistinguishable from ordinary in-session boundaries (`167`–`254`) |
| Process startup overhead | **0.92 s / 1.03 s** (2 runs: wall 3,324 / 3,259 ms minus `duration_api_ms` 2,297 / 2,339 ms) |

**Calibrated against the 20 `honesty_scorer` pilot attempts**
(`~/dev/swe-lab-artifacts/honesty_scorer/pilot/ledger.jsonl`):

| Pilot statistic | mean | median |
|---|---|---|
| `cache_read_input_tokens` | 1,703,372 | 1,272,055 |
| `cache_creation_input_tokens` | 29,519 | 19,218 |
| `total_cost_usd` | 1.093 | 0.698 |
| `rollout_wall_seconds` | 287.8 | 212.0 |
| `num_turns` | 33.4 | 28.5 |

Derived prefix scale ≈ `cache_read / num_turns` ≈ **51 k tokens per request**
(approximate: `num_turns` is the harness's turn count, not the API request
count — order of magnitude only).

At **6 interventions per rollout** (the [steered re-run](../steered_rerun/REPORT.md)'s
rate: 6 hints over 27 boundaries):

- **Injection only (no editing):** +6 s wall on a 212 s median rollout ≈ **+3 %**,
  and ~0 extra tokens. Negligible.
- **With a turn deleted at each intervention:** arithmetic extrapolation from
  the one measured deletion (26 % of a 36 k prefix re-written → 9.5 k tokens):
  ≈ 13 k cache-write tokens per deletion at a 51 k prefix, ×6 ≈ **80 k extra
  cache-creation tokens**, against a whole run's current mean of 29.5 k — i.e.
  roughly **3× the run's entire cache-write budget**. This is arithmetic on one
  measurement, not six measurements; the cache-write price multiplier over a
  cache read is **未核** in this session.

**The cost that is not in tokens** is the supervisor's own deliberation, and it
is unchanged by A: the decision is taken inside the `PostToolUse` hook, exactly
where a non-stopping supervisor would take it.

### 6. Is this what §6 bans?

**Deleting a turn: yes, squarely. Injecting a user turn: no. And the variant
that deletes nothing is still contaminated, which is the part that matters.**

**(a) Injecting a `user` turn is not banned.**
[§6](../../../docs/trace-synthesis/spec.md#6-the-trace-is-the-conversation-unedited)
bans removing from, or synthesizing into, the *collected trace*. A user turn
delivered before the actor's next request is **conditioning inside the
conversation** — which [§4](../../../docs/trace-synthesis/spec.md#4-why-judging-after-the-fact-and-not-before)
explicitly calls "a natural conversational shape" and
[§5](../../../docs/trace-synthesis/spec.md#5-the-mechanism-decisions) explicitly
admits ("*so would a real user turn*"). No ADR is needed for this half.

**(b) Deleting a turn is exactly what §6 bans.** The actor produced that turn
and observed its result; removing it from the session produces a conversation
**no actor ever had** — §6's "a reconstruction assembled offline", and §12's
intended invariant *"Conversion neither drops nor synthesizes turns"*. Worse,
it is self-defeating on the design's own terms: the trace then shows a clean
path reached by discarding the failures, which is rejection sampling at turn
granularity wearing a process-supervision costume, and it deletes the very
evidence that would explain the actor's next move. **Proceeding with deletion
requires a new ADR superseding §6**, and this report does not think one is
earnable.

**(c) The blocker is that the injection-only variant is contaminated too.**
Measured, in the model's own context and in the persisted transcript:

```
user  tool_result  ".\n..\nREADME.md\nnotes.txt\nwidget.py
                    <system-reminder>
                    PostToolUse:Bash hook stopped continuation: <stopReason>
                    </system-reminder>"
user  text         "Continue from where you left off."
assistant text     "No response requested."
user  text         "<oracle_hint>…</oracle_hint>"
```

- The `<system-reminder>` is **unsuppressable**: omitting `stopReason`
  substitutes the default *"Execution stopped by PostToolUse hook"*
  (`runs/r6.proxy.jsonl`, last record). It is delivered on the one channel
  §5 rules out by name (*"Not a system-reminder"*), it tells the actor it is
  being supervised, and it appends to the tool's own bytes — the
  *"a hint never replaces a tool's output"* invariant is honoured by *our*
  hook and broken by the platform.
- The repair pair is **persisted as real `user` and `assistant` messages**
  (transcript lines 19–20 of session `…120`) and survives proxy conversion
  (`proxy_has_repair_pair: True`).
- It appears **only when the leaf is an unanswered tool result** — resuming a
  cleanly-ended session adds nothing (`runs/rD.*`, `proxy` rec 12: the new user
  turn simply follows the last assistant turn). A mid-run supervision point is
  by construction an unanswered-tool-result leaf, so for A the pair is
  unavoidable. Editing the leaf to avoid it is (b).

And the escape hatch is closed: **a headless resume refuses to run without a
prompt.** `-p ""` and a bare `--resume` both exit 1 with *"Provide a prompt to
continue the conversation."* (`runs/tE.err`, `runs/tF.err`). So
"delete the bad turn and let the actor retry silently" is **not expressible** —
every intervention necessarily adds a user turn.

---

## What would have to change in swe-lab

Ordered by size. None of it is Claude Code surgery; all of it is ours.

1. **Capture must become proxy-based, or a transcript converter must be
   written.** This is the real cost. `convert.py` reads the `stream-json`
   stream or the proxy log and **never** the persisted transcript
   (its docstring says so); the stream loses every hint. The proxy already runs
   in-sandbox ([ADR-0012](../../../docs/decisions/ADR-0012-in-sandbox-capture-proxy.md)),
   so the plumbing exists — but `proxy_log_to_conversation` keeps only the
   **last record's** thread, which is fine across resume segments (the last
   record carries the whole conversation) and **not** fine if a subagent or a
   compaction lands last.
2. **The harness must drive N processes per rollout, not one.**
   `harnesses/claude_code/harness.py` runs one `claude` and writes one
   `event_stream.jsonl`. A supervisor loop needs per-segment stream files, a
   segment index, and a stitcher — plus the [§12](../../../docs/trace-synthesis/spec.md#12-invariants-intended-enforced-where-marked)
   no-silent-gaps record spanning segment boundaries.
3. **`CLAUDE_CODE_SKIP_PROMPT_HISTORY` must be unset** in the rollout
   environment, or nothing is resumable at all.
4. **[§12](../../../docs/trace-synthesis/spec.md#12-invariants-intended-enforced-where-marked)
   would need re-pointing.** "No banned channel is reachable in a hook
   response" stays true of *our* response and becomes false of the run: the
   platform injects the system-reminder itself. "Conversion neither drops nor
   synthesizes turns" becomes unsatisfiable while the repair pair exists.

---

## Unknowns — 未核

Listed because a feasibility report that hides its gaps is worthless.

- **Whether the repair pair can be avoided at a leaf that is an assistant
  `tool_use` with no `tool_result`.** Measured: unanswered `tool_result` leaf →
  pair appears; clean `end_turn` leaf → no pair. The third shape was not tested.
- **Compaction.** Pilot rollouts are long enough to compact; a compacted resume
  breaks the "last proxy record carries everything" assumption that item 1
  above rests on. Not exercised.
- **`--fork-session`.** Exists (`--help`); would leave the original session
  intact and branch. Not exercised.
- **`SessionStart` hook's `initialUserMessage`.** First-party schema, 2.1.257
  (`hookEventName: "SessionStart"`, `initialUserMessage: string`, consumed as
  `pendingInitialUserMessage`). A different injection point on resume. **Not
  measured**, and the initial-user-message channel is the one already known to
  vanish from stream capture.
- **`--input-format stream-json` ("realtime streaming input", per `--help`).**
  It would place a user turn into a *live* session with no stop and no resume,
  which bears directly on whether A is the cheapest route to a user turn.
  **Not measured; out of this brief's scope** — flagged, not designed.
- **Whether the stop's system-reminder changes actor behaviour.** The 5 compliant
  runs above all carried it. Trivial tasks, no control arm — the same weakness
  §10 already flags about the one trivial-task `decision: "block"` run.
- **Cache-write pricing multiplier** over a cache read (used only in the §5
  extrapolation).

---

## Side finding — a credential exposure, reported not fixed

While capturing, `cc-reverse-proxy` was run from
`/home/ubuntu/dev/cc-reverse-proxy/python/reverse_proxy.py` (HEAD `4d8d7e6`,
clean tree) — the same path `injection_shape/run_experiment.py` invokes. **That
build does not redact.** Its captures contained, in cleartext:

- `Authorization: Bearer sk-ant-oat01-…` — the live `dev-shared`
  `claude-code-oauth-token`;
- `Anthropic-Organization-Id`, `Anthropic-Workspace-Id`,
  `Anthropic-Ratelimit-Unified-Representative-Claim`, and
  `request.body.metadata.user_id` — operator identity.

The repo's redaction commits (`9fecf6e`, `f1d8148`) touched **`reverse_proxy.go`
only**; `python/reverse_proxy.py` was last modified by `942a11c`, before them.
Committed captures under `injection_shape/runs/` **are** clean (`<redacted>`),
so this is a regression in what the runner reaches for, not a committed leak.

Actions taken here: the two captures in [`runs/`](runs/) were masked with this
repo's own `swe_lab.harnesses.claude_code.redaction.redact_record`, and
`analyze.py --check-redaction` reports **0 findings** on both. **Nothing
containing the token has been committed.**

**Two things need a human.** (1) The token value was echoed into this research
session's transcript by a careless header dump before the exposure was noticed
— **recommend rotating `op://dev-shared/claude-code-oauth-token`.**
(2) Any experiment runner that uses the Python proxy should run
`redaction.unredacted_fields` over its capture before the capture is written to
a repo path; `injection_shape/run_experiment.py` does not.

---

## Reproduce

```sh
# every number in this report, from the checked-in artifacts (no network):
uv run python experiments/trace_synthesis/process_supervision/analyze.py
uv run python experiments/trace_synthesis/process_supervision/analyze.py --check-redaction

# to re-run the probes themselves (burns tokens; needs the OAuth token):
#   export CLAUDE_CODE_OAUTH_TOKEN=$(op read --no-newline --force \
#     "op://dev-shared/claude-code-oauth-token/credential")
#   export CLAUDE_CONFIG_DIR=<a scratch dir>
#   unset CLAUDECODE CLAUDE_CODE_SKIP_PROMPT_HISTORY   # both break the probe
#   claude -p "<task>" --output-format stream-json --verbose \
#     --session-id <uuid> --dangerously-skip-permissions \
#     --model claude-sonnet-4-5 --settings probes/settings.json
#   claude -p "<oracle_hint>…</oracle_hint>" --output-format stream-json \
#     --verbose --resume <uuid> --dangerously-skip-permissions \
#     --model claude-sonnet-4-5
# probes/settings.json and probes/*.py are the exact hooks used; their absolute
# paths point at the scratch dir the round ran in.
```

Run map: `r1*` persistence probes · `r2`/`r3` transcript shape and a plain
resume · `r4` the stop · `r5a`/`r5b` stop + resume behind the proxy · `rB`
turn deleted · `rC` dangling `tool_use` · `rD` resume of a cleanly-ended
session · `rE` stop at an `Edit` · `rF` `PostToolUseFailure` · `rG`/`rG2` stop
with no `stopReason` · `t1`/`t2` startup timing · `tE`/`tF` resume with no
prompt.
