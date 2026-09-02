# Trace capture fidelity — what each of the three capture paths can actually see

**Date:** 2026-08-07 · **Kind:** engineering study (a snapshot, not a spec)

The question that started this: **does our `conversation.json` capture Claude
Code's injected `<system-reminder>` blocks?** The answer is no, and chasing
*why* turned out to map a bigger thing — Claude Code writes its turn data at
**three different points in its pipeline**, each of which loses something
different, and swe-lab currently reads only the first.

Nothing here proposes a change to ship. It records the evidence so the eventual
"capture everything" work starts from measurements rather than a re-derivation.
Sources are the vendored CLI source in the sibling `coding-cli-survey`
(`submodules/claude-code`), our own code, and three live runs — one of them a
Docker run made for this study.

## 1. The one-paragraph answer

A `<system-reminder>` is **not a message Claude Code stores**. It is *rendered*
at the last moment before the HTTP request, out of structured "attachment"
records. So there are three places to tap, and they hold three different
things:

| | STREAM (`event_stream.jsonl`) | Session transcript (`~/.claude`) | PROXY (wire log) |
|---|---|---|---|
| Rendered `<system-reminder>` text | ❌ | ❌ | ✅ verbatim |
| Structured attachment records | ❌ | ✅ richest | ❌ (already flattened to text) |
| Per-tool-result reminders (FileRead malware warning, …) | ❌ | ❌ | ✅ |
| System prompt | ❌ | ❌ | ✅ |
| `AgentOutcome` (the 8-state ending, [ADR-0011](../decisions/ADR-0011-fair-retry.md)) | ✅ full | — | ❌ degraded |

**STREAM is what we ship today.** It is the only path with a trustworthy
outcome signal and the only one that sees *none* of the injected context.

## 2. Why — the pipeline, with line references

Claude Code's turn data forks three ways, in this order:

```
   getAttachmentMessages()                    query.ts:1578
        │
        ├──> yield attachment ────────────────> QueryEngine case 'attachment'   QueryEngine.ts:829
        │                                          ├─ recordTranscript(...)  ──> ① TRANSCRIPT
        │                                          └─ stdout: only max_turns_reached
        │                                             + queued_command; else `break`
        │                                                                    ──> ② STREAM (nothing)
        └──> toolResults.push(attachment)          query.ts:1589
                    │
                    └──> normalizeMessagesForAPI()                            messages.ts:1989
                            └─ ensureSystemReminderWrap()                     messages.ts:2276
                                  └──> HTTP request                        ──> ③ PROXY
```

Three load-bearing facts, each verified in source:

**(a) The `<system-reminder>` wrapper is applied on the API path only.**
`ensureSystemReminderWrap` is called from exactly one place —
`messages.ts:2276`, inside `normalizeMessagesForAPI` (defined at 1989). Both
other taps are upstream of it.

**(b) The stdout stream never sees an attachment.** `QueryEngine.ts:829`'s
`case 'attachment':` pushes the record to the transcript
(`if (persistSession) { messages.push(message); void recordTranscript(messages) }`)
and yields to stdout for exactly two subtypes — `max_turns_reached` (re-emitted
as a terminal `result`) and `queued_command` (as a user replay). Every other
attachment `break`s without reaching stdout.

