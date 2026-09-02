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

- **Evidence — admitted by origin, not by role.** The **task statement**, the
  actor's assistant messages, and the tool results its own calls returned, in
  order, with the cursor that identifies where in the stream the supervisor is.
  **[C]** These are the record types `event_stream_to_conversation` already
  parses ([`convert.py:51`](../../../src/swe_lab/harnesses/claude_code/convert.py)),
  which is where the shapes are defined rather than re-derived here.

  **The task statement is admitted deliberately, and the axis matters.** An
  earlier form of this rule cut on the `user` role and excluded every user text
  — which threw out the brief along with the interjections. The barrier exists
  to keep out the **solution**, not the **goal**: a supervisor that cannot see
  what was asked cannot tell deviation from progress, and is left objecting to
  style. What is excluded is what did not come from the actor *or* the task:
  **this supervisor's own corrections**, which arrive back on the same stream as
  `user` messages, and any later external user text. The task statement and a
  correction are both `user` messages; they differ in where they came from.
- **Criterion — the guidebook.** The phase-B artifact, host-side, validated by
  **[C]** `validate_guidebook`
  ([`guidebook.py:41`](../../../src/swe_lab/trace_synthesis/guidebook.py)).

**And here is the honest limit of the barrier, stated plainly because a barrier
described more strongly than it is, is worse than none.** The guidebook is
*distilled from* privileged material in phase B — that is what phase B is. So
the claim is **not** that the supervisor is information-theoretically isolated
from the fix. The claim is narrower and checkable:

> The supervisor never receives the gold patch, the reference patch, the test
> patch, or the hidden tests — there is no field on its input that can carry
> them, and no code path that fills one. What it receives about the intended
> solution is the guidebook, and the guidebook's own content rules are phase B's
> problem, not this component's.

**Tests that must land with the code** (per `AGENTS.md`: an invariant needs a
test or the sentence is downgraded):

- `test_supervisor_input_carries_no_privileged_field` — the input type's field
  names are asserted against an **exact allowlist**, so *adding* a field is what
  fails the test, not merely adding one of today's forbidden names. A denylist
  of `{gold_patch, reference_patch, test_patch, hidden_tests, fail_to_pass,
  pass_to_pass, fix_commit}` catches the names we thought of; an allowlist
  catches the one we did not.
- `test_the_task_statement_reaches_the_supervisor` — the goal gets through.
- `test_the_supervisors_own_words_never_come_back_as_evidence` — its own
  correction is **memory, not observation**. Admitted as evidence, the
  supervisor would be reading its own output as something the actor did.
- `test_later_external_user_text_is_excluded` — the brief is the first user
  message; a later interjection is not the actor's doing.
- `test_every_event_is_dispositioned_in_the_record` — the log says *why* a
  message was not judged rather than omitting it, so a reader can tell an
  exclusion from a gap.

**The supervisor's own words live in its memory, on a channel separate from its
evidence.** Since a correction never returns as an observation, a policy has
nothing to compare against unless the supervisor keeps a list of what it has
already said — and would otherwise be free to say the same thing three times in
a row. That list is handed to the policy beside the evidence, never mixed into
it (`test_what_it_said_is_remembered_outside_the_evidence`).

## 4. When it speaks: a policy seam, and exactly two implementations

```
SpeakPolicy:  consider(observation) -> Intervention | None
```

The observation is §3's evidence at the current cursor plus the supervisor's own
run state (what it has already said, and where). **The policy decides whether
and when; it does not decide the channel, does not write, and cannot end the
run.** Returning `None` is the ordinary case and is not an error.

**Two implementations ship, and the second one is not padding:**

1. **The real policy** — one, whichever the rig's first arm needs.
2. **`NeverSpeak`** — because the control arm must run *the same supervisor*
   with speech disabled, not *no supervisor*. If the control arm simply omits
   the component, the arms differ by the judge calls, their latency and their
   cost as well as by the corrections, and the comparison stops being paired.

Beyond those two, **no policy library.** The repo's rule is that the third
repetition earns the abstraction; the seam here is earned by the measured fact
in §2, not by anticipated variety.

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

- **The in-sandbox fold check** — [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
  refutation condition, carried here so that it is scheduled rather than filed
  against nobody. The supervisor delivers **one** intervention *inside the
  sandbox*, and the fold's measured shape — the injected block's **length** and
  **`sha256`** — must match the host measurement (`len 440`,
  `sha256 3ba88726…fb90c8`, pinned by `tests/test_streamjson_input_evidence.py`).
  **A mismatch means this task is not complete** and opens the ADR's refutation
  path: the byte-identity result would then be about the host binary and not the
  one we ship. This is on the critical path either way — the rollouts that will
  use this channel run in containers, and every measurement of the channel so
  far is host-side.
- The six named tests above exist and fail when their invariant is violated.
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
- **Not a judge-quality study.** Whether the guidebook judge is any good is
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
(this PR) for the attribution; [task 04](task-04-oracle-analysis-task.md) for the
guidebook it judges against. **Not** blocked on task 16, by §1's seam.
**Scope:** M.
