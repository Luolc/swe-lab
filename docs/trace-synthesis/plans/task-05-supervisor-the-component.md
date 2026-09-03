# Task 05 — The supervisor: what it may see, when it speaks, what it may say

**Status lives in [`plans/README.md`](README.md), not here.**

**This plan replaces a hook-era one.** The old task 05 was *"Supervisor + hook
wiring in the sandbox"*: hook settings injected per run, a host-side Supervisor
called from a `PostToolUse` handler, and a hook-response builder that appended
into `updatedToolOutput`. **That delivery mechanism is gone.** The correction is
delivered on the stdin of a live `claude -p --input-format stream-json` process
([ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)), so
everything in the old plan that was keyed to a hook response no longer describes
anything we intend to build. What the old plan got *right* — the information
barrier, "a dropped decision is recorded, never silently skipped", "no silent
gaps" — survives with its enforcement point moved, and is restated below.

Every claim carries its status: **[M]** measured (with N and design) · **[C]**
read out of this repo's code at `origin/main`, with file and line · **[I]**
inferred from one of those · **[U]** unmeasured.

## 0. The task in one sentence

> **A supervisor process consumes the actor's live output stream and, when its
> policy says the moment has come, writes one short user message into a sink
> that reaches the actor's stdin.**

Three things follow, and they are the three sections that matter: what it is
allowed to see (§3), when it speaks (§4), and what it may say (§5).

## 1. Boundaries — three layers, and this task is only the middle one

| Layer | What it answers | Owner | Status |
| --- | --- | --- | --- |
| Plumbing | how stdin stays open into a sandboxed actor; who holds the write end; when the run ends | [task 16](task-16-live-correction-channel-in-the-harness.md) | **design only, unauthorized** |
| **The supervisor component** | **what it may see, when it speaks, what it may say, what it records** | **this task** | this plan |
| The experiment | which tasks, the baseline sweep, the paired arms, whether supervision moves the resolved rate | the measurement rig | separate work, consumes this component |

**The seam between the first and the second is a sink the supervisor is handed
and does not own.** It writes lines; it never opens the channel and **never
closes it**. That is not a stylistic preference: **[M]** the CLI exits when its
stdin reaches EOF ([report §4](../../../experiments/trace_synthesis/streamjson_input/REPORT.md),
N=5 multi-message sessions), so closing the sink *is* the termination
mechanism, and termination belongs to whoever owns the process — task 16 in the
sandbox, the rig on the host today. A supervisor that may close the sink is a
supervisor that can end a rollout by dying.

This is why the component is buildable now although task 16 is not authorized:
it depends on a `write(line)` sink, not on a FIFO. The rig drives `claude`
host-side as a subprocess ([the compliance driver already
does](../../../experiments/trace_synthesis/mid_turn_compliance/driver.py)), so
the sink is that subprocess's stdin today and the in-sandbox relay later,
without the component changing.

## 2. Why "when to speak" is the design, and not a detail

**[M]** In the compliance batch, **8 of 8 non-compliances arrived too late** —
the intervention landed after the actor had already moved past the moment it
addressed — and firing unconditionally on a syntactic pattern made roughly half
the interventions redundant
([report §6.2](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)).
The batch's headline verdict (`BELOW_BAR`, compliance 0.529 against a
pre-registered 0.70) is **not** the input here; the owner's ruling is that it
answered a question chosen for its measurability
([report §6.1](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)).
What survives that re-labelling is the timing result, and it survives *stronger*
than the verdict it sat under: the failures were **timing**, not refusal.

So the component's shape follows from one sentence: **the thing we do not know
is when to speak, therefore when-to-speak must be the part that is easiest to
replace.** A supervisor with its trigger welded in is an instrument that can
measure exactly one hypothesis.

## 3. The information barrier is a constructor, not a prompt

