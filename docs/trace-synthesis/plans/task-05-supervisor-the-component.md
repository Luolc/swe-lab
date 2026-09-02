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
- **Criterion — the pinned artifact**, built into the judge rather than
  travelling this channel; see [§3.1](#31-the-barriers-second-half-what-the-judge-may-reason-from).
  **Not** phase B's guidebook: `oracle.py` writes that one with the reference
  patch, the exact grading procedure and the repository's unpurged history in
  hand, so a supervisor reading it would steer down the fix's path without ever
  quoting it. `Observation` has no field for it —
  **[C]** `test_supervisor_input_carries_no_privileged_field` asserts the field
  set against an exact allowlist, and the field was **deleted** rather than left
  unused, because a field nobody may fill is a hole waiting for the next
  person.

**The barrier's claim, stated plainly because one described more strongly than
it is, is worse than none:**

> The supervisor never receives the gold patch, the reference patch, the test
> patch, the hidden tests, or a phase-B guidebook — there is no field on its
> input that can carry them, and no code path that fills one. What it measures
> against is one pinned criterion, so **no run selects a different criterion per
> instance**. What remains outside the claim: whether that shared text is itself
> free of solution knowledge is settled by reviewing the artifact, not by the
> digest; and the judge and the writer are model calls, with nothing here
> bounding what a model infers from the actor's own records.

### 3.1 The barrier's second half: what the *judge* may reason from

**`Observation` guards one entrance, and it is the one facing the actor. The
judge has a second one — its criterion — and material derived from the answer
pierces the barrier from behind.**

The concrete case is not hypothetical. SWE-bench instances carry **no
per-instance guidebook**. If one is written for an instance by someone who has
read the gold patch, the supervisor is *paraphrasing the answer*: it will never
say "the fix is X", and it will still push the actor down X's path. That is the
leak the whole design exists to prevent, arriving through a different door — and
**no field test on `Observation` can see it, because it never travels that
channel.**

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
| **rejection** | `CriterionRejectedError`. Not a recorded gap: a criterion that is not the reviewed one leaves nothing to judge against |
| **named test** | `test_a_criterion_quoting_the_gold_patch_is_rejected` — a criterion that quotes the fix must make the check fail |

**[U] The startup gate is not wired yet, and this row says so rather than
implying otherwise.** `load_criterion` has no production caller: the criterion
is consumed by the *judge*, which is not implemented, so that is where the
run-level refusal belongs. What is enforced today is narrower and is the whole
of the current claim: **`SpeakWhenOffTrack` refuses to construct unless its
criterion's digest is the pinned one, and passes that criterion to the judge on
every call.** Hand-off is the whole of it — a protocol cannot compel an
implementation to use a parameter, so *what the judge measures against* is a
judge-implementation invariant whose named behavioural test belongs to that PR.
`SpeakAt` takes none and judges nothing — it is the timing knob, and applying a
criterion gate to a policy with no judgement would be theatre. **The judge's PR
discharges this**, with a named test that a forged artifact prevents the run
from starting.

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
needs a per-instance criterion, the hash stops matching and the run stops. That
is the moment the barrier has to be re-examined by a person — **not an obstacle
to route around**, and the rejection path is deliberately loud so that routing
around it takes a visible decision rather than a quiet edit.

**The provenance statement survives as rationale, not as a guarantee.** *"Its
author must not have read the fix"* is a claim about a person; nothing can test
it, and by this repo's own rule an untestable *must* is either given a test or
downgraded. It is kept here because it says **why** the artifact is pinned, and
it no longer does any of the load-bearing work.

**Tests that must land with the code** (per `AGENTS.md`: an invariant needs a
test or the sentence is downgraded):

- `test_supervisor_input_carries_no_privileged_field` — the input type's field
  names are asserted against an **exact allowlist**, so *adding* a field is what
  fails the test, not merely adding one of today's forbidden names. A denylist
  of `{gold_patch, reference_patch, test_patch, hidden_tests, fail_to_pass,
  pass_to_pass, fix_commit}` catches the names we thought of; an allowlist
  catches the one we did not.
- `test_a_criterion_quoting_the_gold_patch_is_rejected` — §3.1's second half:
  the loader rejects rather than recording a gap. **This is a loader test, not
  a run-level one** — the startup gate is `[U]` and lands with the judge.
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

**The budget gates speech, not judgement**, and the order follows from that —
an earlier draft of this section put budget first, which would have made
`SpeakWhenOffTrack(budget=0)` skip the judge entirely and quietly destroy the
matched control §4.4 depends on. `consider()` returns `None` unless every gate
passes, in this order:

1. the judge says off-track, else silent;
2. the judge says it will not self-correct, else silent;
3. **the would-have-spoken marker is recorded here**, before any budget is
   consulted — this is what the control arm produces and what lets the two arms
   be compared at matched deviation points;
4. budget remaining, else silent (with the marker already recorded);
5. cooldown elapsed since the last intervention, else silent (likewise);
6. the writer produces a usable line, else **a recorded gap** — never a retry.

The cost of this ordering is stated rather than hidden: **the judge runs on
every boundary even after the budget is spent**, so a treatment run and a
control run pay the same judge calls. That is the price of a paired comparison,
and paying it is the point.

A policy that speaks by default cannot be produced by omitting a parameter,
because **`budget` has no default**: a policy that may speak must state how
often. The guarantee is tested three ways: a judge that always says on-track
yields zero interventions; `budget=0` yields zero **interventions and a non-zero
count of would-have-spoken markers** when the judge always says off-track — the
marker count is what proves the judge still ran; and `budget=k` yields at most
`k` on a trace where it always says off-track.

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
would have spoken** on control traces, which is what lets the two arms be
compared at matched deviation points rather than only at their endpoints.

### 4.5 What it writes

A second model call, given the same `Observation` and nothing else. The shape is
the user's, quoted because paraphrasing it loses the thing that matters:

> "不对不对，你不太应该看那些 fail，我觉得看这些是更相关一点的" — a person
> watching over your shoulder, hedged and offhand.
>
> **Not**: "ok the right answer is X, go do that."

So the writer is instructed to produce one short line that **hedges** ("I don't
think…", "I'd look at…"), **points at a direction** already visible in the
actor's own work, and **names no fix**. Three of those are prose properties. The
checkable ones are stated as checks and the rest is stated as unenforced:

§5 already fixes what *any* intervention must satisfy — the length cap, the
tag, and the ban on fabricating an observation — and those are properties of the
`Intervention` type, not of this writer. **What the writer adds are two checks
of its own**, and they are the only two worth adding because they rule out
failures a length cap does not:

| Writer check | What it actually rules out |
| --- | --- |
| no fenced code block and no diff hunk header | the most literal form of handing over the answer |
| no verbatim n-gram shared with the criterion (n≈8 words) | the criterion being **pasted through** the channel into the actor's context |

A third property — *not a repeat of what it already said* — belongs to the
policy rather than the writer: `said` is in the `Observation` precisely so the
judgement can decline to speak again, and rejecting a duplicate after paying for
it would be the wrong layer.

**The n-gram guard is a floor, not a proof.** A paraphrase defeats it, and it is
worth having anyway: it catches the failure that would actually happen, which is
a writer quoting the criterion because the criterion is the most relevant text
in its context.

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

The two writer checks in 4.5 are automatic and shallow by construction. **The
real guard on leakage is that a person can go back and read what happened**, so
every intervention is recorded together with **the judge's input and its stated
reason** — not only the line that was sent. Without the input, an audit can see
that a nudge looked innocuous and cannot see that the judgement behind it was
made from material it should never have had.

This is also what makes §3.1 reviewable after the fact rather than only at
design time: a criterion that quietly acquired instance-specific knowledge shows
up in the judge's reasoning long before it shows up in the text of a nudge.

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
| **Directional, not a solution** | **read by a human, not asserted by a checker** | **intended, not enforced** |
| **Never a fabricated observation** | the supervisor emits only its own message; it never rewrites, replaces or attributes anything to a tool | testable → `test_the_supervisor_emits_only_its_own_message` |

The third row is deliberately downgraded. "Short, directional, no solution
content" is exactly the property the compliance batch showed a *mechanical*
criterion cannot hold: **[M]** requiring a machine-checkable predicate reshaped
the intervention into one naming a concrete next action — "already close to
handing over the answer"
([report §6.1](../../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md)).
Writing it here as an enforced invariant would repeat the mistake one layer
down. It is a review property with a length cap underneath it.

**Every intervention is written to a log, one row each**, with: the cursor it
was decided at, the wall clock, the policy that produced it, the text as sent,
and whether the write succeeded. **[M]** The precedent for needing this is a
recorded failure, not a hypothetical: in the steered re-run the polling thread
died on a malformed reply at boundary 13 and every later boundary went unjudged
with nothing in the record to say so ([`spec.md` §11](../spec.md#11-open-questions)).

## 6. Failure modes this component owes an answer to

| Failure | The answer this task must implement |
| --- | --- |
| The policy raises | recorded as an explicit gap at that cursor; the run continues; the supervisor does not go quiet without a record ([`spec.md` §12](../spec.md#12-invariants-intended-enforced-where-marked)) |
| The sink write fails, or the sink is already closed | recorded, and the supervisor stops speaking — it does **not** close the sink and does **not** kill the run |
| The supervisor dies | the run's fate belongs to whoever owns the process (task 16 / the rig); this component asserts nothing about it |
| The stream ends while a decision is in flight | the decision is dropped **with a record**, never applied to a later cursor |

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
- A `SpeakPolicy` can be replaced without touching the stream consumer, the
  intervention type, or the log — demonstrated by `NeverSpeak` and the real
  policy sharing every other line.
- One end-to-end test drives the component over a **recorded** event stream with
  a stub sink and asserts the log accounts for every cursor: a judgement, a
  silence, or an explicit gap.
- **No live run is part of this task's acceptance.** Live behaviour is the rig's
  measurement, and this task must not consume its budget.

## 8. What this task is not

- **Not the harness wiring.** The FIFO, the relay, the reaping order and the
  termination rule are [task 16](task-16-live-correction-channel-in-the-harness.md),
  which remains design-only.
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
(this PR) for the attribution. **No longer** dependent on
[task 04](task-04-oracle-analysis-task.md): the judge measures against the
pinned criterion, not against phase B's guidebook. **Not** blocked on task 16,
by §1's seam.
**Scope:** M.
