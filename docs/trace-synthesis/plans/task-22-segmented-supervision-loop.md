# Task 22: The segmented supervision loop

**Design record, drafted and ruled on 2026-09-03.** Written against the owner's
first-principles ruling of the same day (recorded in the Verdict of the
[feasibility report](https://github.com/Luolc/swe-lab/pull/412)) and the brief
that followed it. Status is [`README.md`](README.md), not here.

**The bring-up basis.** A second carrier for the same supervision stack: instead
of writing a correction onto a live stdin (ADR-0013, A′), stop the actor every
*N* turns, judge, and resume. The owner set criterion **(b)** aside for this
line of work and left exactly one hard requirement — *a synthetic assistant
record must never be trained on*. "Get it running end to end" is the acceptance
tone; the shape of the resulting trace is post-processing's problem.

> **The requirement changed shape before any code was written**, and §6 is the
> whole of why. The identifying fields the feasibility report measured live in
> the actor's **session transcript**; the trace we train on is the **event
> stream or the proxy log**, where they do not exist — and by the time
> `conversation.json` is written, a canonical `Message` keeps only `role` and
> `content`. Worse, those fields are the candidate record's **own self-report**,
> so no detector built on them establishes anything. The answer is a
> **provenance gate** (§6.4): annotate before provenance is discarded, fail
> rather than pass when it cannot be established, prove authorship against the
> **captured API responses**, and have the driver record every seam it cut —
> which it can do exactly, because it cut them. **What that gates is the
> trace's use as training data, not this task's bring-up.** Ruled by the
> orchestra on 2026-09-03, on findings from two independent directions.

---

## 1. What the mechanism is, in one loop

```
segment k:  claude -p … --max-turns N  [--resume <session_id>]  < <segment prompt>
            └ stdout appended to the one event stream

read the segment's terminal `result` event
  subtype == "success"          → the actor thinks it is done → stop
  subtype == "error_max_turns"  → a seam:
        record the seam (§6)
        build an Observation from the actor's records so far
        policy.consider(observation)
          Intervention  → segment k+1's prompt = intervention.rendered()
          None/Unjudged → segment k+1's prompt = the neutral continue
  anything else                 → stop, and record which ending it was

caps, all three, checked before launching segment k+1:
  segments run    >= max_segments        → stop
  wall clock      >= wall_clock_seconds  → stop
  cumulative cost >= max_cost_usd        → stop
```

`N` is `SegmentedSupervision.turns_per_segment`, **default 5**, never a literal
in the loop body.

### Why the cost cap is read on the host and not passed to the actor

`--max-budget-usd` writes a running readout **into the actor's context**
(`attachment budget_usd {'used': …, 'remaining': …}`), so the actor can see it
is on a budget — the feasibility report's *Amendment 1*, where it was removed
from every behavioural arm on exactly this ground: **a guard the actor can see
is a treatment, not a guard.** The loop therefore sums `total_cost_usd` off each
segment's own terminal `result` event and stops itself. `max_budget_usd` stays
available on the harness and this path does not set it.

---

## 2. What is reused, and at which interface

Almost all of it. The new code is a driver and a seam record.

| Reused | The interface it is reused on | Untouched? |
|---|---|---|
| `SpeakPolicy.consider(Observation) -> Intervention \| Unjudged \| None` | the loop calls it once per seam, exactly as `Supervisor.observe` does per event | yes |
| `SpeakWhenOffTrack`, `SpeakAt`, `NeverSpeak` | as-is; budget / cooldown / window are per-*decision*, and a seam is a decision | yes |
| `ModelJudge` / `ModelWriter` / `Judge` / `Writer` | as-is, plus one **opt-in** flag — see §5 | default path byte-identical |
| `Criterion` + `CRITERION_SHA256` pin | `supervising_policy(...)` builds the policy before the sandbox exists | yes |
| `EvidenceFilter` / `evidence_of(events)` | turns the appended event stream into the `Observation.evidence` window | yes |
| `Intervention.rendered()` + `INTERVENTION_TAG` | the seam prompt is the same tagged text A′ writes to stdin | yes |
| `Observation`, `Unjudged`, `PolicyLapseError`, the five `LOG_KIND_*` rows | the loop writes the same `supervisor.jsonl` vocabulary, one row per **seam** rather than per event | yes |
| `ClaudeCodeHarness.actor_argv()` | the one construction of the run's flags; the loop adds `--resume <sid>` through it, not beside it | extended |
| `_invocation_script` / `mounts` / `observers` / `native_outputs` / `to_conversation` / `outcome` / `usage` | unchanged code paths; see §4 for the two one-character edits | extended |
| `NativeTranscriptObserver` | already archives the actor's own session record on every run — the second leg of §6.4's join | yes |

**What is *not* reused, and why.** `channel.py` — the FIFO, the relay, the
`SupervisorPump`, `SupervisedRun`. Not because it is wrong but because it cannot
be the seam here: `SupervisedRun` is an observer that brackets **one blocked
`run()`**, and this loop has to *drive* `run()` several times. `channel.py` and
every caller of it are untouched by this task.

---

## 3. Where the loop lives: a field on the harness, not a new harness

`ClaudeCodeHarness` already carries two mutually-exclusive supervision
mechanisms as fields (`correction_channel: bool`, `native_supervision:
NativeSupervision | None`), with `__post_init__` refusing the combination in
which two components own the actor's stdin. This is the third, and it follows
the same shape for the same reason stated at `correction_channel`: a forked
harness is a standing invitation for the supervised and unsupervised paths to
drift in flags, denied tools or capture wiring — **drift that would be invisible
in the traces it produces**.

```python
@dataclasses.dataclass(frozen=True)
class SegmentedSupervision:
  policy: SpeakPolicy
  max_segments: int          # no default: a loop must state its ceiling
  wall_clock_seconds: float  # no default
  max_cost_usd: float        # no default
  turns_per_segment: int = 5
  neutral_continue: str = "Continue."
```

- New harness field `segmented: SegmentedSupervision | None = None`.
- `__post_init__` refuses `segmented` together with `correction_channel` or
  `native_supervision` — three owners of one actor is not a configuration.
- `actor_argv()` gains a keyword-only `resume_session_id: str | None = None`
  and uses `segmented.turns_per_segment` for `--max-turns` when segmented.
- **`max_turns` changes meaning under segmentation** — today it is the whole
  run's runaway guard at 500, and under segmentation it is one segment's length.
  The runaway guard moves to `max_segments × turns_per_segment`, which is why
  `max_segments` has no default. **This is written into `max_turns`'s own
  docstring**, not only here: a parameter that silently changes meaning and a
  comment that has gone stale are the same failure.

---

## 4. The two edits inside the invocation script, and the measurement each rests on

The script is re-staged per segment (`sb.write(AGENT_SCRIPT_NAME, …,
executable=True)` — `SandboxFs.write` is documented for exactly this), so each
segment gets its own argv while the argv is still built in one place.

1. **The event stream is appended, not truncated** (`>` → `>>` under
   segmentation), and the harness truncates it once before segment 1. Safe for
   every existing reader, and that is measured rather than assumed:
   `event_stream_outcome` already scans **backwards for the last `result`
   event**, and `event_stream_usage`'s docstring already states the segmented
   aggregation rule (`total_cost_usd` cumulative → take the last; `num_turns`
   per-result → sum). The multi-segment stream is the shape those two were
   written for.
2. **The capture proxy survives a per-segment restart.** Each segment's script
   starts and reaps its own proxy instance, which would lose earlier segments if
   the log were truncated. It is not: `cc-reverse-proxy` opens its `--output`
   with `os.OpenFile(outputFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)`
   (`reverse_proxy.go:266`), so a restart appends. Regenerate with
   `grep -n "O_APPEND" ~/dev/cc-reverse-proxy/reverse_proxy.go`.

**So the segmented arm runs on `capture="proxy"`, like the two shipped A′ arms
and for one more reason than they have.** Their reason is that the wire is the
only record of the request bodies a run produced; ours adds that §6.4's
condition 3 needs the **captured API responses** as its independent oracle, and
the proxy log is where they are. `_narrates_event_stream` gains
`segmented is not None` — exactly as it already carries `correction_channel` and
`native_supervision` — so the actor still narrates the event stream the loop
reads its `result` events from. Both artifacts exist, as they do on the A′ arms.

### The flag composition this rests on, measured free

The feasibility report leaves open whether `--resume` composes with
`--input-format stream-json` — every anchored arm delivered its prompt as a
positional `-p "Continue."`, and every stream-json arm ran without resume
(report §13). The harness's stdin is stream-json on every path that keeps
`--replay-user-messages`, and dropping that flag would drop the injected user
messages out of the trace — so the composition is load-bearing here.

Probed at **zero API cost** by resuming a session id that does not exist, on the
host CLI (2.1.259), three arms:

| arm | command tail | output |
|---|---|---|
| **A** (the untested combination) | `--resume <nonexistent> --input-format stream-json --output-format stream-json --verbose --replay-user-messages` | `No conversation found with session ID: …`, then a `result` event with `num_turns: 0`, `total_cost_usd: 0` |
| **B** (control) | `--resume <nonexistent> --output-format json` | the **same** session error |
| **C** (control) | `--resume <nonexistent> --input-format not-a-format` | a **different**, parse-time error: `argument 'not-a-format' is invalid. Allowed choices are text, stream-json` |

**A and B agree, so nothing about stream-json broke resume; C differs, so the
validator that would have rejected the combination demonstrably fires.** A
therefore reached session lookup, which is past the point where an incompatible
pair is refused.

**Scope, stated rather than left to the reader.** This is the **host** binary
2.1.259; the sandbox pins **2.1.212** (`binary.py:45`). ADR-0013's refutation
condition — host measurements may not describe the pinned build inside the
sandbox — applies to it exactly as it applies to every number in the feasibility
report, and this is the family where *the same check gives different answers in
two environments*. The bring-up run is what closes it, and it fails fast and
cheap if the pinned build disagrees.

---

## 5. Requirement C: the two quantities that only exist while running

The owner named two, and their difference is the empirical distribution of
"how many turns late was the correction" — **the only evidence a future choice
of `N` can rest on.**

1. **Which turn an injection landed on.** Free: the loop already knows the
   cumulative turn count at each seam. A turn is one distinct assistant
   `message.id`, corroborated here on a **real** rollout rather than only on the
   report's toy task — the first end-to-end capture has 59 assistant *events*,
   **32 distinct assistant `message.id`**, and its `result` event reads
   `num_turns: 32`. (Counting events would have given 59; that is the report's
   own counting trap, met again on production data.) Regenerate over
   `~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0/claude_code.event_stream.jsonl`.
2. **Which turn the judge believes the deviation started at.** **Not obtainable
   from any interface we have** — `Verdict` carries `off_track`,
   `self_correcting`, `reason`, and `WouldHaveSpoken` records where a deviation
   was *found*, never where it began. So this one costs a change to shared code,
   scoped so the A′ path cannot move:

   - `Verdict` gains `deviation_started_at_turn: int | None = None`, read from
     the answer with `.get` — the same tolerance `reason` already has, so an
     answer without it is not an error.
   - `ModelJudge` gains `locate_deviation: bool = False`. Only when it is
     **True** does the prompt number the evidence records by their global turn
     index and ask for the extra field.
   - **Control arm, and it is the whole condition on which this is allowed:** a
     named test asserts the prompt built with the default is byte-identical to
     today's, so "A′ is unchanged" is a check rather than a claim. The positive
     arm asserts the numbering and the field appear when it is on.

Both land in the loop's seam row (§6.3), beside the existing `cursor` /
`policy` / `kind` fields.

### The cost of N, written down because it will not announce itself

At `N=5`, an actor that goes wrong on turn 1 is not corrected until turn 5:
**turns 2–4 are a "wrong and uncorrected" trajectory, and training on them
teaches the model to keep making that mistake.** This is **not** covered by §6 —
that is about tokens no model wrote, and these are tokens the model really did
write, in a state we chose not to interrupt. It is a separate, deliberate
post-processing action that **nobody will take unless it is written down**,
which is why it is here, in the loop's module docstring, and in the spec
reconciliation of §7.

---

## 6. The one hard requirement: a provenance gate, and what it gates

### 6.1 The finding that changed the design, before any code

The brief specified a filter keyed on the fields the feasibility report
measured: `message.model == "<synthetic>"`, the absent `requestId` /
`apiBlockIndex` / `effort`, the extra `isApiErrorMessage`. **Those are session
transcript fields, and the trace we train on is not the session transcript.**

- `spec.md:476`, measured and already in the spec: *"`convert.py` reads the
  `stream-json` stream (or the proxy log), **never** the persisted transcript"*.
- Measured here on the first end-to-end capture: **0 of 59 assistant events
  carry `requestId`**, and all 59 report `message.model: "claude-sonnet-5"`.
  The chain applied to the event stream would answer about **every** assistant
  record identically.
- On the **wire** the synthetic record is worse still: report §6.2 measured it
  arriving as an ordinary `assistant` message carrying the text
  `No response requested.` — no marker of any kind.

**Reached independently from the other direction, in PR #412's third review
round, and that reproduction is the stronger one**: the product conversion
(`ClaudeCodeHarness.to_conversation()` / `proxy_log_to_conversation()`) yields a
canonical `Message` carrying **only `role` and `content`**, which
`ConversationObserver` persists as `conversation.json`. Run a real dirty-seam
capture through it and the synthetic assistant sits at index 7 of 10 canonical
messages, and the briefed filter returns the list unchanged. **So "every unit
test is green" and "the fabricated turn is in the training data" hold at the
same time.** Two paths, one conclusion; the fact is settled and is not re-tested
here. That branch's report links to this section rather than restating it.

### 6.2 Provenance cannot be supplied by the record being authenticated

The other half, which neither the brief nor my first pass had: **all three
identifying fields are the candidate record's own self-report.** A record
carrying `model: "claude-sonnet-5"` and any non-empty `requestId` passes the
positive chain while having been fabricated. Matching the literal
`No response requested.` is no better — it gives the same answer for "our seam
made this" and "the model wrote that sentence".

So authorship has to come from **an independent source**, and one exists: the
captured API responses. Reconciling a retained assistant turn against the
response that produced it is a check the record cannot pass by asserting things
about itself.

### 6.3 What is gated — the trace's use as training data, not the bring-up

Stated in these words deliberately, because it now lives across two PRs and two
different phrasings of one gate would be the next "same fact, two copies":

- **Phase 1 end-to-end bring-up is not blocked.** The owner said get it running
  first.
- **The traces it produces may not be delivered downstream as training data
  until the provenance gate below is built.**

The basis: **producing a trace is not training on it**; `spec.md` puts training
itself out of scope; and the owner's hard requirement binds at the **consumer**.

### 6.4 The gate's acceptance conditions — and this section is their only home

Adopted from PR #412's Phase 1 conditions and combined with the seam record the
orchestra ruled for. **The ADR of §7 points here; nothing copies this list.**

1. **Filter or annotate at the trace → `conversation.json` boundary, before
   provenance is discarded.** That boundary is exactly where the fields go: the
   canonical `Message` keeps `role` and `content` and nothing else.
2. **Fail the run when provenance cannot be established**, rather than passing
   it through. The `required=False`, never-fails-a-run transcript observer is
   the shape to avoid here — right for evidence collection, wrong for a gate.
3. **Establish authorship from an independent source**: reconcile each retained
   assistant turn against the **captured API response** that produced it. Never
   the candidate record's own fields (§6.2).
4. **One end-to-end test from a real dirty-seam fixture** that produces the
   final canonical conversation and asserts **both** arms: the fabricated record
   is *absent* **and** the real responses are *still there*. A gate that drops
   everything satisfies the first arm alone.
5. **The driver records every seam it cut** (§6.5). Complementary to 3, not a
   substitute: the driver's record says **where the seams are**, from the
   production side; the API reconciliation says **which assistant turns are
   real**, from an independent oracle. Two sources crossing is stronger than
   either.

**The structurally stronger repair is named rather than buried.** The
feasibility report measured that `--resume-session-at` produces **0** synthetic
assistant records — *not producing* one beats *producing and labelling* one.
Bring-up follows the owner's "get it running first" and uses plain `--resume`;
the ADR records that the requirement is met today by 3 + 5, and that moving to
`--resume-session-at` would make the artifact not exist at all. Evaluated after
bring-up, not before.

### 6.5 What the driver records at each seam

One row per seam in the loop's log, alongside the `LOG_KIND_*` row the policy
decision already produces:

| field | why it is only knowable now |
|---|---|
| `segment` | which segment ended here |
| `cut_at_turn` | cumulative distinct assistant `message.id` count at the cut — requirement C.1 |
| `stop_subtype` | the segment's terminal `result` subtype (§8's known limitation reads this) |
| `session_id` | what the next segment resumed |
| `next_prompt` | `"intervention"` or `"neutral-continue"` |
| `deviation_started_at_turn` | the judge's answer — requirement C.2, `None` when not asked or not given |
| `resume_artifact_expected` | **True on every resumed segment.** A claim about what *we* did, not a detection of what appeared |
| `anchor_event_index` / `anchor_result_uuid` | where the seam sits in the appended event stream: the index of the segment's terminal `result` event, and that event's own `uuid` (present on every `result` — verified on the real capture) |

The anchors are what let a consumer localize a seam in a corpus that carries no
marker.

### 6.6 The transcript leg, and exactly how far it reaches

`transcript_marks.py` is the cheap second leg of the join in condition 5 — the
CLI's own session persistence marks the records it fabricated, so its count can
be reconciled against the driver's seam count. **It is not condition 3 and
cannot become it**: `test_a_forged_record_passes_the_chain` asserts the
limitation rather than leaving a green suite to be misread as authentication.
Its name was narrowed from `model_authored` for that reason — a name wider than
its coverage is how a check gets trusted past what it checks.

### 6.7 The open reading the bring-up run closes

**Does the synthetic assistant record appear in the event stream at all?** It is
measured on the wire (report §6.2) and in the transcript (report §6.1);
`--replay-user-messages` echoes *user* messages, and this is not one. Both
answers are results:

- **absent** — a stream-capture corpus never contained it, and the seam record
  is belt-and-braces there;
- **present** — the corpus contains an unmarked fabricated record, and the seam
  anchors of §6.5 are what locates it.

Read on the bring-up run over all three artifacts, and recorded either way.

## 7. The ADR and the spec reconciliation, in this task's PR

**An ADR, because ADR-0013 decided *the* delivery channel and this adds a second
carrier** — a decision, not an implementation detail. It states three things:

1. the owner's 2026-09-03 relaxation of criterion **(b)**, its reasoning (this
   is SFT data generation and post-processing is rich, so a trace need not match
   the shape an interactive user produces), and who ruled it;
