# First e2e supervised rollout — report

**The shape of this file — which slots exist and what each one must say — is
reviewed on its own in [#359](https://github.com/luolc/swe-lab/pull/359); this
PR fills the values from the run's record.** Two PRs so that the specification
and the readings are judged separately: a filled slot answering a question the
specification never asked is a diff against it.

**The criteria are not here.** They are frozen in
[`PREREGISTRATION.md`](PREREGISTRATION.md) and are linked, never copied — a
second copy of a closure criterion is a criterion that can drift from the
frozen one without anything failing. When a row below says "closes when",
read it there.

## 0. Coordinates

Every reading below was taken from this run's own record, not from anyone's
account of it. Where a number is derived rather than read, the derivation is
shown.

| | |
|---|---|
| Run | `run_ts = 20260902-072316`, backend `host`, one attempt (`a0`) |
| Instance | `instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb` |
| Record root, as read | `.cache/runs/supervised_rollout_and_unit_test/<instance>/r0/store/adhoc/<instance>/r0/` in the main checkout — `rollout/a0/` below |
| Record root, to check against later | `~/corpora/swe-lab/first-e2e-2026-09-02/r0/` — 122 files, the same run kept off the checkout. The `.cache` copy is keyed by instance, so a rerun overwrites it, and a worktree removal deletes it silently; the corpus copy is what these citations remain checkable against. Machine-local, not uploaded (owner ruling, 2026-09-02). |
| Actor | Claude Code **2.1.212** — `claude.info`'s verbatim `/opt/claude-code/claude --version`, matching `PINNED_CLAUDE_CODE_VERSION` (`src/swe_lab/harnesses/claude_code/binary.py:45`) |
| Actor model | `claude-sonnet-5` (`run.json` → `extra.agent_model`) |
| Supervisor model | `SUPERVISOR_MODEL` (`src/swe_lab/workflow/definitions.py`), one judge call and one writer call per intervention |
| Read at | 2026-09-02, after the run ended and the container was gone; every file below is final, not growing |

## 1. The seven points

One row per point of
[task 01's acceptance table](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end),
judged against [`PREREGISTRATION.md` §4](PREREGISTRATION.md#4-per-point-closure--file-field-judgment).
Three slots each, and none of them may be merged:

- **Evidence from this run** — the artifact and the field, with the value.
  Not "the record shows it"; the file, the key, the number.
- **Verdict** — one of `closed` / `not closed` / `left open`. Three distinct
  values, and `left open` is not a soft `not closed`; which one a given
  record earns is fixed by §4, not here.
- **Assertion relied on** — `A` (the actor's own native session transcript),
  `B` (`proxy_log.jsonl`'s wire capture), or `none`. Which points require an
  assertion, and how the two rank, is fixed by §4 and
  [§5](PREREGISTRATION.md#5-evidence-for-points-1-3-and-4--two-assertions-not-one-on-two-different-bases),
  not here.

| # | Claim | Evidence from this run | Verdict | Assertion |
|---|---|---|---|---|
| 1 | Supervisor attached to the actor's **live** stream | `supervisor.jsonl` has 170 rows, first `at 07:23:35.650499+00:00` (cursor 1), last `at 07:42:14.572388+00:00` (cursor 170); `metrics["supervision.boundaries"] = 170`. Counted independently off the actor's own `claude_code.event_stream.jsonl`: **170 events** — two files, two write paths, same number. Corroborated by `claude_code.proxy_log.jsonl`: 33 real API exchanges, so the stream being watched was a working actor's, not a synthetic one. And Assertion A carries the half the pipeline cannot vouch for (see point 3). | **closed** | A (+ proxy corroboration) |
| 2a | Barrier holds: no gold patch, no hidden tests in the supervisor's input | Consumed as-is per §4, not re-verified here; `test_supervisor_input_carries_no_privileged_field` is where it lives. This run adds a *different* fact, not a substitute: the three delivered corrections carry no answer either (§1a). | consumed, **not re-verified** | none |
| 2b | Criterion sha verified, mismatch refuses **the run** | Closed by the suite, not by this run: `test_a_forged_criterion_stops_the_run_before_a_sandbox_exists` (`tests/test_rollout.py`) and `test_the_shipped_supervised_arm_carries_the_pinned_criterion` (`tests/test_workflow_registry.py`), both on `main`. A run against a *correct* criterion says nothing about a forged one. | **closed** by the suite | none |
| 3 | Policy speaks at least once **because of a real deviation** | Three `kind: "spoke"` rows, cursors **4 / 8 / 12**, `at` 07:23:47.541 / 07:24:04.404 / 07:24:23.080, `policy: "speak-when-off-track"` on all 170 rows (never `speak-at`); `metrics["supervision.corrections"] = 3`. **Assertion A:** all three texts appear in the actor's own native session transcript — `claude_code.native_transcript.tar.gz` → `projects/-app/f4ddae90-a7d2-440a-9e56-36e8a90c08ce.jsonl`, 122 lines, `supervisor_note` on lines **32 / 51 / 57** (1-indexed), `type: attachment`. That file is written by Claude Code for its own resume, by nothing of ours. **That is all of what §4 asks, and §4 asks nothing about the deviation** — its test is a `spoke` row whose policy is not `speak-at`, plus Assertion A for delivery. On the half §4 leaves untested this run's record runs the other way, three times out of three: **§1b**. | **closed** on §4's test, which does not reach *"because of a real deviation"* — §1b | A |
| 4 | Correction arrives **mid-turn**, matching the measured wire shape | `claude_code.proxy_log.jsonl` (3,064,215 bytes, final): 33 requests carry a `messages` array, **24** carry the block, **63** occurrences of `<supervisor_note>` in total, of which **3 sit in the last message** — one per intervention — and **0 appear in any response**. The carrying message is `role: "system"` with one `type: "text"` block wrapping `<system-reminder>The user sent a new message while you were working: <supervisor_note>…</supervisor_note>…</system-reminder>`. **Structural agreement** with `experiments/trace_synthesis/sandbox_fold_check/` — role, wrapper and position — and deliberately **not** byte-identity: that measurement is about *its own probe text*, and this run's injected text is different, which is the whole of why it is not borrowable. | **closed on B**, with the trust assumption below | B |
| 5 | Rollout completes, patch taken **against the pre-agent baseline**, grading runs | `run.json` → `extra.patch_base_ref = 64501d9b938bd7986b36dd2cd4fdb7af930b2750` (ADR-0014); `metrics["patch_is_empty"] = 0.0`, `patch.diff` 3,107 bytes. Grading ran: the `unit_test` entry's `metrics["unit_test.resolved"]` is **present** (`0.0`), which is what §4 makes the criterion — presence, not value. | **closed** | none |
| 6 | Trace persisted, **interjection in it**, provenance complete | `conversation.json`: 73 messages, of which **msg[19], msg[29], msg[32]** are `role: "system"` and carry `supervisor_note` — the interjections survived conversion. Provenance: `run.json` carries `run_ts`, `backend`, `instance_id`, `sweep_id`, `tier`, `rollout_id`, `attempt`, `status`, and `extra["agent_model"] = "claude-sonnet-5"`. (The top-level `model` field is empty **by design** — `src/swe_lab/sandbox/persist.py:66-71`: nothing in-tree sets it, and a rollout records its actor in `extra`. Not a provenance gap; written down so the next reader does not re-derive it.) | **closed** | none |
| 7 | The **outcome word is correct** | `run.json` → `extra.rollout_outcome = "patch_produced"`, recorded verbatim. Judged against `RolloutOutcome`'s members read fresh from `src/swe_lab/rollout.py` — `OOM_KILLED`, `SYSTEM_FAILED`, `TIMED_OUT`, `NO_PATCH`, `PATCH_PRODUCED`, `UNCLASSIFIED`, `SUPERVISION_FAILED` — against what happened: the agent finished (`agent_complete 1.0`, `claude_code.exit_code 0.0`, `claude_code.timed_out 0.0`), the patch is non-empty (`patch_is_empty 0.0`), no OOM (`sandbox.oom_kills 0.0`), and supervision was never lost (no `supervision.unhealthy` key; see §3). | **closed** | none |

**The trust assumption point 4 rests on, stated rather than implied.**
`proxy_log.jsonl` is written by `cc-reverse-proxy`, which is this project's
own code. Assertion B cannot rule out that recorder fabricating or corrupting
what it records; checking it against itself would be circular. Point 3 is
stronger because Assertion A crosses a boundary this project does not
control. Six of the seven rows closing does not make point 4 as hard as the
others, and this paragraph exists so a later reader does not read it that
way.

### 1a. The corrections themselves

**The three delivered corrections, verbatim** (`supervisor.jsonl`, the
`spoke` rows; identical bytes reached the actor — see point 3):

1. *Notice you haven't yet opened models.py to see how from_isbn currently
   branches on identifier type before sketching the new helpers.*
2. *Odd that no output shows a read of the current from_isbn/canonical-ISBN
   logic yet — worth confirming what's actually there before locking in the
   helper contracts.*
3. *Still haven't seen a read of the current from_isbn body land in the
   transcript — worth checking what's actually there before the helper
   signatures get finalized.*

**Did any of them leak the answer?** No. None contains gold-patch content, a
test name, a file line to change, or a solution sketch. All three are
procedural — they say *you are designing before you have read what is there*.
They are quoted in full here because that judgment has no other evidence:
paraphrasing them would ask the reader to take it on trust.

**Procedural is not the same as apt, and only the first of those is settled
here.** These read like what someone watching over a shoulder says, and a
shoulder-watcher can see the screen. Each of these was judged against a prefix
the actor had already left behind, and all three were false of the actor by the
time they arrived — §1b, correction by correction. The paragraph above is about
their *content*; it says nothing about their *truth*, and nothing in it is
weakened by §1b: a false statement can be as free of the answer as a true one.

**One thing the texts do show, and it is not a leak:** all three say the same
thing three times. See §7.

**The delivery's morphology**, counted row by row over the final
`claude_code.proxy_log.jsonl` (3,064,215 bytes, read after the run ended, so
the file is not growing under the count):

- **As the last message: 3.** One per intervention. A run property.
- **Carried in history: the rest.** 24 requests carry the block at all, 63
  occurrences in total, and the per-request count climbs 1 → 2 → 3 and then
  holds at 3. The two counts close against each other: 3 in a last message
  plus 60 carried in history is the 63. **Reported as a shape, not a number:** once injected, a
  correction stays in the actor's context and travels with every subsequent
  request; the total is a property of how many requests followed, not of the
  delivery.
- **In any response: 0.** The load-bearing count. The block appears only in
  what the actor *sent*, never in what came back — so it is the actor's
  context, not our own narration folded into the capture.

### 1b. Was it a real deviation? Three checks, three noes

Point 3's claim has two halves and its closure test has one. The claim is
*"policy speaks at least once **because of a real deviation**"*; what
[§4](PREREGISTRATION.md#4-per-point-closure--file-field-judgment) closes on is a
`spoke` row whose `policy` is not `speak-at`, plus Assertion A for delivery.
Nothing in that test can fail when the deviation is not real, and the same is
true of the acceptance table the claim is copied from, which offers
`SpeakAt` as the thing being excluded
([`plans/README.md`](../../../docs/trace-synthesis/plans/README.md#task-01-one-instance-end-to-end):
*"a run whose only utterances are scheduled does not satisfy this point"*).

**So the gap is not that the report over-read its evidence — it is that the
criterion was written narrower than the claim it is labelled with, in both
places, before the run.** This section is what the run says about the half no
criterion covers. It cannot move the verdict — §4 is frozen
([§9](PREREGISTRATION.md#9-what-may-still-change)), and a criterion rewritten
after seeing the result is the thing a pre-registration exists to prevent, in
whichever direction it is rewritten.

**The three corrections, each against the evidence that produced it and against
the actor it reached.** Every reading is in `WITNESS.md`'s block; the function
they all turn on is at `models.py:377`, taken from the actor's own `grep`
output (event 7, 07:23:34.719Z) rather than written into the witness.

| # | judged at | evidence the judge held | had the actor read it? | reached the actor | the actor's answer |
|---|---|---|---|---|---|
| 1 | cursor 4 | **0 records — empty** | not in the window | 07:23:47.615Z, **9.5 s** after event 13 | line 34, 07:23:53.867Z |
| 2 | cursor 8 | 3 records | not in the window | 07:24:04.503Z, **26.4 s** after event 13 | line 61, 07:24:42.592Z |
| 3 | cursor 12 | 6 records | not in the window | 07:24:23.162Z, **45.1 s** after event 13 | line 61, 07:24:42.592Z |

**The column is receipt, not the supervisor's write.** `supervisor.jsonl`
records when a correction was written — 07:23:47.541, 07:24:04.404,
07:24:23.080 — and the actor's own transcript records when it arrived,
**73 / 99 / 82 ms** later.
The question this section asks is what the actor had already done when the note
reached it, so the receipt is what the table carries; the two are named apart
because a column headed *delivered* holding the write time is how they get
merged.

**Event 13 (07:23:38.071Z) is the actor's first `Read` of `models.py` whose
window covers line 377** — `offset 370, limit 60`, printed by the witness rather
than asserted here. It is *not* in the evidence any of the three judgements
held: it is event 13, and the three judged at cursors 4, 8 and 12. Correction
2's other half is the same story with a second file: it says no read of the
*canonical-ISBN* logic has landed, and the actor read `utils/isbn.py` at event
49, 07:24:01.701Z — **2.8 s before that correction reached it** (2.7 s before
it was written).

**Two different failures, and merging them would lose the one that matters.**
Corrections 2 and 3 are sound judgements on a stale prefix: what they assert was
true of everything in front of the judge, and false of the actor. Correction 1
is not that. Its observation carried **no evidence at all** — every one of the
first four events was excluded by the filter, `Supervisor.observe` appends only
admitted records (`supervisor.py:592-594`), and `SpeakWhenOffTrack.consider`
calls the judge regardless (`supervisor.py:468`). So the first thing this
pipeline ever said to an actor was decided on an empty input, and the specific
claim in it — that `models.py` had not been opened — could only have come from
the task text. *(Those two line numbers are read from the checkout, not from
the run's record. The file's last change is `3e97442`, which `WITNESS.md`
records as the repository state at run time, so the lines that ran are the lines
in the file today.)*

**That failure was named in this project before this run produced it.**
[`mid_turn_compliance/REPORT.md` §5.1](../mid_turn_compliance/REPORT.md#51-one-shape-three-times)
carries it as a playbook entry:

> **A model used as an instrument gets a closed vocabulary.** Anything outside
> it is `UNPARSEABLE`, never interpreted, and **it is never handed an empty
> input** — a normal-looking output becomes a false finding under a permissive
> reader, and it will be false in the direction you were hoping.

Correction 1 is that entry, item for item: an empty input, an ordinary-looking
verdict, and a finding false in the direction the judge was looking for one.
Recorded here as a recurrence of a failure mode this repository had already
named, which is a different and worse thing than a first sighting.

**The actor answered, and this fills a slot the pre-registration left open.**
[§5](PREREGISTRATION.md#5-evidence-for-points-1-3-and-4--two-assertions-not-one-on-two-different-bases)
reserved one — *"a third thing, stronger than either assertion if it happens:
the actor's subsequent behavior visibly changing in response to the correction …
Recorded alongside either branch above, if observed, but never what closes any
of the three points."* It was observed, and it points the other way: the actor's
own transcript answers the notes rather than acting on them.

> line 34 — *I've already read the current `from_isbn` implementation
> (models.py:377-446) — it does a crude `isbn.startswith("B")` check for ASIN
> and has confused branching logic…*
>
> line 61 — *I already reviewed the current `from_isbn` body at the start
> (models.py:377-446) — confirmed its bugs.*

Two texts, not three: line 61 is the next assistant text after **both** the
second and third notes, because nothing the actor said between them carried
text. Both are in the same file Assertion A rests on — written by Claude Code
for its own resume, by nothing of ours — so the rebuttal has the same standing
as the delivery it rebuts. §5's slot is filled with a **negative** observation,
which is exactly what a slot reserved before the run is for.

**What this changes, and what it does not.**

- **The verdict stays what §4 assigns it: `closed`.** All of §4's conditions
  were met and none of them is about the deviation. Reading this section as
  "point 3 failed" would be substituting a criterion nobody registered.
- **The claim it is labelled with is not established, and this run argues
  against it.** The row now says both, because saying only the first is the
  overstatement this section exists to remove.
- **The pre-registration's vocabulary has no word for this.** `closed` /
  `not closed` / `left open` are verdicts on *criteria*; the finding here is
  that a criterion is narrower than its claim, which is a property of the pair
  and not of either. Recorded as the gap it is rather than forced into one of
  the three.
- **Nothing here touches [§7a](#7a-the-supervisor-runs-an-order-of-magnitude-slower-than-the-actor).**
  §7a already reports that the three were judged early and delivered late; what
  it does not say is that they were *wrong*, and it is not amended here — this
  section is where a reading that bears on a closure judgement belongs, since
  §7 is barred from closing, weakening or strengthening any of the seven
  points.

## 2. The three readouts

[`PREREGISTRATION.md` §6](PREREGISTRATION.md#6-readouts-required-alongside-the-seven-points)
requires these as **three lines, never merged into fewer**.

1. **Actor-side cost.** `total_cost_usd = 1.3311234` on the terminal `result`
   event of `claude_code.event_stream.jsonl`, with
   `usage = {input 64, cache_creation 79,716, cache_read 2,049,248, output
   15,438}` and `num_turns = 32`. **One run is one point, not a range**: the
   two prior points in
   [`downstream-scale-note.md`](../../../docs/trace-synthesis/downstream-scale-note.md)
   are both higher than this one — read them there, not here; two dollar
   figures copied into this file are two more numbers that can drift from the
   document that owns them. Three points do not make a distribution; the
   spread is still what a reader should carry.
2. **Supervision-side call counts — counts, not a rate.**
   `metrics["supervision.boundaries"] = 170`,
   `metrics["supervision.corrections"] = 3`,
   `metrics["supervision.lapses"] = 16`. Read together, never alone:
   `corrections == 0` would have had two possible sources, and only
   `boundaries` separates them ([`RUNBOOK.md` §4](RUNBOOK.md#4-reading-the-result)).
   Here `boundaries = 170` establishes that judgement happened, so
   `corrections = 3` is "spoke three times", not "was never asked".
3. **Supervision-side tokens and dollars — absent, and the absence is the
   readout.** Not implemented: `ModelJudge`/`ModelWriter` keep
   `requested_model`, `response_model`, `sampling_sent` and `raw` per call and
   discard the provider's `usage` block, so nothing durable ever sees it
   (§6.3). **What the absence means:** line 2's counts **cannot** be
   multiplied into a dollar figure, because each judge call's context grows
   with `window` and the boundary index — token count is not constant across
   calls even within this one run. The 170 judge calls of this run cost
   something; this run cannot say what.

## 3. The outcome word

- **Recorded outcome:** `patch_produced` (`run.json` → `extra`), verbatim.
- **What actually happened, in one sentence:** the actor ran to completion
  under supervision, produced a non-empty patch against the pre-agent
  baseline, and the patch did not resolve the instance.
- **Do they match (point 7):** yes — see the point 7 row for the members it
  was judged against and the metric that excludes each competing word.

Two categories carry obligations of their own
([§7](PREREGISTRATION.md#7-failure-classification)), and each keeps its slot
whether or not it fired:

- **`SUPERVISION_FAILED`** — **did not apply.** No `supervision.unhealthy`
  key is present, which is an event key rather than a zero, so its absence is
  the statement. Note what this means in the presence of the 16 lapses: a
  lapse is a bounded, recorded gap at a named boundary, not a loss of the
  supervisor ([#348](https://github.com/luolc/swe-lab/pull/348)), so the run
  keeps its evidentiary value **and** reports honestly that 16 boundaries went
  unjudged. That distinction was drawn before this run and was used by it.
- **`TIMED_OUT` on the first attempt** — **did not apply.**
  `metrics["claude_code.timed_out"] = 0.0`,
  `metrics["claude_code.exit_code"] = 0.0`. The presumption written into §7
  (a first-attempt timeout is ours until the proxy timeline says otherwise)
  was therefore never exercised. §7 of this report explains why that
  presumption is now *more* load-bearing than it was, not less.

## 4. What this run does not claim

[§8](PREREGISTRATION.md#8-what-this-run-deliberately-does-not-measure-or-claim)'s
four exclusions, each checked against this report's own text:

- **Not an effect estimate.** No sentence here says supervision helped or
  hurt. The instance was not resolved; **that is not evidence either way**,
  and no row above treats it as such. Checked.
- **Not the stability batch.** Nothing here sizes, schedules or pre-empts it.
  §7's throughput finding constrains how such a batch would have to be run,
  which is not the same as planning one. Checked.
- **Not a rate.** No `resolved N/M` or `Rate`-shaped number is computed. The
  unit-test figures below are this one run's counts. Checked.
- **Not a comparison to `control_rollout_and_unit_test`.** The control arm
  exists in the registry and did not run. Nothing here reads as if it had; in
  particular, the three corrections are not compared to anything. Checked.

**Grading, for completeness, not as a claim:** `unit_test.required = 25`,
`unit_test.passed = 9`, `unit_test.missing = 16`, `unit_test.resolved = 0.0`,
over three attempts (`a0`/`a1`/`a2`) with identical results each time. Per
§1 of the pre-registration this is a **complete result**, not a failure, and
it closes or fails none of the seven points.

## 5. Cost and hygiene

- **Wall clock:** `metrics["claude_code.wall_seconds"] = 1124.47`.
- **Supervision catch-up: at most 955.1 s, at most 84.9% of the rollout's
  wall clock (denominator = `claude_code.wall_seconds` = 1124.47 s).** In
  words, because a bare percentage hides which question it answers: *the
  actor spent at most 955.1 s of the run's 1124.47 s wall clock already
  finished, waiting for the supervisor to catch up.*

  Four quantities, each with what it actually is:

      1118.9 s   the supervisor's whole span      (measured)
    ≥  163.8 s   actor / supervisor overlap       (at least)
    ≤  955.1 s   the catch-up tail                (at most)
    ≤    84.9%   of the rollout's wall clock      (at most)

  **They are bounds, not point values, and the reason is one row of the
  event stream.** The last line carrying a `timestamp` is line 169, an
  `assistant` event at `2026-09-02T07:26:19.485Z`; the terminal `result`
  event carries none, and 90 of the stream's 170 lines carry one at all. So
  that timestamp is not when the actor finished — the actor finished at some
  unknown time *at or after* it. Subtracting it from `supervisor.jsonl`'s
  last row (`07:42:14.572388+00:00`) therefore gives the **largest** the
  catch-up can have been, and the overlap the **smallest**.

  A second, independent derivation agrees to 1.8 s: the wall clock minus the
  actor's own reported `duration_ms = 167591` gives ≤ 956.9 s, also an upper
  bound, since the wall clock includes setup the actor's duration does not.

  **The supervisor's 1118.9 s span is not a fourth reading of the same
  thing** — it is how long the supervisor worked, including the 163.8 s while
  the actor was still going. The same 955.1 s over *that* denominator is
  85.4%, which is a true statement about the supervisor rather than about
  what the run cost; this line uses the wall clock because the claim it
  supports is what that hour bought.

  Recorded on every run, not only when something looks wrong: a number first
  computed after an incident has no baseline to be read against.
- **Actor spend:** $1.3311234 (see §2 line 1).
- **Containers:** the rollout used one, the grading three (one per attempt);
  `docker ps -q` and `docker ps -aq` were both 0 after the run. The run held
  the machine's container window for its duration.

## 6. Artifacts

Under `<record root>/rollout/a0/` — in `.cache` as read, and under
`~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0/` for anyone checking
these numbers after the `.cache` copy is gone:

- Rollout record: `run.json` (and `../complete.json`, `outcome: succeeded`)
- `supervisor.jsonl` — 170 rows, 27,340 bytes
- `claude_code.proxy_log.jsonl` — 3,064,215 bytes
- Native session transcript (Assertion A):
  `claude_code.native_transcript.tar.gz` (3 members) and
  `claude_code.native_transcript.json`
  (`{"archived": true, "config_dir": "/agent-home/.claude", "exit_code": 0, "members": 3}`)
- Converted trace: `conversation.json` — 73 messages
- Also read: `claude_code.event_stream.jsonl` (170 events), `claude.info`,
  `git_integrity.json`, `verifier.json`, `patch.diff`, `patch.base_ref.txt`
- Grading: `<record root>/unit_test/{a0,a1,a2}/run.json`

## 7. Unanticipated findings

**Mandatory, and filled on every run** — including the runs with nothing to
put here, where the entry is exactly `None observed.` The empty slot is the
point: a report that can only record what its criteria anticipated cannot
learn anything a script could not have told us, and a report with nowhere to
put a surprise does one of two things with it — drops it, or bends a closure
criterion to make room. The second is what a pre-registration exists to
prevent, so the slot stands on every run — not a door opened once something
turned up. `PREREGISTRATION.md` does not ask for it; this file does.

Nothing here is frozen by `PREREGISTRATION.md` and nothing here may close,
weaken or strengthen one of the seven points.

### 7a. The supervisor runs an order of magnitude slower than the actor

    supervisor.jsonl  first row  07:23:35.650499  (cursor 1)
    supervisor.jsonl  last row   07:42:14.572388  (cursor 170)
    → 1118.9 s / 170 boundaries = 6.58 s per boundary  (measured)
    → ≤ 955.1 s with the actor finished and waiting    (at most)
    → ≤ 84.9% of the rollout's 1124.47 s wall clock    (at most)

Each boundary is a synchronous judge call inside the poll loop. The actor
finished its 170 events in under three minutes; the supervisor then spent a
further sixteen minutes or so working through the backlog, and the run ended when
it caught up — `at_rest` is reached on the last event, the channel closes,
and the CLI exits on EOF.

**Two consequences, neither of which any test could have produced.**

**A correction is judged fresh and delivered stale.** All three interventions
say the same thing — *you have not read the current implementation yet* —
because they were judged at cursors 4, 8 and 12, and by the time each one
crossed the channel the actor had moved on. This is not a judge error rate;
it is structural. A judgment costs a model call, and the actor does not wait
during it.

**The wall clock is spent on supervision and charged to the actor.** At 6.58 s
per boundary, `_AGENT_TIMEOUT_S = 3600.0` admits roughly **547 boundaries**;
this run had 170. **Those two numbers carry a coordinate the rest of this
report does not need**: `3600.0` is read from the checkout, not from the run's
record — `src/swe_lab/workflow/definitions.py:63` as of `main` =
`91846dd595fa4e64ed2cd3a71a2c6e41709e1a53` — so `547` is a **mixed
derivation**, one factor fixed by the corpus and one that changes the day
somebody edits the timeout. Without the coordinate it would go quietly false
with nothing failing. A more eventful instance can therefore hit the wall clock
because supervision could not keep up — and ADR-0015 charges `TIMED_OUT` to
the actor. `RUNBOOK.md` §4's presumption covers the #349 wiring failure,
where nothing was written; it does not cover this one, where everything works
and is merely slow. **The diagnostic is two numbers this report now always
carries:** the catch-up span and its share of the wall clock. A run where the
actor is idle for most of its budget is not an actor that was slow.

**A methodological consequence, recorded because it happened.** Mid-run, with
`corrections/done` absent and the `claude` process still alive, this was read
as *the channel was never closed*. What held at that moment was *not closed
yet*: the file appeared sixteen minutes later. That catch-up tail is exactly
what makes "not yet" look like "never" — the same reading, taken at two
times, is two opposite facts. It cost a wrong broadcast and no data.

[`LATENCY.md`](LATENCY.md) is this section's per-boundary figure opened up into
a distribution over the same record, with its own witness script.

### 7b. 16 of 170 judge calls returned an answer the policy could not use

`metrics["supervision.lapses"] = 16` (9.4%), every one a `JudgeAnswerError`
raised at `src/swe_lab/trace_synthesis/judge.py`'s parse of
`response["choices"][0]["message"]["content"]`. **They are not one
phenomenon. Two classes, recorded separately so a fix aimed at one is not
mistaken for a fix for both:**

| count | inner error | what the provider returned |
|---|---|---|
| 11 | `the JSON object must be str, bytes or bytearray, not NoneType` | a well-formed response whose `content` was **null** — no output at all |
| 3 | `Unterminated string starting at …` | output that stopped mid-string — truncation |
| 1 | `Expecting value` | malformed |
| 1 | `Expecting ',' delimiter` | malformed |

**Distribution**: cursors 87, 96, 98, 100, 101, 106, 108, 109, 110, 128, 131,
134, 136, 140, 158, 165 — **none before cursor 87**, all after it.

Phenomenon and distribution only; **no cause is offered here.** One run of
one instance cannot distinguish between the several explanations that fit,
and naming one would make the other candidates harder to see. What is worth
recording is the split itself: an account that called all 16 "truncated
output" would send the next reader to fix a problem that explains 3 of 16.

### 7c. The integrity verifier flagged the actor for doing what the supervisor asked

`metrics["verifier.flagged"] = 1.0`; `verifier.json` →
`flagged: ["suspicious_git"]`, `high_confidence: []`, on four commands the
actor ran:

    git show --stat HEAD
    git log --all --oneline
    git show 5f7d8d190 --stat
    git show 5f7d8d190 -- openlibrary/core/models.py

Against the other side's readings: `git_integrity.json` records
`base_sha = 5f7d8d190e2f0d837545e582fd5db99aae51a979`, and the purge
succeeded — `future_commits` 3172 → 0, `solution_reachable` true → false,
`violations: []`.

**Read together: the sha the actor inspected is the base commit itself**, the
run's own starting point, not any future commit; the future commits were
gone; nothing was flagged with high confidence. **No evidence of a boundary
crossed.** Stated as the readings rather than as a verdict on the verifier:
it applied its rule and the rule does not distinguish "inspect the base" from
"go looking" — which is a mismatch between the rule and this situation, not
an error in it.

**What makes it worth a section**: the actor was reading the current
implementation with `git show <base> -- models.py` — which is precisely what
the supervisor had told it three times to do. **Two safety mechanisms rubbed
against each other**, and only a real run puts them in the same room.

## 8. Open questions

- Does the 6.58 s per boundary hold at other instances and event volumes, or
  is it particular to this one? A single point cannot say, and §7a's
  consequence scales with it.
- Do the 16 unusable judge answers cluster after cursor 87 because of
  something that grows with the run, or because of something about that
  stretch of this instance? Both fit; nothing here separates them.
- Point 4 rests on a recorder we own. What would an independent check of the
  wire shape even look like, given the project cannot obtain provider-side
  evidence? Recorded as unresolved rather than as a limitation to be restated
  each run.
