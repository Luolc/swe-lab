# Spec: Oracle-guided trace synthesis

> **Scope:** a new component, parallel to the [horizontal
> foundation](../horizontal/) rather than inside it — it consumes the engine,
> harness and workflow layers but adds a product line of its own (training-data
> synthesis) rather than shared plumbing. **Status lives only in
> [`plans/README.md`](plans/README.md)**, this component's task index.
>
> The mechanism decisions in [§5](#5-the-mechanism-decisions) were taken by the
> owner on 2026-08-31 and 2026-09-01 and are the design of record; earlier
> framings (a `PreToolUse` deny-and-redirect gate, and a phase D that surgically
> removed the interventions) are superseded and do not appear here.

## Objective

Produce **SFT training traces** for a coding agent on tasks in the *hard but
not impossible* band — around `pass@10 ≈ 3/10` — in which **every assistant
token is supported by the context visible before it**.

That second clause is the whole point. A trace is only worth training on if the
reasoning in it could have been produced by a model that knew only what the
trace shows it knowing. A trace that reaches the right answer by a route the
model could not have derived is worse than no trace: it looks like a good
example, and what it teaches is confident guessing.

It is a **design target, reached by construction rather than by a checker.**
The construction argument is [§6](#6-the-trace-is-the-conversation-unedited),
and it is an argument, not a test: the earlier framing's per-trace inducibility
checker was dropped *because* of it ([§7](#7-what-that-simplifies)). What
[§12](#12-invariants-intended-none-enforced-today) can pin is the mechanical
half — that no intervention the actor received goes missing from the trace —
and beyond that the check is a human reading a sample
([§15](#15-success-criteria)).

## 1. The problem

Two obvious ways to get traces on this difficulty band both fail, and they fail
differently:

- **Rejection sampling** — run rollouts, keep the ones that pass. At 3/10 you
  burn roughly three rollouts per kept trace, and the ones you keep are the
  *lucky* ones. They frequently stumble into the answer, so what the data
  teaches is stumbling.
- **Imitating a privileged expert** — let an agent see the golden patch and
  write the trace. The result contains reasoning no blind model could have
  produced: "the fix is in `foo/bar.py`", with no derivation. Training on it
  teaches the model to *assert* knowledge it does not have, which is the
  shortest known path to a confident hallucinator.

## 2. What this is

The third way is an old idea in a new medium: **privileged-expert distillation
with on-policy correction**.

- *Learning by Cheating* (Chen et al., 2019) — a privileged agent sees ground
  truth; a blind student imitates it.
- *DAgger* (Ross et al., 2011) — the student rolls out and the expert corrects
  at the states the **student** actually visits, which is what removes the
  distribution shift plain behaviour cloning suffers from.

What is new is the medium of the correction: it is delivered **inside the
conversation, visibly marked as an interjection from outside**
([§11](#11-open-questions)), and it must be honest — vague enough that the
model's own next reasoning is a real derivation rather than the execution of an
instruction.

## 3. The pipeline

Four phases. A and B are offline and privileged; C is the run that produces the
trace; D is collection.

```mermaid
flowchart TD
  A["<b>A.</b> baseline rollout + eval<br/><i>keep: failed, but the task is solvable</i>"]
  G["golden patch + golden tests<br/>+ repo at base_commit"]
  B["<b>B.</b> Oracle<br/><i>privileged</i>"]
  GB[["guidebook.md<br/><i>private — never enters the actor's context</i>"]]
  C["<b>C.</b> fresh env, same prompt, <b>blind</b> actor"]
  T["tool call runs to completion"]
  S["Supervisor judges the <i>result</i><br/><i>guidebook + belief state, host-side</i>"]
  H["inject a short <b>tagged hint</b><br/><i>direction only, never specifics</i>"]
  D["<b>D.</b> collect the conversation<br/><i>unedited</i>"]

  A --> B
  G --> B
  B --> GB
  GB -.privileged, host-side only.-> S
  C --> T --> S
  S -->|on track| T
  S -->|off track| H --> C
  C --> D
```

### Phase A — baseline rollout and eval

Existing machinery ([`rollout_and_unit_test`](../horizontal/plans/task-22-late-binding-workflows.md)).
It produces a conversation, a patch and a verdict. We keep the instances where
the verdict is **failed** while the task is **known solvable** (the gold
self-test resolves). That pair — a real failure on a real solvable task — is
the raw material.

Measuring the `pass@10 ≈ 3/10` band properly costs ten rollouts per instance.
"One failed rollout plus a passing gold self-test" is the cheap proxy, at the
price of a fuzzier band; which of the two we use is [an open
question](#11-open-questions).

### Phase B — the Oracle

A fresh agent with **privileged access** to:

- the full conversation of the failed rollout — what went wrong, in detail,
- the golden patch and the golden test patch,
- the repository at `base_commit`.

It produces one artifact: a **guidebook** — a staged, step-by-step document
telling a *future, blind* agent how to solve this task. One entry per stage:

| Field | Content |
|---|---|
| `goal` | what this stage establishes |
| `actions` | which files to open, which searches to run, which commands |
| `expected observations` | what you should see if you are on the right track |
| `justification` | **how you could have known to do this** — grounded only in the problem statement and earlier stages |
| `exit criteria` | what tells you the stage is done |
| `edits` / `tests` | for the stages that change code |

Two rules on the guidebook:

1. **It should never say it saw the answer.** It reads as a tutorial, not as a
   diff summary. This is a property of generated prose and **nothing mechanical
   can check it** — the check is a human reading a sample, so it stays a rule
   for the Oracle's prompt rather than an invariant.
2. **Every step carries a derivable justification.** This is not cosmetic. The
   Supervisor in phase C can only produce an honest nudge toward a step if a
   derivable reason for that step exists. A guidebook that says "open
   `foo/bar.py`" with no reachable *why* leaves the Supervisor nothing to say
   except the answer. The schema can enforce that the field **exists**
   ([task 04](plans/README.md)); whether the reason is genuinely derivable is
   again a reader's judgement, not a test.

The guidebook is **private**: it is intended never to enter the actor's context
or the training trace. Nothing enforces that yet — see
[§12](#12-invariants-intended-none-enforced-today) for the test that must land
with the code.

### Phase C — the guided rollout

A fresh sandbox, the same prompt, an actor with **no** privileged information —
an ordinary rollout, as far as the actor is concerned.

The steering runs **after each tool call completes**. The tool executes
normally; the hook hands the call and its result to a host-side **Supervisor**
model together with the guidebook and the **belief state** — a running summary
of what an honest, blind agent would know from the observations so far. The
Supervisor then takes one of two branches:

- **On track** — do nothing; update the belief state.
- **Off track or looping** — inject a short **tagged hint**: the sort of thing
  a person watching over the agent's shoulder would say ("I don't think that's
  the right direction — have you considered how this interacts with X?"),
  giving **a direction and never a specific**, and wrapped in a marker that
  says it came from outside ([§11](#11-open-questions)).

The belief state lives **host-side, outside the sandbox**. If the actor could
read a file written with oracle knowledge, the construction collapses — which
is why that, too, is one of the
[invariants](#12-invariants-intended-none-enforced-today) a test has to pin.

### Phase D — collection

The conversation of the phase-C run **is** the training trace. **Nothing is
removed and nothing is synthesized** — see
[§6](#6-the-trace-is-the-conversation-unedited); that is an
[invariant](#12-invariants-intended-none-enforced-today) with a named test, not
just a statement of intent. D converts the harness's
`stream-json` into the typed
[`Conversation`](../horizontal/plans/task-06a-conversation-protocol.md) and
persists it with the run's records.

D is a pure conversion: the recommended channel survives it
([§11](#11-open-questions)), so there is nothing to materialize. What D does owe
the design is the **guard** — a hint the converter cannot represent must fail
the conversion rather than produce a hint-less trace
([task 03](plans/README.md)).

## 4. Why judging after the fact, and not before

The steering point is *after* the tool result, not before the tool call, and
the three reasons are structural rather than convenient:

1. **Judging after the observation is a far easier problem than predicting
   before it.** A pre-execution gate must answer "will this action turn out to
   be bad?" blind. A post-hoc judge answers "did that actually move us
   forward?" with the evidence in hand — fewer false interventions, and the
   ones it makes are better grounded.
2. **No fabricated tool failures.** A denial writes a `tool_result` with
   `is_error: true` for a call that would have succeeded — a lie inside the
   trace, which something downstream then has to remove.
3. **A user turn is a natural conversational shape.** People really do
   interrupt an agent with "I think you're going the wrong way". The
   conversation stays well-formed with no repair at all.

## 5. The mechanism decisions

Owner-decided (2026-08-31 / 2026-09-01). These are the design of record.

| Decision | Rationale |
|---|---|
| **Steer from a Claude Code hook** — not the proxy, not our own agent loop | The proxy ([task 08](../horizontal/plans/task-08-proxy-capture.md)) is already complex and folding steering into it couples the two badly. Our own agent loop abandons the point of this infrastructure, which is to hug the harness we actually want traces of. |
| **Never rewrite the tool call** | Measured: a rewritten call is *not* reflected back in the assistant turn, so the actor finishes the turn believing it did something it did not do, and every later step reasons from a false premise ([§10](#10-what-is-measured-about-hooks)). |
| **Never deny** | Let the call execute and take its result; see [§4](#4-why-judging-after-the-fact-and-not-before). |
| **Inject as an identifiable external hint** | The intervention has to be honest *conditioning* — text the actor visibly received from outside, not a fabricated observation and not something it produced itself. What makes it honest is that the actor can **tell it apart**: an explicit marker (`<oracle_hint>` …) saying so. The wire-level `role` field is **not** the criterion (owner, 2026-09-01) — a tagged segment appended to a tool result qualifies, and so would a real user turn. [§11](#11-open-questions) states the three tests a channel has to pass. |
| **Direction only, never specifics** | The leakage / teaching dial; see [§8](#8-what-hint-specificity-now-trades). |
| **Not a system-reminder** | Claude Code already uses that channel heavily, so ours would be indistinguishable from machine noise — both to the actor at run time and to anyone reading the trace. |

## 6. The trace is the conversation, unedited

**The training trace is the phase-C conversation itself, with nothing removed
and nothing added.** Not the conversation minus the hints; not a reconstruction
assembled offline. Each hint is already a visible part of the conversation the
actor had — a tagged segment of a tool result
([§11](#11-open-questions)) — so [Phase D](#phase-d--collection) is a pure
conversion.

The reason is a property of the training objective, not a convenience:

- SFT takes loss on **assistant tokens only**. A user turn is *conditioning*,
  never a target.
- So the model is never trained to **produce** a hint — only to **respond well**
  to one. There is no mechanism by which keeping the hint teaches the model to
  assert something it cannot know.

And the sharper half: **deleting the hint is what would create the leak.** Strip
the user turn and what remains is an assistant that pivots hard for no visible
reason — which is precisely the pathology in the
[Objective](#objective): a confident, unmotivated jump. Keeping the hint means
every assistant token is justified by its own visible context, by construction.

## 7. What that simplifies

Keeping the hints removes most of the machinery an earlier framing needed:

| Was going to be needed | Status under this design |
|---|---|
| A trace-surgery pass removing the intervention turns | **Gone** — there is nothing to remove, and ([task 02](plans/README.md)) nothing to insert either: the hint arrives inside the tool result and converts as it stands |
| An inducibility invariant enforced by a checker | **Satisfied by construction** — nothing in the trace is unexplained |
| A blind judge used as a **leak detector** | **Not needed for that job** — it may still earn its keep as a plain quality filter |
| Synthesized "thinking" spliced in front of corrected actions | **Gone** — the model writes its own reasoning in response to the hint, and its `thinking` blocks survive capture ([§10](#10-what-is-measured-about-hooks)) |

This is the largest simplification the design has had, and it is what makes the
build tractable.

## 8. What hint specificity now trades

Not leakage any more — **how much the trace teaches**.

- **Too specific** ("the bug is in `config.py`") and the assistant is merely
  executing an instruction. The trace carries little skill, because the hard
  part was done by the hint.
- **Directional** and the assistant does the real derivation from evidence it
  actually holds. That derivation is the part worth learning.

So specificity trades steering power against teaching value, and it is the main
thing to tune. Prompt-level detail is deliberately deferred to the experiments.

## 9. Data mixture — to be validated, not solved

A diet of traces in which a helpful user always intervenes at exactly the right
moment plausibly teaches **dependence**: an agent that under-performs when
nobody is steering. This is a **data-mixture question and an open one** — the
obvious levers are mixing hinted with unhinted traces and keeping hints sparse,
but neither the mixture nor the sparsity has been measured, and nothing here
should be read as saying the problem is handled.

Worth recording alongside it: this trace shape corresponds to a **real usage
mode**. A person course-correcting a coding agent mid-task is one of the ways
these agents are actually used, so the data is plausibly in-distribution rather
than an artifact of the collection method.

## 10. What is measured about hooks

Claude Code `2.1.252`, measured on this machine 2026-08-31 / 2026-09-01 under
`claude -p --output-format stream-json --include-hook-events` with an isolated
`CLAUDE_CONFIG_DIR`. Documentation source:
[Hooks reference](https://code.claude.com/docs/en/hooks) (retrieved
2026-08-31/09-01). Where documentation and measurement disagreed, the
measurement is what is recorded.

| Fact | Status | Consequence here |
|---|---|---|
| A hook can fire on **every** tool call (`"matcher": ""`) | measured | Phase C is expressible |
| Hooks can be injected per-run with `--settings <file-or-json>` plus an isolated `CLAUDE_CONFIG_DIR`; the user's own `~/.claude` hooks did not fire | measured | Fits our sandbox wiring. `--restricted` also isolates but **forbids** `--dangerously-skip-permissions` |
| The actor's `thinking` blocks are present in full (text + signature) in the `stream-json` stream | measured | The trace carries the model's own reasoning; nothing needs synthesizing. This is the representation that matters: `convert.py` reads the `stream-json` stream (or the proxy log), **never** the persisted transcript, so no claim about the transcript's `thinking` representation is load-bearing here and none is made |
| `PreToolUse` **can** replace `tool_input` via `hookSpecificOutput.updatedInput` — whole-object replace, not merge | measured | Available, and deliberately unused ([§5](#5-the-mechanism-decisions)) |
| After a rewrite, the assistant turn still shows the model's **original** `tool_use` while the result is the **rewritten** call's; nothing tells the actor | measured (`Edit`: model wrote `MODEL_WROTE`, hook wrote `HOOK_WROTE`, file on disk `HOOK_WROTE`, result said only "updated successfully") | The reason rewriting is banned: it desynchronizes the actor's own world model mid-run |
| `PostToolUse` observes the **rewritten** input, `PreToolUse` the original | measured | If we ever needed to log a triple, this asymmetry is where |
| A `PreToolUse` denial lands as a `tool_result` with `is_error: true`, content = `permissionDecisionReason` | measured | The shape we are avoiding |
| `PostToolUse` `decision: "block"` + `reason` lands in the transcript as a line of `type: "attachment"`, `attachment.type: "hook_blocking_error"` — **not** a message, and not a user turn | measured | **This is the head open question** ([§11](#11-open-questions)). In that run the model's own `thinking` named it "a post-tool hook message", weighed it against the user's actual instruction and declined to follow it. So the channel appears to affect **compliance**, not only trace shape — which strengthens rather than weakens the case for finding a channel the actor reads as an external instruction rather than as machine noise. The task was trivial and already complete, so this is *weak* evidence about persuasion and strong evidence about shape |
| `additionalContext` is delivered wrapped in a system reminder | measured ([task 02](../../experiments/trace_synthesis/injection_shape/REPORT.md)) | The wire shows it inside the tool result as `<system-reminder>\nPostToolUse:Bash hook additional context: …\n</system-reminder>`. Still ruled out by [§5](#5-the-mechanism-decisions), and now also because `event_stream_to_conversation` drops it |
| `PostToolUse` `updatedToolOutput` reaches the actor **inside the `user` / `tool_result` block, as the tool's own output bytes**, with no wrapper | measured | The recommended channel: a tagged suffix appended there survives **both** converters ([§11](#11-open-questions)) |
| `updatedToolOutput` is validated against the tool's declared output schema; a mismatch is discarded with *"…does not match `<tool>`'s output shape; using original output"* | measured | The hook must copy the tool's response object and append into its text field — `stdout` for `Bash`, `file.content` for `Read` |
| `PostToolBatch` and `PostToolUseFailure` accept **only** `additionalContext` — no `decision`, no `updatedToolOutput` | measured (the shipped binary's own schema) | Neither seam can carry a hint that survives a stream capture |
| `PostToolUse` does not fire on a failed tool call; `PostToolUseFailure` does | measured | A `PostToolUse`-only design is blind exactly when the actor is spinning after an error |
| Two reminders reach the actor that are in **no** client request body: a token-usage `<system_warning>` and a `PROMPT INJECTION WARNING` naming *"impersonating a user message"* as the pattern | measured (quoted verbatim by the actor; absent from every proxy-captured request) | A proxy capture is ground truth for what the **client sent**, not for what the model saw. Our hint provokes a warning the actor then reasons about in `thinking` while it appears in no visible turn |
| `proxy_log_to_conversation` keeps only the **last** proxy record's thread | measured (a subagent run: stream conversion kept 3 hints over 18 turns, proxy conversion emitted 7 turns and 0 hints) | A defect in the proxy capture path, and a live way to lose a hint silently — part of why the [conversion guard](plans/README.md) stays required |
| A built-in `type: "prompt"` / `"agent"` hook can only allow or deny | documented | The Supervisor must be our own `command` / `http` handler calling the API |
| Spawning `claude` inside a hook is blocked by the `CLAUDECODE=1` nesting guard, and there are recorded recursive cost-explosion incidents on `Stop` / `SessionEnd` | documented + measured (`CLAUDECODE=1` present in the hook environment) | Never nest the CLI; call the API |
| The hook subprocess does **not** inherit `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` | measured | The Supervisor needs its own credential passed in explicitly |
| `transcript_path` is on stdin but is written asynchronously and may lag; under `--no-session-persistence` the path is reported and the file does not exist | measured | Never treat it as an authoritative real-time prefix |
| `command` / `http` hook timeouts default to **600 s** and **fail open** — a timed-out hook's output is discarded and the run continues | documented | A silent data-corruption source; see [§11](#11-open-questions) |
| Matched hooks run **in parallel**, so a batch of parallel tool calls produces N concurrent hook invocations | documented | Argues for one decision per batch; see [§11](#11-open-questions) |

**Harness scope.** Hooks are a Claude Code mechanism. `codex` and `grok_build`
have no equivalent, so this design is `claude_code`-only — even though
`grok_build`'s headless output happens to share Claude Code's `stream-json`
schema ([task 29](../horizontal/plans/task-29-grok-harness.md)).

## 11. Open questions

**Head question — what shape can the hint actually take?** Not "can a hook
produce a genuine `user`-role turn?": the owner ruled on 2026-09-01 that the
wire-level role is **not** the criterion. A channel qualifies when three things
hold, and those three are the whole question:

1. **The actor sees it** — it is in the context of the next model request.
2. **It is marked as an external injection** — an explicit tag
   (`<oracle_hint>` …), so the actor can mistake it neither for its own output
   nor for the tool's. The tag carries the provenance the role field was being
   asked to carry.
3. **Our typed `Conversation` conversion preserves it** — the second half,
   below.

**[Task 02](plans/README.md) measured this, and the answer is
`updatedToolOutput` carrying a tagged suffix appended to the tool's real
output** ([report](../../experiments/trace_synthesis/injection_shape/REPORT.md)).
It is the only candidate that passes all three criteria under **both** of this
repo's converters: the wire shows it inside the `user` / `tool_result` block as
the tool's own bytes, the actor reads it as injected rather than as the
command's output, and it survives `event_stream_to_conversation` and
`proxy_log_to_conversation` alike. `additionalContext` on `PostToolUse` /
`PostToolBatch` / `PostToolUseFailure` fails (3) on the default capture — it is
delivered wrapped in a system reminder that `stream-json` does not carry — and
`decision: "block"` fails it as an `attachment`
([§10](#10-what-is-measured-about-hooks)). **This is a recommendation from a
measurement, not yet a [§5](#5-the-mechanism-decisions) decision**; it needs the
owner's sign-off before it becomes one.

The measurement also says something about the marker, and it is narrower than
it first looked. An **unmarked** hint is refused as a prompt injection far more
often than a tagged one; and of the four combinations of tag name and hint body
that were tried, **only a neutral tag with a body that does not claim to be the
user drew no objections at all** — the other three drew objections in 2 to 4 of
4 runs. So the marker is a neutral, acknowledged third party (`<oracle_hint>`)
whose text does not impersonate the operator. *Why* that combination wins is
**not** settled: the 2×2 splitting the tag from the body is non-monotone, so
"never impersonate the user" is the provisional reading rather than a
demonstrated mechanism.

**Appending a tagged suffix is not rewriting the tool output**, and the
distinction is exactly where [§5](#5-the-mechanism-decisions)'s *never rewrite*
draws its line. Replacing a tool's output wholesale is the same disease as
`updatedInput`: the actor's world model comes apart from what actually
happened. Keeping the tool's real output verbatim and appending a tagged
segment after it does not — everything the tool said is still there, and the
tag says who said the rest.

The second half turned out to be **a property of the converter, not of the
channel**, which is why the recommendation is worth what it is.
`event_stream_to_conversation` skips every stream event whose `type` is not
`user` or `assistant`, so a hint delivered as the hook's **own** output (a
`system` / `hook_response` event, and only under `--include-hook-events` at
all) converts to nothing — and so does `additionalContext`, whose system
reminder the stream does not carry. `proxy_log_to_conversation` keeps both,
because on the wire they sit inside the tool result. Choosing
`updatedToolOutput` is what makes the answer stop depending on which capture a
run used.

**Materialization is therefore not needed**, and the fallback in earlier
drafts of this section does not fire.

**The hint log and the conversion guard are not cancelled by that**, because
losing a hint silently is still reachable. Two measured ways:
`proxy_log_to_conversation` keeps only the last proxy record's thread, so a
hint delivered inside a subagent's conversation vanishes from a proxy capture
([§10](#10-what-is-measured-about-hooks)); and a hint injected after the
actor's last API call never reaches the model at all, which is honest but must
still be *recorded* rather than assumed.

**The one fatal failure mode is a hint disappearing silently.** That produces
precisely the unmotivated-pivot trace this whole design exists to avoid, and it
would do so while looking fine. So, as a hard constraint on whatever the
resolution turns out to be: **a hint lost in conversion must be detectable, and
conversion must fail rather than silently emit a hint-less trace.**

The rest, in no particular order:

- **Which events do we hook? — answered: both.** `PostToolUse` fires only
  after a tool *succeeds* and `PostToolUseFailure` on a failure, both measured
  ([§10](#10-what-is-measured-about-hooks)), so a `PostToolUse`-only design is
  blind exactly when the actor is spinning after an error. What is *not*
  settled is a permission or schema failure, which triggers neither.
- **One hint per batch, or one per call?** Parallel tool calls fan out to one
  hook each plus a single `PostToolBatch` (measured: three calls → three
  `PostToolUse` invocations and one batch invocation). `PostToolBatch` remains
  the natural seam for one decision per batch, but it carries **only**
  `additionalContext`, which the default capture drops — so a batch-level hint
  costs the proxy capture and its
  [thread-loss defect](#10-what-is-measured-about-hooks).
- **Why does routing the actor through a proxy change whether it follows the
  hint?** Measured and unexplained: with the identical channel, hint, model and
  prompt, the actor followed the hint in 4 of 4 proxied runs and 0 of 10
  unproxied ones (5 of which set `ANTHROPIC_BASE_URL` to the real API, ruling
  out the variable itself). Until it is chased down, proxied compliance numbers
  are optimistic and the **unproxied** ones — the default capture — are what
  the design should plan against.
- **Does the harness's prompt-injection guard harden over a long rollout?**
  Every measurement so far is a 1–3 call toy task. The guard tells the actor to
  check whether an instruction "actually arrived as a user turn"; ours never
  does. Whether it starts refusing over a 50–200 call rollout is the risk that
  would kill the design, and only [task 01](plans/README.md) can show it.
- **What happens when the Supervisor times out?** The default is fail-open —
  the hint is silently dropped and the run continues. Silently is the one
  behaviour that quietly changes the dataset, so the policy must be explicit
  and the occurrence must be recorded per attempt.
- **Selection.** Measure the `pass@10` band (ten rollouts per instance) or use
  the cheap "failed once, gold self-test passes" proxy?
- **Guidebook reuse.** One guidebook per instance, reused across attempts, or
  regenerated per attempt from the newest failure?
- **Triage.** Can a cheap model answer "on track?" and escalate to an expensive
  one only on suspicion (a repeated file, an error loop, no progress)?
- **Termination.** What ends a guided rollout that is still failing — a hint
  budget, a fallback to a more directive Supervisor, or discard?
- **Quality filtering.** A blind judge is no longer needed as a leak detector
  ([§7](#7-what-that-simplifies)); is it worth building as a plain trace-quality
  score?
- **Cost.** One Supervisor call per tool call, on a task that runs 50–200 of
  them, on top of a baseline rollout, an oracle analysis and a guided rollout
  that can still fail and cost all of that for nothing. Whether the yield
  justifies it is the question [the batch measurement](plans/README.md) exists
  to answer.

## 12. Invariants (intended; none enforced today)

Per `AGENTS.md`, an *always / never* claim needs a named test or it must be
softened. **None of the rows below are enforced today**; each names the test
that must land in the same change as the code it constrains. Absolute-sounding
claims elsewhere in this spec that are *not* in this table have been softened
where they cannot be tested — the guidebook's tone
([Phase B](#phase-b--the-oracle)) and the honesty of a trace's reasoning
([Objective](#objective)) are read by a human, not asserted by a checker.

The banned-channel row covers exactly the three of
[§5](#5-the-mechanism-decisions)'s decisions that surface as fields in a hook
response, and claims nothing beyond them. The other three are not
settings-level facts and are not pinned here: *steer from a hook rather than
the proxy or our own loop* is an architectural choice visible in what the
component builds at all, *inject as an identifiable external hint* is the
positive form of the same three bans, and *direction only, never specifics* is a property of
generated prose — the [hint-specificity dial](#8-what-hint-specificity-now-trades)
is tuned by reading traces, not asserted by a test.

| Intended invariant | Test that must pin it |
|---|---|
| The guidebook never enters the actor's context or the training trace | a test asserting the guidebook path is absent from phase C's mounts and from the serialized `Conversation` |
| The belief state is host-side only — never written into the sandbox | a test asserting no phase-C mount or write target resolves to the belief-state file |
| Every phase B / C record carries the oracle-guided policy stamp | a test asserting the stamp is present on the record and that aggregation across differing stamps still errors |
| A dropped or timed-out Supervisor decision is recorded, never silently ignored | a test asserting the run record shows the drop |
| A hint lost in conversion is detectable — conversion fails rather than emitting a hint-less trace | a test feeding a run whose hint the converter cannot represent and asserting conversion errors |
| Conversion neither drops nor synthesizes turns: the training trace is exactly the actor's turns plus the interventions the actor received | a test comparing the converted `Conversation` against the capture and the hint log, asserting equality of the turn sequence — no extra turn, no missing one |
| A hint never replaces a tool's output: the tool's own output is a substring of what the actor is shown | a test over the Supervisor's hook-response builder asserting the tool response it returns contains the original response's text verbatim |
| No banned channel is reachable in a hook response: the Supervisor's output never carries `updatedInput` (a rewrite), a deny decision, or `additionalContext` (the system-reminder channel) | a test over the Supervisor's hook-response builder asserting all three fields are absent from every response it can produce |

## 13. Where this plugs into swe-lab

The workflow layer ([ADR-0007](../decisions/ADR-0007-task-and-workflow-layer.md))
already has the right shape — statically declared entries with edges resolved
from the store — so this is a workflow, not a new subsystem.

| Phase | Reuses | New |
|---|---|---|
| A | `rollout_and_unit_test`, unchanged | — |
| B | the `Task` layer | a task that mounts the golden patch and tests, with the git-history purge **off**; declared output `guidebook.md` |
| C | the rollout composition | hook settings injected into the sandbox (`--settings` + `CLAUDE_CONFIG_DIR`); a host-side Supervisor; declared intervention records |
| D | the `Conversation` converter + `Store` | — |
| all | `register_workflow(...)` | the A→B→C→D edges |

## 14. Integrity red lines

**Phases B and C are deliberately contaminated.** B mounts the golden patch and
tests; the git-history purge
([task 25](../horizontal/plans/task-25-git-history-purge.md),
[ADR-0010](../decisions/ADR-0010-benchmark-integrity.md) §3b) must be **off**
for it. Two consequences, and neither is negotiable:

1. **These runs are never pooled with benchmark numbers.** ADR-0010 §5's
   **policy stamp** is the mechanism: the record carries the policy that
   produced it, and aggregation across differing stamps is already defined as
   an error rather than a warning. Oracle-guided runs carry a stamp that says
   so.
2. **The result verifier ([task 26](../horizontal/plans/task-26-result-verifier.md))
   will flag these runs as contaminated, and that is correct behaviour.** We
   declare the contamination; we do not suppress the detector. A change that
   makes the verifier quiet about oracle-guided runs is a bug in this design,
   not a fix.

## 15. Success criteria

1. A hint reaches the actor **visibly marked as an external injection** and is
   **preserved in the training trace** — or, if no mechanism can do that, an
   explicit owner decision on the alternative
   ([§11](#11-open-questions)).
2. On at least one instance, a supervisor holding a good guidebook steers a
   blind actor from a known failure to a passing verdict. Until this is shown,
   nothing else in the pipeline is worth building.
3. Sampled traces read as honest: each assistant turn is explicable from the
   turns before it, judged by a human reader.
4. Every oracle-guided record carries the policy stamp, and no aggregation
   pools it with benchmark runs.
5. A measured cost per kept trace, and a yield, good enough to argue the
   pipeline beats rejection sampling on the same instances.

## 16. Out of scope

- **Harnesses other than `claude_code`.** No hook equivalent exists for `codex`
  or `grok_build` ([§10](#10-what-is-measured-about-hooks)).
- **Rewriting the actor's tool calls or its assistant turns.** Banned by
  [§5](#5-the-mechanism-decisions); a proxy-based design that could rewrite the
  assistant turn is a different design, not a later phase of this one. The
  capture proxy now runs **inside** the sandbox
  ([task 10](plans/README.md#task-10-run-the-capture-proxy-inside-the-sandbox),
  [ADR-0012](../decisions/ADR-0012-in-sandbox-capture-proxy.md)) and this line
  is unchanged by that: it already modified *requests* (OpenRouter `provider`
  preferences, `X-Anthropic-Beta` mirroring) when it ran host-side, it still
  does not touch assistant turns, and moving it changed where it runs rather
  than what it may do.
- **Training itself.** This component produces traces; running SFT on them is
  somebody else's pipeline.
- **Deciding the data mixture.** [§9](#9-data-mixture--to-be-validated-not-solved)
  states the risk; measuring it is downstream of having traces at all.