**(c) Per-tool-result reminders are generated even later, by the tool.**
`FileReadTool.ts:729` defines `CYBER_RISK_MITIGATION_REMINDER` (the "consider
whether it would be considered malware" block) and appends it at line 700 —
inside `mapToolResultToToolResultBlockParam` (652), which `Tool.ts:206`
documents as sitting at *"the `normalizeMessagesForAPI` boundary"*. So this
class of reminder exists **only** in the wire request: not in the stream, and
not in the transcript either.

The same is true of the sibling warnings on that path, e.g.
`<system-reminder>Warning: the file exists but the contents are empty.
</system-reminder>` (FileReadTool.ts:706).

## 3. Evidence — STREAM

A real 200 KB rollout capture (`.cache/store/runs/smoke23/…/rollout/a0/`,
flipt, 51 assistant turns):

```
event types in claude_code.event_stream.jsonl
  ('system','init')              1     ('assistant', None)          51
  ('system','thinking_tokens')  44     ('user', None)               30
  ('system','task_started')      5     ('rate_limit_event', None)    1
  ('system','task_notification') 5     ('result','success')          1

grep -c "system-reminder"  raw event stream : 0
grep -c "system-reminder"  conversation.json: 0

conversation.json roles : {assistant: 51, user: 30}      ← no SYSTEM message
conversation.json blocks: {tool_use: 30, tool_result: 30, text: 12, reasoning: 9}
```

The `system/init` event carries `agents, apiKeySource, capabilities,
claude_code_version, cwd, mcp_servers, memory_paths, model, permissionMode,
plugins, session_id, skills, slash_commands, tools, …` — **no field holds the
system prompt text**. So STREAM loses the system prompt as well as every
reminder.

What STREAM *does* have, uniquely, is the terminal `result` event — the eight
`AgentOutcome` states ADR-0011's fair-retry policy is built on.

## 4. Evidence — the session transcript

### 4a. A long interactive session (24.3 MB, host)

```
record types                          attachment subtypes
  assistant             3765            task_reminder          266
  user                  2449            edited_text_file       120
  pr-link                635            queued_command          69
  mode                   541            file                    12
  last-prompt            540            date_change              8
  ai-title               521            deferred_tools_delta     5
  attachment             492            agent_listing_delta      4
  system                 242            compact_file_reference   3
  queue-operation        182            skill_listing            2
  file-history-snapshot  158            invoked_skills           2
  file-history-delta      95            command_permissions      1

grep -c "system-reminder": 1            ← one literal occurrence in 24.3 MB
system prompt text present: False

size split: assistant 44.9% · user 39.5% · file-history-snapshot 8.0%
            attachment 5.1% · last-prompt 0.6% · pr-link 0.5%
```

The attachments are **structured, pre-render payloads** — strictly *more*
information than the rendered text, just not the text:

```json
{"type": "edited_text_file", "filename": "/…/tests/test_sandbox_manager.py", "snippet": "1\t\"\"\"Lifecycle tests…"}
{"type": "date_change",      "newDate": "2026-07-31"}
{"type": "task_reminder",    "content": [], "itemCount": 0}
{"type": "skill_listing",    "content": "- find-skills: …", "names": [...], "skillCount": N, "isInitial": true}
```

### 4b. Does it exist inside our sandbox? — a live Docker run

Made for this study, faithful to the shipped rollout in everything that could
matter (same `ClaudeCodeHarness`, same invocation script with `HOME=/agent-home`
and `-p`, `bare=False` + OAuth, same `DockerHostSandboxConfig` and binary
provisioning); only the image and prompt are cheap. Image `debian:stable-slim`,
`max_turns=12`, prompt: *write `/work/notes.txt`, read it back, reply DONE*.

**Yes — it is written, at the path the source predicts:**

```
/agent-home/.claude/projects/-work/73fa114e-ac34-4824-bcd9-8cb42f54f6d2.jsonl   16799 bytes
```

```
transcript record types : {queue-operation: 2, user: 3, attachment: 3,
                           ai-title: 1, assistant: 3, last-prompt: 1}
transcript attachments  : deferred_tools_delta (692 B)
                          agent_listing_delta (1804 B)
                          skill_listing       (6703 B)
event_stream attachments: 0

grep -c "system-reminder"  transcript  : 0
grep -c "system-reminder"  event stream: 0
```

**The decisive observation.** The agent really did call `Write` then `Read` on
`/work/notes.txt`. The transcript stores that `Read`'s tool result as:

```
'1\tHELLO\n2\t'
```

— the line-numbered content and **nothing else**. No malware reminder. This is
§2(c) confirmed end to end: the highest-frequency reminder in real coding runs
never reaches the transcript.

Persistence is on by default and we do not have to enable anything:
`persistSession = !isSessionPersistenceDisabled()` (`QueryEngine.ts:240`,
`print.ts:4906`), and `sessionPersistenceDisabled` is initialised `false` at
`bootstrap/state.ts:365` and **never set true anywhere in the source**. A
one-turn non-interactive run on the host (9.7 KB) also persisted its two
attachments, so this is not an artifact of long sessions.

### 4c. Collecting it would be easy

`getClaudeConfigHomeDir()` is
`process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), '.claude')`
(`envUtils.ts:7–14`), and `getProjectsDir()` is `join(…, 'projects')`
(`sessionStorage.ts:199`). Exporting `CLAUDE_CONFIG_DIR` into the workspace
would land the transcript as an ordinary collected artifact with **no copy
step** — same treatment as `event_stream.jsonl`.

### 4d. …but it is not worth much on its own

Two measurements argue against collecting it as a reminder source:

- **It does not carry the reminders that matter.** Per §4b, the per-tool-result
  class is absent entirely.
- **Most of its bulk is constant.** In the Docker run, `skill_listing` +
  `agent_listing_delta` + `deferred_tools_delta` = 9.2 KB of a 16.8 KB
  transcript (**55%**), and those are environment boilerplate identical across
  every run of a sweep. Same run: `event_stream.jsonl` 6864 B, transcript
  16799 B (**2.4×**), `conversation.json` 1313 B.

Its real value is different and worth keeping in mind: the listings are an
**environment fingerprint** (which skills, agents, deferred tools and MCP
servers the sandbox actually offered the agent), which is an integrity and
reproducibility signal rather than a conversation one.

## 5. Evidence — PROXY

The proxy records the wire request, which is downstream of
`normalizeMessagesForAPI`, so the reminders are in it verbatim. Our existing
`proxy_log_to_conversation` already preserves them — checked against a record
shaped like the real wire form:

```
system     text          <system-reminder> present: False   ← the system prompt itself
user       tool_result   <system-reminder> present: True    ← smooshed into a tool result
user       text          <system-reminder> present: True    ← standalone reminder block
assistant  text          <system-reminder> present: False
```

No model change is needed for any of this: a reminder is just text, landing in
`ToolResultBlock.content` or as a `TextBlock`. **The gap is purely the capture
strategy, never the `Conversation` schema.**

This extends the prior finding in
[`task-08-proxy-capture.md`](../horizontal/plans/task-08-proxy-capture.md) §5,
which recorded that "proxy additionally carries the SYSTEM turn (a richer
capture)" — true, and it turns out to undersell the difference: proxy also
carries every injected reminder.

