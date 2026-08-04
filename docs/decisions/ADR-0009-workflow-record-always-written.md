# ADR-0009: The workflow record is always written — it is a report, not a marker

## Status

Accepted (supersedes the record-absence rule of
[ADR-0007](ADR-0007-task-and-workflow-layer.md) §10; the rest of §10 stands)

## Date

2026-08-04

## Context

ADR-0007 §10 gave the derived workflow record two jobs at once: it is the
roll-up of a run's per-task records, **and** its absence is the signal that the
workflow did not complete. So `Workflow.execute` wrote `workflow.json` only when
every entry succeeded.

The completion half of that has always been slack. ADR-0007 §10 says so itself:

> That absence rule is safe precisely because task markers are terminal. A
> workflow whose task failed has no record, so resume re-enters it — and
> immediately hits that task's terminal marker, does not re-run it, blocks, and
> fails again **having done no work**. Recording the failure at the workflow
> level too would save a cheap re-entry, but it is a nicety, not a correctness
> requirement; v1 can record success only.

That reasoning holds, and nothing in the tree contradicts it: no code reads the
workflow record to decide anything. Resume is entirely task-marker driven
(`run_task` reads `TerminalMarker`), and `record_key` is only ever reported
outward.

What the absence rule costs is **reading**, not correctness:

1. A failed run leaves no roll-up at all — and the failed run is the one most
   worth reading. A consumer has to glob every task prefix and reassemble the
   entries by hand, which is the work the roll-up exists to avoid.
2. `WorkflowOutcome.succeeded` is computed, reported in memory, and never
   persisted.
3. The roll-up carries no metrics, though `AttemptRecord.metrics` already holds
   them and `_write_record` already has the final record in hand — so a
   consumer that wants a verdict opens one extra object per task per run to
   read a dict that could have been copied.

There is also a plain readability cost: the task layer one level down writes its
terminal marker for a failed task exactly as for a succeeded one (`TaskOutcome`
— "both values are terminal"), so the two layers used opposite conventions for
"this finished".

## Decision

**Write the workflow record whatever the outcome, and put the outcome in it.**

- `_write_record` is called unconditionally; the JSON gains a top-level
  `"succeeded": bool`. `record_key` is populated either way.
- Each entry carries its `status` (`succeeded` / `failed` / `edge_failed` /
  `blocked`), and its `metrics` copied from the final attempt shard.
  `missing_inputs` is present only for `edge_failed`.
- An entry that never ran (`blocked`, or `edge_failed` before any sandbox
  existed) is **emitted with zeroed counters**, not skipped — that it never ran
  is the fact worth recording.
- The two properties that made it trustworthy are unchanged: written **last**,
  after every entry has stopped, and **atomically**, so a torn write can never
  read as complete.

**Resume is explicitly out of scope.** It stays task-marker driven, exactly as
ADR-0007 §10 argues it should. This ADR does not give the workflow record a
role in control flow; it makes it a reporting artifact and nothing more.

Absence now means something stricter and more useful: **the workflow never got
past binding.** A `WorkflowError` from `_resolve_edges` still raises before any
entry runs, so nothing is written — correct, since no work was attempted.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep success-only; let consumers glob task prefixes | That reassembly is precisely what the roll-up exists to avoid, and it is worst in the failed case, which is the one most often read. |
| Write the record always **and** resume from it | Two sources of truth for "did this finish", one level apart. Task markers already answer it and are already terminal; a second answer can only drift. Deliberately not done. |
| Add a separate `workflow-failed.json` | Two object names for one fact, and every consumer has to look for both. A field in one record is strictly simpler. |
| Recompute metrics at the workflow level | Would stop being a roll-up. ADR-0007 §10's "nothing new is measured" is worth keeping; copying what the shards already say preserves it. |

## Consequences

- A consumer relying on "absence means failed" must read `"succeeded": false`
  instead. This is the one breaking change; the record is a T1 artifact, not a
  public API, and no in-tree reader existed.
- `assert run is not None` in `_write_record` no longer holds and is gone —
  `blocked` and `edge_failed` entries have no run.
- Reporting over a sweep becomes one object read per run, for both outcomes.
- The two layers now agree: a terminal task writes its marker, a finished
  workflow writes its record, and in both cases the outcome is *in* the record
  rather than in its absence.
