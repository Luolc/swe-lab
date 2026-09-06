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

## Date

2026-09-06

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

Every decision row of both carriers (`Supervisor._row`,
`SegmentedRun._decision_row`) carries:

- `said_visibility` — the mode the policy ran under (`null` for a policy that
  makes no model call);
- `said_count` — how many corrections the judge was shown at that boundary,
  read off the observation, so a `spoke` row counts the ones before its own;
- `judge_prompt_sha256` — the digest of the judge's user prompt, from the
  `judge_input` the row already carries, so rows judged on the same bytes can
  be paired across arms without re-reading the prompt.

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
2. **A config key for the native runtime.** `prompt.rs` is hard-coded to the
   default shape; the three modes need a key there before a native arm can
   join the A/B.
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
