# Steered re-run — design and how to run

**Serves** trace-synthesis
[task 01 step 5](../../../docs/trace-synthesis/plans/task-01-one-instance-end-to-end.md#step-5--the-steered-re-run-simulated-supervisor):
a blind actor runs an instance that phase A failed on, a Supervisor watches
every tool boundary against an Oracle's guidebook, and a hint is injected when
it goes off track. Findings live in [`REPORT.md`](REPORT.md); this file is the
design.

## Question

Not "does steering make the actor pass" — a steered run that still fails is a
complete result and feeds task 06 and the specificity dial
([spec §8](../../../docs/trace-synthesis/spec.md#8-what-hint-specificity-now-trades)).
What this round can actually answer:

1. Can a hint reach a blind actor **inside a real rollout**, over a 20–50 call
   horizon, rather than in task 02's 1–3 call toy task? — **yes**, 6 of 6 over
   27 boundaries.
2. Does **every** hint the host emitted survive into the converted trace — and
   when one does not, is the loss detectable? (The spec's one fatal failure
   mode.) — **yes**, and the proof is a three-way join, not a log
   ([`reconcile.py`](reconcile.py)).
3. Do the hints stay **directional**, or slide into specifics? — they stayed
   directional; all six are quoted in the report so a reviewer can disagree.
4. Is the instance a **genuine reasoning failure** at all, or a broken task?
   ~30% of SWE-bench Pro public is broken
   ([survey](../../../docs/research/swebench-pro-task-quality.md)), so this is
   a gate on the other three, not a footnote. — **yes**, and the gate turned out
   to need a third check nobody had: whether the actor ran at all.

The question it could **not** answer, and the one that now matters most: the
actor recognized the injection, refused all six hints on provenance, and told
the operator its tool output was being tampered with. Delivery is solved;
**credibility is the open problem** ([`REPORT.md`](REPORT.md)).

## Method

### The rig

Three processes, and which one holds what is the whole design:

| | Where | Holds |
|---|---|---|
| **Supervisor** ([`supervisor.py`](supervisor.py)) | host | the guidebook, the belief state, the hint log, the model credential |
| **hook** ([`steer_hook.py`](steer_hook.py)) | inside the instance container | nothing — it describes a boundary, asks, and appends what comes back |
| **actor** | inside the instance container | the ordinary task prompt, and no idea any of this exists |

The guidebook never enters the sandbox, so
[spec §3](../../../docs/trace-synthesis/spec.md#phase-c--the-guided-rollout)'s
"the belief state lives host-side, outside the sandbox" is mechanical here
rather than promised. What crosses the boundary is one sentence of hint text.

The Supervisor is **never a nested `claude`** — the `CLAUDECODE=1` guard and the
recorded recursive cost-explosion incidents
([spec §10](../../../docs/trace-synthesis/spec.md#10-what-is-measured-about-hooks))
rule that out. It is a host process the hook reaches **through the shared
workspace**, not over the network: this box runs `ufw` with
`default deny (incoming)`, so nothing on the Docker bridge can open a host port
(measured 2026-09-01). A file drop turned out to fit the isolation argument
better than the HTTP version it replaced — the sandbox gets no route to the host
at all, only a directory it already shared.

### The setting, and why the last round's numbers are not comparable

| | This round | Previous round |
|---|---|---|
| auth | OpenRouter API key (`pass_env`, never on a command line) | subscription OAuth token |
| key | one of a 25-key pool, chosen by `--key-index` | n/a |
| base URL | `https://openrouter.ai/api` | the Anthropic API |
| actor model | `anthropic/claude-sonnet-5` | `claude-sonnet-5` |
| `bare` | **`False`** — see below | `False` |
| subagents | denied (`--disallowedTools …,Task`) | allowed |
| capture | **`proxy`, required** — see below | `stream` |

**`bare=True` was specified and cannot be used: `--bare` disables hooks
outright**, `--settings`-supplied ones included. Measured 2026-09-01, the same
one-tool probe run twice with only that flag differing: with `--bare` the hook
never fires and no hint reaches the actor; without it the hook fires and the
hint lands. Hooks *are* the mechanism. The reason bare mode was wanted — no
subagents, so the proxy converter's
[thread-loss defect](../injection_shape/REPORT.md) cannot bite — is bought with
`--disallowedTools …,Task` instead, at the cost of one argument.

**The pool is 25 accounts, not 25 doors onto one balance** — each key carries
its own credit (measured 2026-09-01: 25 of 25 live, $117–$278 each). So runs
are accounted per key: `summary.json` records the key's index, an irreversible
8-character fingerprint of it, and its own balance before and after. A key is
rotated because OpenRouter rate-limits per key and parallel harvests should not
share one — not to spread spend, which is not a constraint here. (We have not
hit a 429; the per-key limit is OpenRouter's documented model, not something
this round measured.)

### `capture="proxy"` is required, not an arm

An earlier design ran `stream` and `proxy` as two arms. That is **withdrawn**.
On the OpenRouter path, `cc-reverse-proxy` injects two things Claude Code does
not send for itself:

- it mirrors `Anthropic-Beta` to `X-Anthropic-Beta`, the header OpenRouter
  actually reads — without it interleaved thinking does not happen;
- it pins a provider preference with `require_parameters: true` — without it
  OpenRouter may route to a provider that quietly drops `thinking`.

Both failures are **silent**. So a direct-to-OpenRouter `stream` run is not
another capture of the same configuration; it is a *different model
configuration*, and the two are not comparable. There is no stream/proxy
comparison in this round's report.

**The guard, and why the obvious version of it is not enough.** The cheap check
— "the trace carries signed reasoning blocks" — **passes on the degraded path**:
measured 2026-09-01, a direct-to-OpenRouter `stream` run with no proxy at all
scored 10 signed blocks out of 10. What discriminates is the *request* side:
whether later request bodies echo prior assistant `thinking` blocks back, which
is what interleaved thinking across turns means and is visible only on the wire.
[`analyze.py`](analyze.py) reports both, and reports `-1` rather than `0` when
there is no proxy log, so "not measurable" never reads as "measured zero".

### The injection shape is settled, not explored

[Task 02](../injection_shape/REPORT.md) measured it: `PostToolUse`
`updatedToolOutput`, the tool's real output preserved and a hint appended after
it inside an `<oracle_hint>` tag, with a neutral body that does not claim to be
the user. This round uses it as given.

**One boundary the channel cannot reach**, found here: `Edit`'s `tool_response`
is `{filePath, structuredPatch, userModified, …}` with no free-text field, and
`updatedToolOutput` is validated against the tool's declared schema — so there
is nowhere to append. The first steered run judged three interventions, all
three at `Edit` boundaries, and **none reached the actor**. The channel is blind
at exactly the commit points where steering matters most. The hook now tests
whether a boundary can carry a hint *before* asking, and the Supervisor carries
an unreachable intervention forward to the next boundary that can take one.

`PostToolUseFailure` is hooked too, but only to keep the belief state complete
across a failing call: it accepts `additionalContext` alone, which the stream
capture drops, so it is never injected into.

### The task-quality gate

**Screening moved out of this experiment** on 2026-09-01: `swelab-screen-impl` /
`-review` audit all 40 of issue #261's instances and hand confirmed candidates
over. What follows is the gate as it ran here, kept for the methodological
result and for one verdict it got wrong.

Applied **before** an instance is used, and it is a gate rather than a caveat:
OpenAI's 2026-07-08 audit estimates ~30% of SWE-bench Pro public tasks are
broken and retracts their recommendation to adopt the dataset. The criterion is
**determinacy** — is the behavior the hidden tests judge pinned down by the
problem statement, the requirements, the interface, and the repo at
`base_commit`? Issue #261 selects on *mixed outcome*, so "some rollout passed"
is true of every candidate and screens nothing: a coin flip between two
self-consistent readings is reachable and still not pinned.

[`validate_task.py`](validate_task.py) assembles the evidence and runs the one
mechanical screen; the verdict is written by hand into
`task-validation/<instance>.md` with the evidence, so a reviewer can re-derive
it. Both screens have earned their place and neither subsumes the other:

| Instance | requirements/interface diagonal | unpinned-token screen | verdict |
|---|---|---|---|
| openlibrary | **fires** (`The method` vs `Type: Function`) | clean | broken — misleading prompt |
| ansible | clean | **fires** (`destinationAddress`) | broken — underspecified prompt |

### Hint policy

Scratch knobs, not spec decisions, stated so the report's numbers can be read:
at most **8** hints per run, and **2** boundaries of cooldown after each, so the
actor has room to act on the hint it has. A suppressed hint is still logged with
its reason — the log records what the Supervisor *judged*, not only what it
managed to send.

### Scratch, not production

Nothing here is wired into a shipped workflow definition. `run_steered.py` takes
the shipped `ROLLOUT` entry, swaps its harness for a subclass that mounts two
extra files and edits the agent's argv, and chains the shipped `UNIT_TEST` entry
unchanged — so the verdict is the same verdict phase A was measured with. Both
arms run the *same* subclass and therefore the same argv; the control's settings
file simply declares no hooks, so the hook is the only variable. Tasks 03 / 05 /
09 are what turn any of this into something the repo keeps.

## Run

```sh
# screen a candidate before spending a rollout on it
direnv exec . uv run python experiments/trace_synthesis/steered_rerun/validate_task.py <instance-id>

# the control and the steered run; strictly sequential (each rmtree's the
# shared cache directory)
# `--key-index` is what makes two of these safe to run at once; it defaults to
# a single value, so concurrent runs must pass it. The capture proxy needs no
# such flag: it runs inside the sandbox, on a fixed loopback port in the
# container's own network namespace, so two runs cannot collide (ADR-0012 §2)
direnv exec . uv run python experiments/trace_synthesis/steered_rerun/run_steered.py \
    --label baseline-<repo> --instance <id> --rollout-id 0 --no-steer
direnv exec . uv run python experiments/trace_synthesis/steered_rerun/run_steered.py \
    --label steered-<repo> --instance <id> --rollout-id 10 \
    --guidebook guidebook/<instance>.md

# every number in the report
direnv exec . uv run python experiments/trace_synthesis/steered_rerun/analyze.py
```

## Layout

```
supervisor.py     host-side judge: guidebook + belief state + hint log
steer_hook.py     the in-container hook; holds nothing, appends what it is told
run_steered.py    one run end to end: supervisor up, rollout, grade, freeze
validate_task.py  the task-quality gate: determinacy evidence + token screen
freeze_sample.py  a harvested failure -> the workflow's self-contained input
analyze.py        raw runs -> analysis.json (hint survival is the load-bearing check)
reconcile.py      the three-way join: host log / sandbox hook log / converted trace
manifest.sh       regenerates MANIFEST.md over the frozen artifacts
guidebook/        one Oracle guidebook per instance; `--steer` refuses without one
runs/<label>/     hint_log.jsonl (host-side, every judgement) + summary.json
task-validation/  one dossier per screened instance, verdict written by hand
```

The **sample directory** `freeze_sample.py` writes is a contract, not a
convenience: the phase-C workflow starts from an already-cached failure rather
than re-running a rollout to find one, so its input is a hand-assembled dataset
row — `instance.json`, `failed_conversation.json` (typed, with the raw trace
kept beside it), `verdict.json`, `patch.diff`, `PROVENANCE.json`,
`MANIFEST.sha256` — and the invariant is that a consumer needs that directory
and nothing else. The guidebook agent is built against it; see
[`REPORT.md`](REPORT.md#8-the-failure-sample-is-the-workflows-input-contract).

**Large artifacts live outside every git worktree**, at
`~/dev/swe-lab-artifacts/trace_synthesis/`, with
[`MANIFEST.md`](MANIFEST.md) pointing at them by sha256. Not fastidiousness: the
previous round's frozen tree was gitignored *inside* a worktree and
`git worktree remove` deleted it without a word
([hazards](../../../docs/conventions.md#hazards-learned-the-hard-way)).

**The proxy log carries credentials and operator identity, and it is now
redacted at write time.** `cc-reverse-proxy` masks the request's
`Authorization` / `X-Api-Key` / `Cookie` / `Proxy-Authorization`, the request
body's `metadata.user_id`, and the response's `Anthropic-Organization-Id` /
`Anthropic-Workspace-Id` / `Anthropic-Ratelimit-Unified-Representative-Claim` /
`Set-Cookie` **as it records each exchange**
([ADR-0012 §4](../../../docs/decisions/ADR-0012-in-sandbox-capture-proxy.md)),
so an unredacted capture never exists on disk. Verified on this rig rather than
assumed: a real one-turn run through the migrated harness (2026-09-01) captured
`x-api-key: [REDACTED]` and `unredacted_fields()` returned empty.

`run_steered.py` still rewrites every captured log in place before freezing.
That pass is now the **second belt, not the mechanism**: the proxy is an
external, separately versioned binary, so "the build we ran redacts" is exactly
the assumption that stops holding without anyone noticing.
[`redact_record`](../../../src/swe_lab/harnesses/claude_code/redaction.py) is
the one home for what counts as sensitive, and it masks idempotently.

**The captures from the earlier round are a different matter and the old
warning still applies to them.** Everything frozen before 2026-09-01 was
redacted *after the fact*, so an unredacted file existed on disk for the length
of those runs; those artifacts must not leave this machine — not into the repo,
not into a PR, not quoted in a message. Don't read this section as precedent
that post-hoc redaction is sufficient: it never was, which is why the fix went
to write time.

## Limits

One instance, one actor model, one run per arm. Enough for the mechanical
questions — did a hint reach the actor, did it survive conversion, did the
tool's output stay intact — which are deterministic properties of code and
channels. **Not** enough for a compliance rate, and not enough to attribute a
verdict: read the resolved/unresolved column with the baseline sampling in
[`REPORT.md`](REPORT.md) beside it, never on its own.