**The barrier is a property of the type the supervisor is constructed with, not
an instruction in its prompt.** A prompt-level barrier ("do not use the gold
patch") is unfalsifiable at review time and untestable at build time; a
constructor that has no field for the gold patch is checked by the compiler and
by one named test.

Two inputs, and they are different in kind:

- **The task statement — handed over, not observed.** What the actor was asked
  to do, given at construction by whoever wrote the prompt. **The barrier keeps
  out the solution, not the goal**: a supervisor that cannot see what was asked
  cannot tell deviation from progress, and is left objecting to style.

  It arrives this way rather than being read off the stream because *which
  message is the brief* is a fact about **origin**, and position is only a
  proxy for it. A filter that promoted "the first user text I have seen" would
  admit an outside interjection as the task whenever the supervisor attached
  after the actor had already spoken — the proxy fails exactly where the
  supervisor is most likely to be attached late.
- **Evidence — what the actor produced.** Its assistant messages and the
  results of its own tool calls, in order, with the cursor that identifies
  where in the stream the supervisor is. **[C]** These are the record types
  `event_stream_to_conversation` already parses
  ([`convert.py:51`](../../../src/swe_lab/harnesses/claude_code/convert.py)),
  which is where the shapes are defined rather than re-derived here.

  **Every user text is excluded**, and the two kinds are distinguished only so
  the record can say which it was: text carrying the intervention tag came from
  this supervisor, anything else is an outside interjection. Neither is an
  observation of what the actor did, and admitting the supervisor's own words
  would let it read its output as the actor's behaviour. The filter is
  **stateless**, so where a supervisor attached cannot change its verdict on a
  message.
- **Guidebook — the validated phase-B artifact.** `Observation.guidebook`
  carries the complete Markdown to both judge and writer. The constructor's
  exact allowlist is `task`, `evidence`, `cursor`, `said`, `guidebook`; its
  negative-control test separately attempts `gold_patch`, `reference_patch`,
  `test_patch`, `hidden_tests`, `fail_to_pass`, `pass_to_pass` and
  `fix_commit`. The guidebook is the one reviewed derivative allowed through,
  not a general opening for raw privileged artifacts.
- **Criterion — the pinned general-practice artifact**, built into the judge
  rather than travelling this channel; see
  [§3.1](#31-the-barriers-second-half-what-the-judge-may-reason-from). It
  remains beside, and does not replace, the guidebook.

**The barrier's claim, stated plainly because one described more strongly than
it is, is worse than none:**

> The supervisor receives the complete validated guidebook but has no separate
> input for the gold patch, reference patch, test patch or hidden tests. What
> crosses into the actor is only the writer's tagged correction. The writer is
> intended to teach rather than recite; shallow checks cover named surface
> forms, while semantic paraphrase remains a human-audit question.

### 3.1 The barrier's second half: what the *judge* may reason from

**`Observation` guards the raw-input entrance; the writer guards what is said.**
The guidebook deliberately contains a derivative written with privileged
material in view, so excluding it would also exclude the instance-specific
reason for a useful nudge. ADR-0018 moves the boundary from access to speech:
both model calls may read the guidebook, while only the writer's checked output
can enter the actor conversation. This does not make paraphrase mechanically
safe; it makes the residual risk auditable rather than pretending a field
projection solved it.

**The rule, in the form that can be checked:**

> **The judge's criterion is a named, committed artifact that is byte-identical
> for every instance.**

**What byte-identity buys, exactly.** It removes *per-instance selection*: no
run can be handed a criterion written for the instance in front of it. It does
**not** prove the shared artifact is free of solution knowledge — one committed
criterion could carry the fixes for every instance and still be byte-identical
everywhere, and the redundant path/n-gram check is neither exhaustive nor always
runnable. **The content question is a review and provenance question**, answered
once by reading the artifact in its pull request; the digest's job is to keep
that reviewed text in force until someone re-pins it deliberately. With that
scope, the second half has the same four parts as the first:

| | |
| --- | --- |
| **artifact** | the criterion file, in the repository |
| **check** | `sha256(criterion)` equals the pinned constant — and, redundantly but cheaply, no shared file path and no shared 8-gram with this instance's gold patch |
| **rejection** | `CriterionRejectedError`. Neither a recorded gap nor a lapse — both are boundaries a *running* supervisor could not cover, and this is refused before one runs: a criterion that is not the reviewed one leaves nothing to judge against |
| **named test** | `test_a_criterion_quoting_the_gold_patch_is_rejected` — a criterion that quotes the fix must make the check fail |

**The startup gates are wired.** `supervising_policy` loads the pinned criterion,
and a guidebook-guided harness validates the declared guidebook before its first
actor script is launched. Missing or malformed guidebooks raise
`GuidebookRejectedError`; they never select the unguided prompt as a fallback.
`SpeakWhenOffTrack` passes the criterion and the observation carrying the
guidebook to both model calls.
`SpeakAt` takes none and judges nothing — it is the timing knob, and applying a
criterion gate to a policy with no judgement would be theatre.

**What the criterion being constant does *not* mean.** The judge's *prompt* is
still instance-specific — it carries the task statement and the actor's own
records, which is the whole point of §3. What is pinned is the **standard** it
measures against, not the material it measures.

**The redundant half degrades honestly.** The path- and n-gram-overlap check
needs the gold patch to be available where the check runs; for a dataset that
records none, it cannot run and the hash equality carries the invariant alone.
That is a weaker state and the run should say so rather than reporting a check
it did not perform.

**When this check fires, that is the design working.** The day someone genuinely
needs a per-instance criterion, the hash stops matching, `load_criterion`
rejects, and the run-level construction path stops with it.
That is the moment the barrier has to be re-examined by a person — **not an
obstacle to route around**, and the rejection path is deliberately loud so that
routing around it takes a visible decision rather than a quiet edit.

**The provenance statement survives as rationale, not as a guarantee.** *"Its
author must not have read the fix"* is a claim about a person; nothing can test
it, and by this repo's own rule an untestable *must* is either given a test or
downgraded. It is kept here because it says **why** the artifact is pinned, and
it no longer does any of the load-bearing work.

**Tests that hold the implemented boundary** (per `AGENTS.md`: an invariant
needs a test or the sentence is downgraded):

- `test_supervisor_input_carries_the_guidebook` — the input type's five field
  names are asserted against an **exact allowlist**, and the positive artifact
  handoff is exercised.
- `test_supervisor_input_rejects_separate_privileged_material` — each raw
  privileged name is an actual constructor attempt, so arbitrary keyword
  acceptance does not pass merely because the guidebook arm passes.
- `test_a_criterion_quoting_the_gold_patch_is_rejected` — §3.1's criterion
  half: the loader rejects rather than recording a gap.
- `test_a_guided_run_rejects_an_unusable_guidebook_before_actor_start` — both
  missing and malformed guidebooks refuse the run before an actor script.
- `test_a_forged_criterion_cannot_build_the_policy` and
  `test_the_judge_is_handed_the_canonical_criterion_every_call` — what *is*
  enforced today: `SpeakWhenOffTrack` refuses any criterion whose digest is not
  the pinned one, and passes it to the judge on every call rather than storing
  it beside one — **hand-off, not consumption**.
- `test_the_task_is_given_not_read_off_the_stream` — the goal reaches the
  policy without any message having to be guessed to *be* the brief.
- `test_a_supervisor_attached_mid_run_admits_no_user_text` — where the
  supervisor started cannot change what counts as evidence.
- `test_the_supervisors_own_words_never_come_back_as_evidence` — its own
  correction is **memory, not observation**. Admitted as evidence, the
  supervisor would be reading its own output as something the actor did.
- `test_no_user_text_is_evidence_whoever_wrote_it` — the exclusion is by
  origin, and covers the outside interjection as well as our own.
- `test_every_event_is_dispositioned_in_the_record` — the log says *why* a
  message was not judged rather than omitting it, so a reader can tell an
  exclusion from a gap.

**The supervisor's own words live in its memory, on a channel separate from its
evidence.** Since a correction never returns as an observation, a policy has
nothing to compare against unless the supervisor keeps a list of what it has
already said — and would otherwise be free to say the same thing three times in
a row. That list is handed to the policy beside the evidence, never mixed into
it (`test_what_it_said_is_remembered_outside_the_evidence`).

## 4. When it speaks, and what it says

```
SpeakPolicy:  consider(observation) -> Intervention | None
```

The observation is §3's evidence at the current cursor plus the supervisor's own
run state (what it has already said, and where). **The policy decides whether
and when; it does not decide the channel, does not write, and cannot end the
run.** Returning `None` is the ordinary case and is not an error.

### 4.1 What "off track" means, and why the bar is not a delay

A policy that speaks needs a judgement, and the judgement is a model call over
the `Observation`: the task, a window of the actor's own records,
and what has already been said. **It asks two questions, not one:**

1. **Is the actor off the criterion's path?**
2. **Left alone, would it come back by itself?**

Only *off-track **and** not self-correcting* speaks. The second question is
where the restraint lives — an actor that has just said "that didn't work, let
me reconsider" is already doing the thing an intervention would ask for, and
speaking there is the redundancy the graded batch measured (roughly half its
interventions).

**Precision comes from the bar; restraint comes from the budget; neither may
come from delay.** The obvious way to buy precision is to debounce — require
the deviation to persist across two judgements before speaking. **This design
rejects that**, and the reason is the one measurement we have: 8 of 8
non-compliances arrived *too late*
([report §6.2](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)).
Debouncing buys precision with exactly the currency we are already short of. So
the first intervention is never delayed by design; what is limited is **how
often** the supervisor may speak, not **how long it waits** before it may.

**The judgement is subjective, and the design says so.** The owner's ruling
stands: this is a language model deciding on-track or off-track, so it is
probabilistic and a defensible reading suffices. The policy therefore must not
be built as though the judge were an oracle — no retry-until-agreement, no
second call to break a tie. One call, one verdict, recorded.

### 4.2 The three parameters a sweep needs, and their honest status

The parameters are named here so they can be varied without editing the policy,
because **timing is the variable we have never been able to turn**:

| Parameter | What it controls | Status |
| --- | --- | --- |
| `budget` | how many interventions a whole run may carry | **[U]** proposed 3; no measurement supports any number |
| `cooldown` | how many boundaries must pass **between** interventions — it never delays the *first* one | **[U]** proposed 4 |
| `window` | how many of the actor's records the judge sees | **[U]** proposed 8. **[C]** is what `judge_steps.py` used, which is *provenance, not evidence*: 8 was never tested against 4 or 16 |

**None of these three has a measured value, and the plan will not pretend
otherwise.** They are written as parameters precisely so the rig can sweep them;
a default that arrived by taste and then hardened into a constant is how a
number nobody chose ends up in a result.

### 4.3 Silence is structural, not hoped for

**A boundary with no evidence is not judged at all.** Before the gates, an
empty evidence window returns `Unjudged` and the judge is not called: there is
nothing of the actor's to measure against the criterion, and a judge asked
anyway answers about a record it was never shown. This is the head of a run and
nothing else — the window is empty only until the actor's first message — and it
is where the failure was observed. Replaying the first end-to-end run's 170
events through `EvidenceFilter`: its first correction went out at `cursor` 4,
where the judge held **zero** admitted records (events 1–4 are `system/init`,
`system/thinking_tokens`, `rate_limit_event`, `system/thinking_tokens`, every
one of them `excluded-nothing-to-keep`), and it asserted a fact — that the actor
had not yet opened `models.py` to see how `from_isbn` branches — which the actor
answered by pointing at the read it had already done (`models.py:377-446`).
The rule is about *zero* evidence and nothing wider: cursors 8 and 12 were
judged on 3 and 6 records and are a different question — judgement quality, and
how `window` should couple to the batch — which this does not touch and must not
be read as mitigating. It costs the matched control nothing: the skip reads the
evidence window alone, so both arms skip the same boundaries.

**The budget gates speech, not judgement**, and the order follows from that —
an earlier draft of this section put budget first, which would have made
`SpeakWhenOffTrack(budget=0)` skip the judge entirely and quietly destroy the
matched control §4.4 depends on. Past that precondition, `consider()` returns
`None` unless every gate passes, in this order:

1. the judge says off-track, else silent;
2. the judge says it will not self-correct, else silent;
3. **the would-have-spoken marker is recorded here**, before any budget is
   consulted — this is what the control arm produces (what it buys is stated
   once, at `workflow.definitions.CONTROL_BUDGET`);
4. budget remaining, else silent (with the marker already recorded);
5. cooldown elapsed since the last intervention, else silent (likewise);
6. the writer produces a usable line, else **a recorded lapse** bounded to this
   boundary (§6.1) — never a retry.

The cost of this ordering is stated rather than hidden: **the judge runs on
every boundary carrying evidence even after the budget is spent**, so a
`budget=0` policy still pays for a judge it can never act on. Why that is worth paying is stated once,
at `workflow.definitions.CONTROL_BUDGET`.

A policy that speaks by default cannot be produced by omitting a parameter,
because **`budget` has no default**: a policy that may speak must state how
often. The guarantee is tested three ways: a judge that always says on-track
yields zero interventions; `budget=0` yields zero **interventions and a non-zero
count of would-have-spoken markers** when the judge always says off-track — the
marker count is what proves the judge still ran; and `budget=k` yields at most
`k` on a trace where it always says off-track. The precondition has its own:
`test_a_boundary_with_no_evidence_is_never_put_to_the_judge` asserts the judge
is not **called** — not that it answered and was ignored — and that such a
boundary leaves the markers, the budget and the cooldown untouched;
`test_a_boundary_with_no_evidence_is_recorded_as_unjudged_not_silent` asserts
the log says so in a row a reader can tell from a silence.

### 4.4 The implementations, and why `budget=0` replaces a second control

1. **`NeverSpeak`** — already shipped. No judge, no speech; the trivial control
   and the test double.
2. **`SpeakAt(cursors)`** — speaks a fixed line at fixed cursors, with no judge
   at all. This is the **timing knob in isolation**: it varies *when* while
   holding *what* and *whether* constant, which is the one comparison the graded
   batch could not make because its trigger was entangled with its criterion.
3. **`SpeakWhenOffTrack(judge, writer, criterion, budget, cooldown, window)`** — the real
   one, as designed above.

**A fourth class was considered and is not needed.** The paired control wants a
supervisor that *judges but never speaks* — same calls, same cost, same
cadence, zero corrections — and that is exactly `SpeakWhenOffTrack(budget=0)`,
**which works only because §4.3 consults the budget after the judgement rather
than before it.** Reverse those two and the control silently stops paying for
its judge, at which point it is no longer the same run minus the corrections.
It also produces something more useful than silence: a record of **where it
would have spoken** on control traces; what that buys a comparison is stated
once, at `workflow.definitions.CONTROL_BUDGET`.

### 4.5 What it writes

A second model call, given the same `Observation` — including the complete
guidebook — and the same general-practice criterion. The shape is the user's,
quoted because paraphrasing it loses the thing that matters:

> "不对不对，你不太应该看那些 fail，我觉得看这些是更相关一点的" — a person
> watching over your shoulder, hedged and offhand.
>
> **Not**: "ok the right answer is X, go do that."

So the writer is instructed to produce one short line that **hedges** ("I don't
think…", "I'd look at…"), **points at a direction** already visible in the
actor's own work, and **names no fix**. Three of those are prose properties. The
checkable ones are stated as checks and the rest is stated as unenforced:

The writer takes its reason primarily from `Justification`; `Goal`, `Actions`
and `Expected observations` locate the current stage, while `Edits` and `Tests`
inform private judgement rather than text to relay. That source discipline is
intended, not mechanically parsed. §5 fixes what *any* intervention must
satisfy — the length cap, the tag, and the ban on fabricating an observation.
The writer adds two checks that rule out failures a length cap does not:

| Writer check | What it actually rules out |
| --- | --- |
| no fenced code block and no diff hunk header | the most literal form of handing over the answer |
| no verbatim eight-word shingle shared with the complete guidebook | any guidebook section being **pasted through** the channel into the actor's context; eight reuses `criterion.py`'s established `SHINGLE_WORDS`, rather than introducing another unexplained threshold |

A third property — *not a repeat of what it already said* — belongs to the
policy rather than the writer: `said` is in the `Observation` precisely so the
judgement can decline to speak again, and rejecting a duplicate after paying for
it would be the wrong layer.

**The shingle guard is a floor, not a proof.** A paraphrase, short constant or
decisive identifier defeats it. It catches literal copying from `Edits`,
`Tests`, `Justification`, or any other guidebook section without inventing a
second parsed representation of the artifact.

**What a policy can actually see about timing** is bounded by the channel, and
the plan says so rather than letting an implementer discover it: **[M]** the
delivery lag was exactly one agent-loop record on every one of 37 delivered
interventions
([report §6.2](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)),
and **[M]** whether a correction is absorbed mid-turn or produces its own turn
is decided by what the actor happens to be doing at that instant — a race
neither side arbitrates
([task 16 §2.2](task-16-live-correction-channel-in-the-harness.md)). **[U]** No
policy can therefore *guarantee* it speaks before an action; it can only shorten
the distance. A plan that promised otherwise would be promising something the
channel does not sell.

### 4.6 The leak audit is a human's, and the record has to support it

The writer checks in 4.5 are automatic and shallow by construction. **Semantic
leakage remains a human judgement**, so each intervention row in the existing
declared `supervisor.jsonl` artifact records the guidebook SHA-256, the exact
credential-free judge request, its stated reason and the line sent. This
extends a native diagnostic artifact; it does not add or change a report field.
Without the input, an audit can see that a nudge looked innocuous and cannot see
what privileged derivative informed it.

The guidebook itself is removed from the actor workspace before launch and is
not added to the serialized `Conversation`. The invariant test distinguishes a
real leak from task text the Oracle repeated: it subtracts any 12-word
guidebook/conversation shingle also found in the task prompt, and includes both
a shared-source control and a guidebook-only contamination control.

### 4.7 A retrospective check that costs no rollouts, and its ceiling

**The graded batch's interventions are on disk, so gate (a) can be replayed
against them before any parameter is chosen.** The correction the design claims
to make is precisely the defect that batch exposed: its trigger was syntactic
and **never asked whether there was anything left to ask for**.

**The set, counted from the committed evidence rather than from memory:** of the
20 `mid` runs, 3 produced no trigger and **17 were interventions**; of those,
**14 had the predicate already satisfied at the moment the trigger fired**
(`predicate_already_true.at_trigger`), leaving **3 valid triggers**. This
matches [the report's](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)
own "fourteen of 17".

**Worth stating plainly, because it changes how much that batch is owed:** under
that rule the graded arm contained **3** interventions that still had something
to ask for. Whatever `BELOW_BAR` measured, it measured it over that.

**The question is paired to the fixture, and getting this wrong would
manufacture a false refutation.** Gate (a) judges *deviation from general
engineering practice*; the flag records whether *that fixture's predicate* was
already satisfied. They are not the same thing, and a record whose predicate was
satisfied can still be genuinely off track **on some other axis**. So the replay
asks the matched question:

> *On the dimension this fixture's correction addressed, is the actor deviating
> at this moment?*

**Not** "is anything wrong here". A gate (a) that answers "deviating" about a
different axis is **right**, and scoring it as a miss would refute the design
with an answer to a question nobody asked.

**The reading, fixed here before the replay runs:**

- On the **14**, gate (a) asked the paired question should answer *not
  deviating* — the work was already done, and a judge that says otherwise has
  reproduced the defect.
- On the **3**, the counts are reported and **decide nothing**: n=3 is far under
  that experiment's own pre-registered floor of 12.

**Three limits carried over from the source, each sufficient on its own** — the
validity split is exploratory rather than pre-registered, its partition was
drawn after seeing which runs failed, and the fixtures have **no guidebook**, so
what is being exercised is the *general-practice* criterion of
[§3.1](#31-the-barriers-second-half-what-the-judge-may-reason-from), not the
phase-B pipeline. **This replay can therefore falsify the gate and cannot
validate it — and it can falsify it only on the paired dimension above.** If gate (a) calls the 14 deviations, the design is wrong in the
way that matters; if it does not, we have removed one known failure and learned
nothing about the rest.

**Gate (a) alone is the whole check.** Gate (b) — *would it self-correct* — has
**no** supporting evidence at all, so it must not be tuned to carry weight here;
already-finished work is not a deviation under (a), and reaching for (b) to
explain that would be fitting a second knob to the same data.

**The inputs are read-only and carry operator PII, which constrains what the
replay may emit.** The committed evidence carries each row's action, correction
and validity flags, but **not the conversation prefix** the judge needs; those
raw captures are off-repo, gitignored for that reason, and owned by another
component. The terms this replay works under:

- **Read only** — nothing modified, moved, or copied into another worktree.
- **Only aggregate counts and classification labels may be derived.** No
  verbatim fragment, path, or username reaches a commit, a PR description, or a
  message between agents.
- **A record is cited by its index**, never by its content.

These are the same redaction terms the repo already applies to trace records
([`AGENTS.md`](../../../AGENTS.md) — *redact operator PII in any trace record*);
they are restated here because this replay reads a corpus that never enters the
repository, where the usual reviewer's check on a diff cannot see what was
quoted.

## 5. What it may say

| Constraint | How it is held | Status |
| --- | --- | --- |
| **Bounded length** | a named constant, enforced on construction; over-cap text raises rather than truncates | testable → `test_an_over_length_intervention_is_refused` |
| **Identifiable as external** | the provenance tag the compliance batch used; **[M]** 0 of 37 interventions were challenged as unattributed ([report §6.2](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)) | testable → `test_every_intervention_carries_its_tag` |
| **Directional, not a solution** | the writer prompt uses guidebook `Justification`; shallow checks reject fenced code, diff hunks and eight-word guidebook copying; a human reads semantic content | **intended; surface forms enforced, semantic paraphrase not enforced** |
| **Never a fabricated observation** | the supervisor emits only its own message; it never rewrites, replaces or attributes anything to a tool | testable → `test_the_supervisor_emits_only_its_own_message` |

The third row is deliberately downgraded. "Short, directional, no solution
content" is exactly the property the compliance batch showed a *mechanical*
criterion cannot hold: **[M]** requiring a machine-checkable predicate reshaped
the intervention into one naming a concrete next action — "already close to
handing over the answer"
([report §6.1](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)).
Writing it here as an enforced invariant would repeat the mistake one layer
down. It is a review property with independently tested length, fenced-code,
diff-hunk and literal-copy floors underneath it. Short directional prose and an
inline file reference are acceptance controls, so those floors cannot pass by
rejecting every useful line.

**Every intervention is written to a log, one row each**, with: the cursor it
was decided at, the wall clock, the policy that produced it, the guidebook
identity, judge request and reason, the text as sent, and whether the write
succeeded. **[M]** The precedent for needing this is a
recorded failure, not a hypothetical: in the steered re-run the polling thread
died on a malformed reply at boundary 13 and every later boundary went unjudged
with nothing in the record to say so ([`spec.md` §11](../spec.md#11-open-questions)).

## 6. Failure modes this component owes an answer to

| Failure | The answer this task must implement |
| --- | --- |
| The policy raises, and bounds the failure to this call | recorded as a **lapse** at that cursor; the run continues *and stays evidence*, carrying the count (§6.1) |
| The policy raises anything else | recorded as an explicit **gap** at that cursor; the reach is unknown, so the run stops being evidence about supervision ([`spec.md` §12](../spec.md#12-invariants-intended-enforced-where-marked)) |
| The sink write fails, or the sink is already closed | recorded, and the supervisor stops speaking — it does **not** close the sink and does **not** kill the run |
| The supervisor dies | the run's fate belongs to whoever owns the process (task 16 / the rig); this component asserts nothing about it |
| The stream ends while a decision is in flight | the decision is dropped **with a record**, never applied to a later cursor |

### 6.1 Two scopes of failure, two records

The failure table above used to have one row for "the policy raises", and it
mixed two things that need opposite treatment: a single failed model call, or a
single line the writer could not make usable — after which the next boundary is
judged normally — and the policy's own state machine breaking, after which
nothing is known about any later boundary. One row meant the consumer could only
take the worst reading, and a run was thrown away whole to account for one
boundary.

**What a `lapse` row proves.** *This* boundary, at *this* cursor, went
unsupervised, for *this* reason, and the policy asserted at the moment of
failure that its own state survived. That is what keeps the run evidence: a
reader can name every boundary that was not covered. The distinction the
product sells is exactly this one — "we do not know what happened" and "we know
precisely which one we missed" are not the same fact, and a recorded, single
unsupervised boundary must not be priced like a hole of unknown reach.

**What it does not prove.** Not that the actor did anything at that boundary;
not that the *next* boundary was covered — a later lapse says otherwise, and the
count is the reading; not that the policy's self-assessment was independently
checked. The warrant is the policy's own declaration, which is why it is made by
**raising a named exception** (`PolicyLapseError`) rather than inferred from an
exception type at the catch site. Only the policy knows which of its failures it
can bound; a supervisor classifying on its behalf would be guessing exactly
where the consumer is forbidden to.

**Scope is asserted, never inferred.** An exception that does not carry the
declaration is unbounded, and the run is excluded as before. Silence about
scope is not a claim of a small one.

**Where the bound comes from.** Not from the error — from *where* it happened.
`SpeakWhenOffTrack` claims it for its two calls out to a model and nowhere else:
a judge call fails before the method has touched its own state, and a writer
call fails after the deviation is already marked and before any budget is spent.
The gate arithmetic between them is unwrapped on purpose, and
`test_a_break_in_the_policys_own_state_is_not_bounded` is what keeps it that
way.

**The count is consumed, not just recorded.** A lapse leaves the run's
denominator containing a boundary nobody watched, so
`SUPERVISION_LAPSE_METRIC` carries the count out of the log and into the run's
metrics, which `run_task` copies verbatim into `AttemptRecord.metrics` — the
path `SUPERVISION_METRIC` already takes, and where a reader of the outcome is
standing. It stays separate from `SUPERVISION_METRIC`, which is the one that
changes the outcome word. A count that lived only in `supervisor.jsonl` would
be one more fact recorded and never read.

**Both hops are tested, and the second one had to be.** The observer's half is
`test_a_bounded_lapse_is_counted_where_the_outcome_is_read`; the runner's half
is `test_a_metric_an_observer_contributes_reaches_the_persisted_record`. The
first alone is not enough, and the reason is worth keeping: it builds its
`AttemptResult` from the contribution directly, so deleting the runner's copy
leaves it green while the number vanishes from the record. This metric changes
no outcome word, so the record is its *only* consumer — a claim about it that
stops at the contribution is a claim about nothing (found in review of the PR
that added it).

## 7. Acceptance

- **The in-sandbox fold check — ✅ run 2026-09-02, `MATCH`**
  ([report](../../../experiments/trace_synthesis/sandbox_fold_check/REPORT.md)).
  The injected block inside the sandbox on the pinned `2.1.212` is byte-identical
  to the host measurement — `len 440`, `sha256 3ba88726…fb90c8` — as is every
  other wire count: 7 messages, the same role sequence, 4 `system-reminder`
  blocks, 4 API calls of which 3 are agent-loop.
  [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
  **refutation condition therefore does not fire**, and the byte-identity result
  the attribution decision rests on is about the artifact we ship rather than
  only about the host. **N=1 against a baseline of N=1**: what is established is
  that two recorded outputs were equal, and that the combined change of version
  and environment did not alter the wrapper for this input — not that either
  change is separately harmless, and not that the path is repeatable, which one
  pair cannot show.

  The condition as originally written, kept because it is what the check was run
  against — it was ADR-0013's refutation condition, carried here so that it was
  scheduled rather than filed against nobody. The supervisor delivers **one** intervention *inside the
  sandbox*, and the fold's measured shape — the injected block's **length** and
  **`sha256`** — must match the host measurement, `len 440` /
  `sha256 3ba88726…fb90c8`. **Read the expected values from the committed
  artifact** (`experiments/trace_synthesis/streamjson_input/runs/proxy-midturn/evidence.json`),
  which is where they live; the suite's test asserts that the headless and TUI
  captures agree, not that either equals a literal.
  **A mismatch means this task is not complete** and opens the ADR's refutation
  path: the byte-identity result would then be about the host binary and not the
  one we ship. This is on the critical path either way — the rollouts that will
  use this channel run in containers, and every measurement of the channel so
  far is host-side.
- **Every named test above exists and fails when its invariant is violated.**
  The count is deliberately not written here: it has grown twice already, and a
  number in this line would be wrong before the code lands.
- The guidebook-guided harness refuses missing or invalid guidebooks before its
  first actor launch, passes the exact valid artifact to both model calls, and
  records the audit tuple in its existing supervisor log.
- Each shallow writer check has a rejection arm and a useful-output acceptance
  arm; the length in the unguided byte-compatibility fixture is a literal rather
  than derived from the production constant.
- A `SpeakPolicy` can be replaced without touching the stream consumer, the
  intervention type, or the log — demonstrated by `NeverSpeak` and the real
  policy sharing every other line.
- One end-to-end test drives the component over a **recorded** event stream with
  a stub sink and asserts the log accounts for every cursor: a judgement, a
  silence, a boundary nothing was judged at, a lapse, or an explicit gap.
- **No live run is part of this task's acceptance.** Live behaviour is the rig's
  measurement, and this task must not consume its budget.

## 8. What this task is not

- **Not the channel plumbing.** The FIFO, relay, reaping order and termination
  rule belong to [task 16](task-16-live-correction-channel-in-the-harness.md).
  This task owns only the guidebook preflight and handoff at the existing
  segmented-harness seam.
- **Not the experiment.** Task selection, the baseline sweep and the paired arms
  belong to the rig; this component is what the rig consumes.
- **Not a judge-quality study.** Whether the judge is any good is
  [task 06](README.md) and the rig's problem.
- **Not an authorization to produce traces for training.** [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)
  moves the *attribution* decision; what a shippable trace is remains
  [`spec.md` §6](../spec.md#6-the-trace-is-the-conversation-unedited).

## 9. What stays unmeasured, and is not designed around

- **[U]** Everything about this channel measured so far is **host-side**; the
  sandbox runs a pinned binary with a pinned `CLAUDE_CONFIG_DIR`. This is no
  longer left to [task 13](README.md) to pick up: §7 makes the one-delivery
  in-sandbox check an **acceptance condition of this task**, because a
  falsification condition nobody is scheduled to evaluate is not one. Task 13
  remains the *broader* sandbox confirmation; what this task owes is the single
  shape check.
- **[U]** What a *good* policy is. That is the rig's question, and the reason
  §4 is a seam.
- **[I]** Cost of checking, ≈ $0.0093 per judged step, inferred from PR #305's
  $0.643 over 69 judged steps across mixed populations — an **order of
  magnitude, not a rate**, and it may not be cited as one
  ([DEBATE-VERDICT](../../../experiments/trace_synthesis/process_supervision/DEBATE-VERDICT.md)).

## 10. Dependencies and scope

**Dependencies:** [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)
for attribution, [ADR-0018](../../decisions/ADR-0018-the-supervisor-reads-the-guidebook-but-must-not-recite-the-answer.md)
for the speech boundary, and
[task 04](task-04-oracle-analysis-task.md) for the validated phase-B artifact.
It is not blocked on task 16 because the segmented harness already owns its
delivery seam.
**Scope:** L.