### What PROXY costs today

Its `AgentOutcome` is degraded — the known gap recorded in ADR-0011 and as F4
of the [2026-07-29 outcome-states review](2026-07-29-rollout-outcome-states.md).
A proxy log evidences API traffic, not the agent loop: a run that hit
`--max-turns`, and a run that crashed *after* its last response, both end on a
perfectly complete final response. `proxy_log_outcome` therefore resolves the
ambiguity towards `FINISHED` (not retryable), trading a missed retry for never
inflating a score.

## 6. The most useful thing this study found

**PROXY's weak outcome signal is self-inflicted, not a protocol limit.** In
`harnesses/claude_code/harness.py::_invocation_script`, the proxy branch
downgrades the agent's own stdout and throws it away:

```python
if self.capture == "proxy":
    output_format = "json"            # downgraded from stream-json
    capture_redirect = "> /dev/null"  # and discarded
```

Keeping `stream-json --verbose` redirected to the event-stream file *while* the
proxy records would give **both** at once — proxy for content (reminders +
system prompt, verbatim), stream for the terminal `result` event (the full
eight-state `AgentOutcome`). That would close ADR-0011's recorded PROXY gap and
the review's F4 in the same stroke, and it is a small change to one script.

It is **not made here** — it is untested, the proxy is expected to be rewritten
first, and this document is a study. Recorded so the work starts from this
rather than from scratch.

## 7. What is NOT verified

Stated plainly, because the repo's rule is that a claim names what was actually
run:

- **Version skew.** The vendored source is a **2026-03-31 leaked snapshot**; the
  binary our sandbox runs is **2.1.212**. Every source line cited above is from
  the snapshot. The Docker run (§4b) agrees with it on every point it could
  test, which is the best corroboration available, but a subtype or an
  injection point could have moved.
- **The PROXY + STREAM combination of §6** has never been run.
- **PROXY under auto-compaction.** We reconstruct the whole conversation from
  the *last* proxy record (Anthropic is stateless). If a session auto-compacts,
  whether earlier turns and their reminders survive in that final request is
  untested.
- **`bare=True`.** The shipped rollout runs `bare=False` and so did §4b. Bare
  mode removes reminder *sources* (CLAUDE.md, hooks, MCP); which built-in ones
  remain was not measured.
- **Transcript size at real rollout scale.** Only the two extremes were measured
  (3-turn: 16.8 KB; long interactive: 24.3 MB).
- §5's proxy check used a **synthetic record shaped like** the real wire form,
  not a captured live proxy log.

## Sources

- `submodules/claude-code/src/query.ts` (1578–1589, 845–865) — the fork
- `…/src/QueryEngine.ts` (240, 829–892) — attachment handling, transcript write
- `…/src/utils/messages.ts` (1791–1836, 1989, 2276) — the reminder wrap
- `…/src/tools/FileReadTool/FileReadTool.ts` (652, 700, 706, 729) and
  `…/src/Tool.ts` (206) — per-tool-result reminders
- `…/src/utils/envUtils.ts` (7–14), `…/src/utils/sessionStorage.ts` (199),
  `…/src/bootstrap/state.ts` (365) — transcript location and persistence
- ours: `harnesses/claude_code/{harness,convert}.py`, `conversation/model.py`,
  `docs/horizontal/plans/task-08-proxy-capture.md` §5,
  [ADR-0011](../decisions/ADR-0011-fair-retry.md)
- runs: `.cache/store/runs/smoke23/…` (STREAM, flipt); a host session
  transcript (24.3 MB); the §4b Docker run (2026-08-07)
