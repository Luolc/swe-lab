# ADR-0026: The correction channel is removed, and the segment loop is the only carrier left

## Status

Accepted. **The decision is the owner's, taken on 2026-09-08**: delete every
supervisor-route logic outside what the segment loop needs, on Occam — the
simplest code that runs the segmented supervised route is what they want to
read. Nothing below adds a reason of its own.

**Supersedes
[ADR-0013](ADR-0013-supervision-on-the-stdin-channel.md)** (supervision is
delivered on the harness's stdin channel). That is a real supersession, not a
rule's shape being satisfied: ADR-0013 chose a delivery mechanism, and this
change removes the mechanism it chose. It is a **retirement, not a refutation**
— ADR-0013's own *What would overturn this* asks a later record to say which,
and the answer is neither of its two: the byte-identity result was never
contradicted, and the paired-arm measurement it named was never run. The
channel goes because the owner has ruled one carrier is enough, which is a
third reason ADR-0013 did not anticipate.

This is the **second stage** [ADR-0025](ADR-0025-the-segment-loop-is-the-only-supervised-carrier.md)
staged and named: "removing the correction channel, the streaming `Supervisor`
and the classes around it, and superseding ADR-0013". With it, ADR-0025's
closing sentence stops being true — this repo now has one supervised carrier in
the tree, and it is the one of record.

## Date

2026-09-08

## Context

Three carriers were built for one supervision stack. ADR-0025 removed the
native runtime and stated why the channel was not removed in the same PR: two
removals in one change would put two independent couplings in front of one
reviewer. This is the other half.

**What the channel was.** A FIFO on the actor's stdin, held open by an
in-sandbox relay reading a bind-mounted drop directory; a host-side
`SupervisorPump` polling the actor's event stream; a streaming `Supervisor`
that consumed one event at a time, consulted a policy, and wrote a correction
into the FIFO while the actor was still working. Closing the FIFO's write end
*was* the run's termination mechanism, which is why the whole apparatus carried
an unclean marker, a sentinel file and a failure-closed relay.

**What the segment loop is.** Stop the actor every *N* turns (`--max-turns`),
judge at the cut, resume with `--resume`. It does not need the actor's stdin,
because it starts a new actor invocation for each segment.

Everything the two shared — the criterion, the judge, the writer, the speak
policy, the evidence window, the running state, the guidebook,
`said_visibility`, the `supervisor.jsonl` account — is host-side Python neither
of them owns, and none of it is removed here.

## Decision

**The correction channel and the streaming carrier around it are removed from
the repository, in full, together with everything that existed only to serve
them.** The segment loop is the only supervised carrier.

Removed, with the number of files that referenced each **before** the removal
(Python under `src/`, `tests/` and `experiments/`; a file is counted once):

| Removed | Lines | Referencing files before | Why it went |
|---|---|---|---|
| `src/swe_lab/trace_synthesis/channel.py` — `CorrectionChannel`, `SupervisorPump`, `SupervisedRun`, `supervision()`, `stream_events` | 570 | 8 | the carrier itself |
| `Supervisor`, `NeverSpeak`, `SpeakAt`, `WouldHaveSpoken`, `EvidenceFilter` and the disposition constants, from `supervisor.py` | ~450 | `Supervisor` 6, `NeverSpeak` 6, `SpeakAt` 3, `WouldHaveSpoken` 1, `EvidenceFilter` 4 | the streaming carrier and what only it consumed |
| `ClaudeCodeHarness.correction_channel`, `_relay_start_lines`, `_RELAY_POLL_INTERVAL_S`, the `CORRECTION_*` constants, `__post_init__`'s two-carrier refusal | ~190 | `correction_channel` 8, `CORRECTION_*` 5 | the harness's half of the wiring |
| `Harness.accepts_corrections` | 14 | 4 | a capability asked only to refuse a channel-less supervised run |
| `CodingAgentTask.supervision_factory`, the `SupervisionFactory` type and the composition refusal | ~45 | 5 | how a channel supervisor was attached to a rollout |
| `SUPERVISED_ROLLOUT`, `CONTROL_ROLLOUT`, their `_AND_UNIT_TEST` chains, their two registrations, `CONTROL_BUDGET`, `SUPERVISOR_TRANSPORT` | ~110 | 3 | the only shipped way to ask for the channel |
| `tests/test_correction_channel.py`, `tests/test_task_17_read.py`, `tests/test_n_batching_replay_witness.py`, and the channel-only tests inside four other files | ~2,500 | — | tested only the removed carrier |
| `experiments/trace_synthesis/n_batching_replay/replay.py` and `analyze.py` | 1,534 | — | the drivers; see below |
| `SUPERVISION_METRIC`, `SUPERVISION_LAPSE_METRIC`, `RolloutOutcome.SUPERVISION_FAILED` and the branch that read them | ~40 | 4 | see below — the channel's observer was their only writer |

**Kept, and worth naming, because each looks removable and is not:**

- `vocabulary.py` — established by ADR-0025. `SUPERVISOR_LOG_NAME` is the
  segmented route's own ledger name. Its docstring is reworded here.
- `SpeakWhenOffTrack`, `Verdict`, `Observation`, `WriterObservation`,
  `Intervention`, `Unjudged`, `Judge`, `Writer`, the `LOG_KIND_*` constants,
  `LogWriter`, `jsonl_writer`, `evidence_of`, `judge_prompt_sha256`,
  `said_visibility_of`, `lapsed_judge_request`, `PolicyLapseError` — the set
  `segmented_loop.py` imports, read off its own import block rather than from a
  list.
- `evidence_of`, whose admission rule was `EvidenceFilter.admit`. The class is
  gone and the rule is now a private function beside it: what the class
  additionally returned was a *disposition string*, and the only consumer of
  those was the removed carrier's log.
- `SUPERVISOR_BASE_URL` in `definitions.py`, which no shipped definition reads
  any more. It is the import-time capture of the default upstream, and the only
  value a test can compare the shipped segmented plan's default against — a
  later re-read of the environment answers a different question. Its comment now
  names that reader.

`SpeakAt` and `NeverSpeak` were also the stand-ins three test files used to
drive a carrier without a model behind it. They did not vanish; they moved to
`tests/policies.py`, which is where a test double belongs.

## Alternatives Considered

### Keep the channel unwired, in case the paired-arm measurement returns

Rejected, on the same ground ADR-0025 rejected an unwired crate: the cost the
ruling is about is the cost of reading and maintaining it, which unwiring does
not reduce. The history is not lost — the channel is recoverable from git at
`1c061ed`, and this ADR names that commit so nobody has to go looking.

### Keep `replay.py` and let it break

Rejected. `experiments/trace_synthesis/n_batching_replay/replay.py` imports the
streaming `Supervisor` and exists to prove its driver reproduces it event for
event; `analyze.py` imports `replay`. A driver whose whole claim is equivalence
to a deleted class is not evidence of anything, and a script left importing a
name that no longer exists is worse than one deleted — it looks runnable.

**The frozen evidence stays exactly as it is.** That experiment's `runs/`,
`REPORT.md` and `PREREGISTRATION.md` are the accurate record of runs that
happened, and deleting a driver does not unmake them. Its README gains one
dated line saying the driver went and the recorded results stand, so the next
reader is not left wondering whether they were retracted.

### Keep the metrics and the outcome word, since the run record is *ask first*

**Proposed, and rejected on 2026-09-08 by the workspace coordinator**, whose
call this was; the reasoning is theirs and is recorded because it draws a line
that will be needed again:

- The owner has already ruled that everything outside the segment-loop route
  goes. These three are artifacts of the carrier being removed, and keeping them
  *because the report contract is ask-first* would be the exact reflex the
  ruling is against — leaving dead surface standing so nobody has to decide.
- **The *Ask first* line exists to stop us changing the shape of records
  consumers read.** Nothing can produce these any more, so no record will ever
  contain them again. An enum member that can never be emitted is not a
  contract, it is a name.
- The mechanism that protects a consumer is the **release note's list of
  downstream-visible removals**, which these three are on — not an unremovable
  constant.

If the owner disagrees, three lines come back, which is cheaper than carrying
dead vocabulary indefinitely.

## Consequences

- **`supervised_rollout_and_unit_test` and `control_rollout_and_unit_test` no
  longer exist.** A caller naming either gets "unknown workflow". The supervised
  routes that remain are `segmented_rollout`,
  `segmented_rollout_and_unit_test`, `oracle_guided_trace` and
  `from_scratch_guided_trace`.
- **There is no paired zero-budget control arm any more.** `SpeakWhenOffTrack`
  still judges before it consults its budget, so one could be built; what is
  gone is the shipped definition, the `CONTROL_BUDGET` constant that stated why
  the arms were matched, and the `WouldHaveSpoken` markers a zero-budget arm
  existed to collect. A future paired measurement re-adds an arm, and the
  reasoning it needs is in this repo's history at `1c061ed`.
- **Acceptance point 2b weakened, and this is the honest statement of it.**
  "A forged criterion refuses to *start* the run — the sandbox is never
  created" was true of the channel, whose supervision was built while a
  rollout assembled its observers. The segment loop builds its policy inside
  `SegmentedRun.run`, so a forgery now stops the run **after** a sandbox
  exists. The refusal itself is unchanged and still on the shipped path:
  `test_every_supervised_route_a_command_can_name_is_registered` calls each
  shipped route's policy factory and asserts the pinned digest, and
  `test_a_forged_criterion_cannot_build_the_policy` pins the rejection. What is
  no longer claimed is *when*. The spec and the task index are reworded in this
  PR rather than left carrying the stronger sentence.
- **Nothing reads a lapse count or a gap out of a run record any more, and the
  names for doing so are gone.** The spec's *a lapse is counted where the
  outcome is read; a gap excludes the run* was true of the channel's observer,
  which raised `supervision.lapses` and `supervision.unhealthy`. Both kinds of
  row are still written to `supervisor.jsonl` and are still told apart there —
  that half keeps its tests, and gained one. The **counting** half is deleted
  rather than downgraded: `SUPERVISION_METRIC`, `SUPERVISION_LAPSE_METRIC`, the
  `rollout_outcome` branch that read the first, and
  `RolloutOutcome.SUPERVISION_FAILED` all go. A carrier that wants the behaviour
  back adds a metric and a word for it, and says in that change what writes them.
- **`RolloutOutcome` is six words again**, and `_OURS` is two. The ordering the
  removed branch enforced — an unbounded supervision failure outranks a spent
  wall clock — is not silently lost: the remaining ordering it belonged to (an
  OOM kill outranks a timeout) is pinned by
  `test_an_out_of_memory_kill_outranks_a_spent_wall_clock`, which names the
  third cause in its docstring as the thing that used to sit between them.
- **`ClaudeCodeHarness` has one supervision field**, so the mutual-exclusion
  refusal in `__post_init__` is gone with the pair it refused.
- **The `no-stale-module-refs` hook gained the removed names**, so
  `correction_channel`, `CorrectionChannel`, `SupervisedRun`, `SupervisorPump`,
  `NeverSpeak`, `SpeakAt`, `WouldHaveSpoken`, `supervision_factory`,
  `accepts_corrections`, `CONTROL_BUDGET`, the two arm names and the
  `CORRECTION_*` constants cannot return to `src/`, `tests/` or `experiments/`
  Python without failing the gate. **`EvidenceFilter` is deliberately not on
  that list**: `experiments/trace_synthesis/pipeline_end_to_end/witness.py`
  names it in a comment explaining that it re-implements the rule rather than
  importing it, and that file is frozen evidence for a run that happened.
  Banning the token would have meant editing it.
- **One invariant gained a test it never had.** The segment loop's *a failure
  the policy did not bound is a gap, not a lapse* was stated in a docstring and
  asserted nowhere; the equivalent assertion existed only for the removed
  carrier. `test_a_failure_the_policy_did_not_bound_is_a_gap_and_the_run_goes_on`
  is new, and was checked by mutation: swapping the row kind in
  `segmented_loop.py` turns it red.
- **Tasks 13, 14, 16 and 17 are retired unrun.** Each was registered against
  the channel — confirming it in the sandbox, its edges under a real rollout,
  its harness plumbing, and the pump's two indistinguishable readings. None has
  a subject any more.
- **Issue [#381](https://github.com/Luolc/swe-lab/issues/381)'s follow-up 1 is
  no longer runnable as written**, because it names the deleted driver. Said on
  the issue, which stays open.

## What would overturn this

One condition, and it is not symmetrical with ADR-0013's. This record decides
that **one carrier is enough**, not that stdin delivery was wrong. It would be
reopened by a measurement showing the segment loop's cut changes what is being
measured — that stopping the actor and resuming it is not a neutral way to
reach it, so a supervised run under this carrier is not evidence about
supervision but about segmentation. The seam observations task 22 records are
where that would first show up. Nothing in this PR bears on it either way.
