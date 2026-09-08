# ADR-0024: The judge is not told what the supervisor said

## Status

Accepted. Issue [#381](https://github.com/Luolc/swe-lab/issues/381) was put
to a three-party debate on 2026-09-06 (one advocate per option, one judge;
every reading below was recomputed by the debate's judge from the frozen
records, and again for this record); the owner ruled the same day to adopt the
debate judge's verdict as written. The implementation, the trace-synthesis
spec reconciliation and the task-05 reconciliation land with this record.

[ADR-0020](ADR-0020-running-state-is-a-required-judge-output.md) is unchanged:
its writer input list — previous state, selected segment, guidebook, task
**and prior delivered interventions** — stands, and this record settles the
judge side that ADR-0020 did not enumerate.
[ADR-0018](ADR-0018-the-supervisor-reads-the-guidebook-but-must-not-recite-the-answer.md)'s
input and speech boundaries are untouched.

## Amendment (2026-09-08)

**The native runtime is gone**, by the owner's ruling recorded in
[ADR-0025](ADR-0025-the-segment-loop-is-the-only-supervised-carrier.md). The
decision this record makes is unaffected — it is about the Python judge and
writer prompts, which both surviving carriers share — but two of its sentences
now describe code that does not exist, and are amended rather than rewritten in
place:

- The rejected alternative *"Fix it only in the native runtime"* is settled by
  the removal as well as by the ruling it already cited. Its factual clause —
  *"the native runtime carries one hard-coded shape"* — was true of a runtime
  the repository no longer has.
- **Follow-up 2, "A config key for the native runtime", is withdrawn, not
  outstanding.** There is no `prompt.rs` to add a key to, and no native arm to
  join the A/B. Follow-ups 1 and 3 are untouched: the paid re-measurement and
  the downstream pre-registration are about the Python prompts and stand
  exactly as written.
- The last rejected alternative observes that *"the native judge prompt omits
  the section, and the Python judge prompt now matches it in that respect"*.
  The Python prompt's shape is unchanged; what is gone is the second prompt it
  was compared against, so the sentence should now be read as history — the
  reason the shape was chosen, not a live pair of prompts to reconcile.

## Amendment (2026-09-08, second)

**The correction channel is gone too**, by the same owner's ruling, recorded in
[ADR-0026](ADR-0026-the-correction-channel-is-removed.md). One carrier remains,
and again the decision is unaffected — it is about the Python judge and writer
prompts, which the segment loop runs unchanged. Two more sentences describe code
that does not exist:

- *"`channel.supervision()` forwards the argument, so both Python carriers
  (`SupervisedRun` and `SegmentedSupervision.policy_factory`) run through it"* —
  there is one Python carrier now, and `SegmentedSupervision.policy_factory` is
  it. `supervising_policy` still owns the forwarding, which is the load-bearing
  half; what is gone is the second caller.
- The A/B arms this record kept (`both`, `none`) are still reachable and still
  named per definition, but **no shipped definition names anything but
  `writer`** now that the second and third arms' home — the channel's two
  workflow definitions — is gone. That was already true of the value; it is now
  true of the count of definitions that could differ.

**Follow-up 1 is affected and is not withdrawn.** The paid re-measurement it
asks for was to run through
`experiments/trace_synthesis/n_batching_replay/replay.py`, which ADR-0026
deletes, so it is **no longer runnable as written**. The question it asks — does
showing the judge what the supervisor said confirm it into agreement — is
unchanged and unanswered. Said on [#381](https://github.com/Luolc/swe-lab/issues/381),
which stays open.

## Date

2026-09-06 (amended 2026-09-08)

## Context

### One section, two readers

`Observation.said` is the supervisor's memory: the corrections it has already
delivered in this run. Its own words never come back as evidence — the
evidence filter excludes them by origin (`EXCLUDED_OWN_INTERVENTION`,
`test_the_supervisors_own_words_never_come_back_as_evidence`) — so `said` is
the only place a policy can see what it has said. The default prompt builder
rendered it to **both** model calls as `# Prior supervisor interventions`,
`(nothing yet)` until the first correction.

### The measurement

Issue #381 replayed the shipped supervisor over the recorded first end-to-end
event stream: no rollouts, the actor's trajectory a fixed recording, identical
boundaries, evidence, judge model and criterion. Two arms, `budget=3` and
`budget=0`. `SpeakWhenOffTrack` appends the would-have-spoken marker and then
returns on the budget before any writer call, so the **only** difference
between the arms' judge requests is what that one section contains.

Every number below is recomputed for this record from the committed
`experiments/trace_synthesis/n_batching_replay/runs/{replicate,replicate_budget0}/{a,b}/judgments.jsonl`
at `c84f29f` (rows with `off_track` present are *answered*; the treatment's
first correction is at cursor 7 in both passes; *after* means cursor > 7):

| Reading | `replicate` a / b (`budget=3`) | `replicate_budget0` a / b (`budget=0`) | Source |
| --- | --- | --- | --- |
| answered, `off_track` | 141 → 105, 139 → 114 | 166 → 3, 164 → 3 | #381 body; control completed in its first comment |
| `off_track` after the first correction | **104/134**, **113/132** | 2/159, 2/157 (cursors 8 and 9; #381: `[7, 8, 9]` in both passes and nowhere else) | #381 body |
| `off_track` after the budget was spent (cursor > 15) | 101/126, 106/124 | — | this record |
| byte-identical judge prompts, `off_track` agreement | — | **320/320** across four pairings | #381 first comment; `REPORT.md` §4 |
| same cursor after the first correction, `said` the only prompt difference: agreement | **30/131**, **21/126** | | this record; debate judge |
| same cursor, treatment `True` where control `False` | **100/131**, **105/126** | | this record; debate judge (132 / 127 with cursor 7 counted) |
| prompt length, treatment minus control, at shared cursors | {153, 348, 477}, {145, 281, 409} | | this record |
| the three corrections' lengths, cumulative | 166 / 360 / 488, 158 / 293 / 420 | | this record |
| `judge_reason` after the first correction matching `\bprompt\|\bnote\|\bnudg\|\btold\b` | **54/134**, **42/132** (all among the `off_track` rows: 54/104, 42/113) | **0/159**, **0/157** | this record; debate judge, three vocabularies agree |
| lapses (`max_tokens`, #383) | 29, 31 | 4, 6 | this record |

The prompt-length differences are exactly the section: the cumulative
correction text minus the 13-character `(nothing yet)` placeholder, plus the
join newlines. Nothing else in the request moved.

The reason texts show *how* the section moved the verdict. At cursor 7 of
`replicate/a` the writer said "Looks like you're jumping to edit models.py —
worth actually opening the current from_isbn implementation …"; at cursor 11
the judge's reason reads "The engineer moved to edit models.py without first
reading the current from_isbn implementation or isbn helper module". The
writer's hedged guess became the judge's stated fact. The criterion's ninth
line, "Judge only what the actor's own records show", was in every one of
these prompts and did not hold: the judge read the supervisor's words as the
actor's record. That is the same door the evidence filter closes on the
evidence path, left open on the prompt path.

Task 05 had put `said` in the observation "so the judgement can decline to
speak again". The record runs the other way: after its third correction the
judge never declined — 101/126 and 106/124 `off_track` with nothing left to
spend — and only the budget stopped it.

### What the measurement does not say

- **The magnitude is the old prompt's.** The replay ran at `39be140`
  (`manifest.json`): visible-text-only rendering (#380), no running state
  ([ADR-0020](ADR-0020-running-state-is-a-required-judge-output.md)), no
  guidebook, `max_tokens=512` (#383). The mechanism transfers to the current
  prompt; the numbers do not, and have to be re-measured there (follow-up 1).
- **The running state is a second self-feedback channel** that none of the
  three shapes below touches: the judge's own last state is fed back on every
  call with the instruction to retain failures. Under the default below there
  is no path from `said` into it — the state is produced by the judge from
  evidence and the previous state alone — but its own stickiness is unmeasured
  (follow-up 1, third arm).
- **The writer's half is unmeasured either way.** With `said` in its prompt the
  three corrections share no eight-word shingle and the debate's reading found
  three of six pairs anaphoric; without it nobody has a reading. What is known
  is the bound: a correction is at most 400 characters and the writer is
  called at most `budget` times after the budget gate, so it can be shown at
  most `(budget − 1) × 400` characters of its own words.

### Where the shapes stood before this record

The native runtime already had the writer-only shape: `prompt.rs` builds
`judge_prompt()` without the section and `writer_prompt()` with it, pinned by
`the_judge_is_not_told_what_was_already_said_and_the_writer_is` (#393, merged
as `7d25e6b`). The Python carriers — the A′ channel and the segmented loop —
still rendered it to both, and `definitions.py` listed #381 as the last item
on which the two runtimes "deliberately diverge".

## Decision

### `said` is the writer's to read, and the judge's only by choice

`supervising_policy(...)` takes `said_visibility`, one of:

- `"writer"` — **the default.** The writer's prompt carries the section; the
  judge's does not, and is therefore the same bytes whether or not the
  supervisor has spoken.
- `"both"` — the shape before this record, kept as an A/B arm. Byte-identical
  to the pre-record prompt; it carries no added instruction about how to read
  the section (see the second alternative).
- `"none"` — neither prompt carries it: the A/B control, where any effect of
  `said` is zero by construction.

The switch reaches the two `SupervisorPromptBuilder` instances as
`include_said`: the judge's builder gets `said_visibility == "both"`, the
writer's `said_visibility in {"writer", "both"}`. Off, the section is
**absent**, not empty — as in the native judge prompt — so nothing in the
judge's request says a supervisor exists. `channel.supervision()` forwards the
argument, so both Python carriers (`SupervisedRun` and
`SegmentedSupervision.policy_factory`) run through it. A `prompt_builder=`
override is handed to both calls as-is and owns its own said visibility; the
recorded value then names the mode the default builders would have run under,
the way `guidebook_context_mode` names what the default prompt consumes.

### Not an environment variable; one name, one arm per definition

`SUPERVISOR_SAID_VISIBILITY = "writer"` is named once in
`workflow/definitions.py` beside `SUPERVISOR_BUDGET`, and every shipped
supervised definition passes it. A setting the record cannot show is not an
arm, and a paired pair cannot pin one value each through a process variable.
An A/B arm is a readable `WorkflowDef` that states its own value in its own
`supervision(...)` call, exactly as `CONTROL_ROLLOUT` states its budget; only
the default arm ships with this record.

### Three additive fields on the decision row

Both carriers (`Supervisor._row`, `SegmentedRun._decision_row`) write two of
the fields on **every** decision row, and the third on every row **behind
which a judge request was built**:

- `said_visibility` — on every row: the mode the policy ran under (`null` for
  a policy that makes no model call);
- `said_count` — on every row: how many corrections had been delivered before
  that boundary, the length of the observation's `said`, so a `spoke` row
  does not count its own. It is the same quantity under all three modes and
  on rows written before this record; how many the judge was actually shown
  follows from `said_visibility` on the same row — `said_count` under
  `"both"`, zero under `"writer"` and `"none"`;
- `judge_prompt_sha256` — beside `judge_input`, on every row behind which a
  judge request was built: the digest of the judge's user prompt, so rows
  judged on the same bytes can be paired across arms without re-reading the
  prompt. The request is built before the transport is called, so that is
  three kinds of row: one with a valid verdict; a **lapse** whose request
  came back unusable; and a **lapse** whose transport raised and nothing
  answered. In both lapse cases the judge carries the request on its own
  error (`JudgeAnswerError`, `JudgeTransportError`) and the row records
  `judge_input` and the digest all the same — the rows where pairing is most
  needed are the ones where the judge failed (29 and 31 of the treatment's
  boundaries on the frozen record). A writer lapse keeps the valid verdict's
  fields, request included. **Two kinds of row have no request behind them
  and carry neither field**: a row written for a policy that makes no model
  call (`NeverSpeak`, `SpeakAt`), and an `unjudged` row, where the evidence
  window was empty and the judge was not consulted. A consumer reads the
  absence of the two fields on those rows as "no request", not as an older
  or broken row.

This is the same mechanism as #431's verdict telemetry, ADR-0020's
`running_state` and ADR-0021's `guidebook_context_mode`: an additive field on
the open supervisor diagnostic row. **Not a report-contract change** — no
terminal summary, run record, metric or artifact set moves. Rows written
before this record carry the full `judge_input` and can be classified after
the fact by whether their section reads `(nothing yet)`.

### The invariant has a two-arm test, at zero cost

`test_the_judge_prompt_does_not_depend_on_what_was_said` replays one stream
through a speaking arm and a silent arm and asserts the judge prompts are
identical, boundary by boundary, under `"writer"` and `"none"` — and
**different** under `"both"`, the positive arm without which sameness proves
nothing. `test_the_writer_prompt_carries_what_was_said_unless_told_not_to`
is the writer's half. Both were run against the mutant that renders the
section unconditionally and against the one that never renders it; each
mutant turns the pair red.

The routing above the policy has its own tests, each asked for a
non-default mode so that a seam which drops the argument and builds the
default is told apart from one that forwards it:
`test_the_channel_factory_forwards_a_non_default_said_visibility` through
`channel.supervision()`,
`test_the_shipped_segmented_factory_reads_the_named_said_visibility` through
the shipped segmented definition's factory with the constant patched, and
`test_nothing_in_building_a_supervision_reads_the_environment`, which builds
the shipped arms and a channel factory with `os.environ` replaced by an
object that fails on any read. The two A′ definitions captured the constant
at import, so `test_the_shipped_channel_arms_carry_the_named_said_visibility`
pins only that they agree with it and with each other. The request-bearing
lapse rows are pinned per carrier:
`test_a_judge_lapse_row_still_carries_the_request_and_its_digest` /
`test_a_segmented_judge_lapse_row_still_carries_the_request_and_digest` (an
unusable answer), `test_a_lapse_whose_transport_raised_still_carries_the_request`
(the transport raised; `test_a_transport_failure_carries_the_request_it_was_sent`
pins the judge's side of it) and
`test_a_writer_lapse_row_keeps_the_valid_verdicts_request` /
`test_a_segmented_writer_lapse_row_keeps_the_valid_verdicts_request` (a
writer lapse after a valid verdict). The two no-request rows are pinned per
carrier as carrying `said_visibility` and `said_count` and **neither**
request field: `test_decision_rows_record_said_visibility_count_and_prompt_digest`
(the no-model row) and `test_a_boundary_with_no_evidence_is_recorded_as_unjudged_not_silent`
(the `unjudged` row) on the channel carrier, and
`test_segmented_rows_without_a_request_carry_neither_request_field` for both
rows on the segmented one.

## Alternatives Considered

### Default to `"none"`

Rejected as the default, kept as the control arm. Its argument — that `said`
was never shown to prevent repetition, and that how often the supervisor
speaks is fixed by budget and cooldown, which read no text — is correct and
recorded above. But ADR-0020 lists prior delivered interventions as the
writer's input, the writer's reading of them is what task 05 wrote it in for,
and no measurement touches the writer's side in either direction. Where there
is no measurement, the accepted spec decides the default and a measurement
decides whether to overturn it.

### Default to `"both"` with an instruction on how to weigh the section

Rejected as the default, kept as an arm. There is no measurement that a
sentence of instruction moves the behaviour measured above, and there is a
same-shaped counterexample in the record: "Judge only against the criterion
given below. Do not use any other standard" was in the judge's system prompt
on the day of the measurement, and the criterion's own "Judge only what the
actor's own records show" in its user prompt, and neither held. The arm is
kept because its advocate's structural point is real — the actor's rebuttal
to a correction can appear in the window beside the correction it answers,
so a judge that cannot see the correction cannot see what is being rebutted —
and unmeasured.

### Fix it only in the native runtime

The position #381's thread took: the Python host runtime was slated for
removal and the binary already had the right shape. Rejected by the owner's
ruling. The Python carriers are what downstream runs today, the A/B needs all
three shapes in one codebase, and the native runtime carries one hard-coded
shape (follow-up 2).

### Read the mode from the environment

Rejected. Unanimous among the three advocates: an arm whose setting the
record cannot show is not an arm.

### Render an empty section rather than none

Rejected. An empty `# Prior supervisor interventions` still tells the judge a
supervisor is present and can speak; the native judge prompt omits the
section, and the Python judge prompt now matches it in that respect.

## Consequences

- The default judge user prompt loses one section; its system instructions
  and their byte pins are unchanged. `"both"` reproduces the pre-record prompt
  byte for byte.
- The judge side of the paired arms is matched again: `SUPERVISED_ROLLOUT`
  and `CONTROL_ROLLOUT` now send the same judge bytes at every shared boundary,
  which is what makes their `markers` comparable (#381's first invalidation).
- `definitions.py` no longer lists #381 as a place the runtimes diverge.
- The frozen `n_batching_replay` records are untouched. Its driver,
  `replay.py`, now reads the tool-use answer `ModelJudge` records as well as
  the text answer the frozen runs recorded; before that fix every row of a new
  arm would have come out with `off_track` empty.
- A downstream A/B has three named modes to assign per instance and the row
  fields to stratify by.

## Follow-ups (named, not started here)

1. **Re-measure on `HEAD`, paid, before any downstream A/B.** Replay the same
   recording with `said_visibility="writer"`, `budget=3` against `budget=0`.
   The acceptance reading is the post-correction `off_track` disagreement at
   shared cursors, which must be at or below the floor measured between the
   two `budget=0` passes on `HEAD` — the old 320/320 does not transfer once
   the running state is sampled text — and the own-prompt vocabulary count
   above, which must be 0 as the control's is. Add a third arm, owner-run,
   with `budget=0` and the running state frozen at `INITIAL_RUNNING_STATE`,
   reading `off_track` agreement and P(off_track_k | off_track_k−1) against
   the un-frozen `budget=0` arm: the state channel's own stickiness, shared by
   all three modes. Prerequisite: the `replay.py` answer-shape fix in this
   record.
2. ~~**A config key for the native runtime.** `prompt.rs` is hard-coded to the
   default shape; the three modes need a key there before a native arm can
   join the A/B.~~ **Withdrawn 2026-09-08** — the native runtime was removed
   ([ADR-0025](ADR-0025-the-segment-loop-is-the-only-supervised-carrier.md)),
   so there is no key to add and no arm to add it for. Kept struck through
   rather than deleted: a follow-up that silently disappears is
   indistinguishable from one nobody did.
3. **The downstream A/B's pre-registration** is proposed, not frozen: per
   instance, one of the three modes, each paired with its control; primary
   statistic `Δ_m = solve(treatment_m) − solve(control_m)`; `"both"` replaces
   `"writer"` only on `mean(Δ_both − Δ_writer) ≥ 5 pp` with a one-sided paired
   permutation test at `p < 0.05`, behind a gate that the treatment's
   post-correction `off_track` rate exceed the control's same-segment rate by
   at most 10 pp at the instance median (the old prompt's reading was about
   76 pp) — a solve difference on an arm that fails the gate is "happened to
   help", not "judged correctly". The numbers are the debate judge's proposal
   for the owner to set before the runs, and not to move after them.