2. the one requirement that was **not** relaxed;
3. **how that requirement is met today** and what is gated on it — the
   acceptance conditions of [§6.4](#64-the-gates-acceptance-conditions--and-this-section-is-their-only-home),
   which the ADR **points at and does not restate**, because that section is
   their only home and PR #412's report links to the same place. It also
   records **why today's form is not the strongest**: `--resume-session-at`
   makes the artifact not exist, and is evaluated after bring-up.

**And `spec.md` §6 is reconciled in the same PR**, because it currently says the
synthetic assistant turn is *"the single artifact that disqualifies the
stop-and-resume path"* — the reverse of the sentence this task is built on.
Per `AGENTS.md` and `docs/evidence.md` rule 4, the fix is a **dated, attributed
addendum**, never an edit to the existing text.

---

## 8. Known limitation, carried here and pointed at from task 12

`event_stream_outcome` folds a run to its **last** `result`, and this loop is the
first thing that deliberately produces several. For the ending that fold is
correct — the last segment *is* how the run ended — but **a mid-run
`error_during_execution` segment is now invisible behind a later success.**

Handled by recording `stop_subtype` per seam (§6.3) and **not** by changing
`event_stream_outcome`, which is task 12's. Task 12's row gets a one-line
pointer here, so the fact does not fall between the two tasks.

---

## 9. Files touched

**New**

| Path | What |
|---|---|
| `src/swe_lab/trace_synthesis/segmented_loop.py` | `SegmentedSupervision`, the loop, the seam record of §6.3, the caps, and §5's cost note in its module docstring |
| `src/swe_lab/trace_synthesis/transcript_marks.py` | the transcript leg of §6.4's condition 5, narrowed to what it covers (§6.6) |
| `tests/test_segmented_loop.py` | the loop against a fake `SandboxFs` (`sandbox/testing.py` already scripts successive `run_script` results) |
| `tests/test_transcript_marks.py` | the two arms, plus the forged-record limitation asserted |
| `tests/data/assistant_record_shapes.json` | the committed shape fixture |
| `docs/decisions/ADR-00NN-…md` | §7 |
| this file + its row in `README.md` | |

**Extended**

| Path | Change |
|---|---|
| `harnesses/claude_code/harness.py` | the `segmented` field, the `__post_init__` refusal, `actor_argv(resume_session_id=…)`, the `>>` redirect, `run()`'s one branch, `max_turns`'s docstring |
| `trace_synthesis/supervisor.py` | `Verdict.deviation_started_at_turn` (optional, defaulted) |
| `trace_synthesis/judge.py` | `ModelJudge.locate_deviation` (default `False`, prompt byte-identical when off) |
| `workflow/definitions.py` | one `SEGMENTED_ROLLOUT` definition, `capture="proxy"` and `cooldown=0` |
| `docs/trace-synthesis/spec.md` | §7's addendum |
| `docs/trace-synthesis/plans/README.md` | task 12's pointer (§8) |

**Not touched:** `channel.py` and every caller; `rust/`;
`experiments/trace_synthesis/resume_loop_feasibility/`; any frozen
`PREREGISTRATION.md`.

---

## 10. Order of work

1. The transcript leg and its two arms (§6.6), including the control-arm
   falsification run. Depends on nothing else. **Done** — 7 tests; the mutant
   that reports every record fails exactly `test_a_real_assistant_record_is_not_reported`
   and `test_records_of_other_types_are_never_reported`, 5 passing.
2. `SegmentedSupervision` + `actor_argv` + `__post_init__`, unit-tested against
   the scripted fake sandbox — no container, no model.
3. The loop, its caps and the seam record, same level.
4. `Verdict` / `ModelJudge` opt-in (§5), with the byte-identity control test.
   **Shared code: the diff is reviewed before it lands.**
5. The ADR and the spec addendum (§7).
6. The workflow definition, and **one** bring-up run: **1 instance × 1
   rollout**, this task's ceiling and tighter than the repo's own ask-first
   line. It answers, in one go: does the pinned 2.1.212 build compose the flags;
   does the seam produce the records the report measured; does the synthetic
   record reach the event stream (§6.7); does the transcript leg agree
   with the driver's seam count (§6.4 condition 5); and what the §5 latency distribution
   looks like over one real task.

---

**Phase 2 — the provenance gate (§6.4), which the training delivery waits on
and the bring-up does not.** Conditions 1-4: annotate at the
trace → `conversation.json` boundary, fail the run when provenance cannot be
established, reconcile against the captured API responses, and one end-to-end
test from a real dirty-seam fixture asserting both arms. Until it is green, a
trace this loop produces is a run artifact and **not** training data.

## 11. Where the brief was wrong, and how each was ruled

Kept as a record because five of the nine changed the design, and a plan that
quietly absorbs its own corrections teaches nobody what to look for next time.

| # | What the brief said | Ruling (orchestra, 2026-09-03) |
|---|---|---|
| 1 | a positive-chain filter over the trace meets the hard requirement | **Wrong, and the most important finding here.** The identifying fields are transcript fields; the corpus is the event stream / proxy log (`spec.md:476`), where they do not exist and where the wire record has no marker at all. Independently reproduced from the other direction in #412 — the canonical `Message` keeps only `role` and `content`, and the briefed filter returns a real dirty-seam conversation unchanged. And the half neither of us had: those fields are the record's **own** self-report, so provenance cannot come from them at all. Replaced by the gate of §6.4 |
| 2 | "filter" | **Accepted as a wording fix, applied everywhere:** it is a *label* over an unedited trace, answering "may this record carry SFT loss?" — `spec.md` §6 forbids deleting a turn, and deleting this one leaves narration whose cause is gone |
| 3 | A′-specific is only `channel.py`, so replace that | **Brief conceded.** True of the delivery mechanism, but `SupervisedRun` brackets *one* blocked `run()` and this loop drives several. The seam is `run()` |
| 4 | requirement C is free | Costs a shared-code change; the narrowest form (§5) is accepted **on condition of the byte-identity control test**. A second judge call per seam was considered and rejected as more expensive and worse |
| 5 | "a cost cap, any cap" | **Brief conceded**: `--max-budget-usd` is a treatment, not a guard (report Amendment 1). Host-side accumulation instead |
| 6 | reuse `max_turns` | Accepted with `max_segments` mandatory, **and the meaning change written into the docstring** |
| 7 | no ADR mentioned | **Write one, in this PR**, with the three points of §7 |
| 8 | — | Known limitation recorded here (§8) with a pointer from task 12, so it does not fall between them |
| 9 | assert the seam shape on the wire | Not required on this path: bring-up accepts the dirty seam, so there is **no clean state to regress from**. The wire assertion returns with `--resume-session-at` |
