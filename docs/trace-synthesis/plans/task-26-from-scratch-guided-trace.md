# Task 26: The from-scratch chain and its reading — design record

**Status lives in [`README.md`](README.md#task-26-the-from-scratch-chain-and-its-reading), not here.**
This is a point-in-time design record for the `from_scratch_guided_trace`
workflow and the `guided-gain` reading, written against the code that landed
with [ADR-0023](../../decisions/ADR-0023-phase-a-returns-as-an-entry-of-the-from-scratch-chain.md).
Where this record and the code disagree, the code wins.

## 1. What it is for

The pipeline's two shipped entry points, `oracle_analysis` and
`oracle_guided_trace`, start at phase B over an `oracle_failures` record: a
failure somebody already paid for, hand-assembled into a dataset row
([task 11](task-11-oracle-failures-dataset.md)). Both presuppose the failure
exists, and the row builder refuses a run that resolved — so from those two
workflows alone the one reading the owner asked for cannot be assembled: for
each instance, **did the blind rollout pass, and did the guided one?**

That reading is a 2×2 over the pair of verdicts, and two of its cells exist
only when every instance goes through every step:

|                   | guided pass | guided fail   |
|-------------------|-------------|---------------|
| **baseline pass** | `kept`      | `regressed`   |
| **baseline fail** | `gained`    | `unsolved`    |

The two numbers the chain is run for are the marginals the table names in
words: **solved at baseline** (kept + regressed — the first roll was already
right) and **gained with the guidebook** (wrong blind, right guided). The
`regressed` cell is why the chain is **unconditional**: a chain that skipped
the guidebook and the guided rollout whenever the blind rollout passed could
report the gain and never the harm. No skip flag ships with this task; one
that does will have to say which cell it gives up
([ADR-0023 §2](../../decisions/ADR-0023-phase-a-returns-as-an-entry-of-the-from-scratch-chain.md#2-the-chain-is-unconditional)).

## 2. The definition

[`src/swe_lab/workflow/definitions.py`](../../../src/swe_lab/workflow/definitions.py),
`FROM_SCRATCH_GUIDED_TRACE`, registered as `from_scratch_guided_trace`. Five
entries, in order; the key is the store's task segment
(`<sweep>/<instance>/r<rollout>/<key>/a<attempt>`, ADR-0007 §6) and the only
thing that tells the two rollouts and the two gradings apart:

| # | key (`WorkflowEntry.key`) | task | same as | inputs bound |
|---|---|---|---|---|
| 1 | `baseline_rollout` | `Task` — the container agent loop, git-history purge on, baseline observer, capture | `rollout`, under another key (`_rollout_entry(BASELINE_ROLLOUT_KEY)`) | none |
| 2 | `baseline_unit_test` | `Task` — `UnitTestParseObserver` + `BaselineVerifyObserver` | `unit_test`, under another key (`_unit_test_entry(BASELINE_UNIT_TEST_KEY, …)`) | `patch.diff`, `patch.base_ref.txt` ← `baseline_rollout` |
| 3 | `oracle_analysis` | `OracleAnalysisTask(failure_inputs=True)` | the `oracle_analysis` entry, plus §3 below | `conversation.json`, `patch.diff`, `patch.base_ref.txt` ← `baseline_rollout`; `unit_test.verdict.json` ← `baseline_unit_test` |
| 4 | `guided_rollout` | the segmented supervised rollout, `guidebook_name=GUIDEBOOK_NAME` | `oracle_guided_trace`'s rollout, under another key (`_segmented_rollout(…, key=GUIDED_ROLLOUT_KEY)`) | `guidebook.md` ← `oracle_analysis` (by name; one producer) |
| 5 | `guided_unit_test` | as 2 | as 2 | `patch.diff`, `patch.base_ref.txt` ← `guided_rollout` |

The bound-inputs column is the map `tests/test_from_scratch_guided_trace.py`
pins as `EXPECTED_EDGES`, asserted through the engine's own resolver
(`_resolve_edges`) rather than re-derived. Two facts about it:

- **The bindings on entry 5 are demanded by the engine.** By the time the
  guided grading binds, both rollouts have produced `patch.diff` and
  `patch.base_ref.txt`, and an unbound name with two producers is refused
  (`WorkflowError: … bind it explicitly`). The test's control arm strips the
  bindings from entry 5 and asserts that refusal. Every other binding in the
  definition is unambiguous today and is written out anyway, so the graph can
  be read off the definition rather than inferred from name matching.
- **Two entries under one key are refused at declaration**
  (`validate_declaration`, `WorkflowError: duplicate entry keys`) — asserted
  as the second control arm. That refusal is the collision guarantee: two
  `patch.diff`s in one run live at `…/r0/baseline_rollout/a0/patch.diff` and
  `…/r0/guided_rollout/a0/patch.diff`, and nothing else is renamed or
  suffixed ([ADR-0023 §3](../../decisions/ADR-0023-phase-a-returns-as-an-entry-of-the-from-scratch-chain.md#3-the-key-is-the-namespace--there-is-no-second-one)).

The shipped entries are unchanged: `ROLLOUT`, `UNIT_TEST`, `ORACLE_ANALYSIS`
and `ORACLE_GUIDED_TRACE` are now built by the same small factories with the
old keys as defaults (`_rollout_entry()`, `_unit_test_entry()`,
`_oracle_analysis_entry()`, `_segmented_rollout()`), and
`tests/test_workflow_registry.py` still sees the same declarations.

## 3. The Oracle's two modes

[`src/swe_lab/trace_synthesis/oracle.py`](../../../src/swe_lab/trace_synthesis/oracle.py).
Edges match by store name and never rename (ADR-0007 §5), and a rollout
produces `conversation.json` / `patch.diff`, not the sample contract's
`failed_conversation.json` / `failed_patch.diff` that an `oracle_failures`
record stages. So the task learns a second set of names rather than the edge
layer learning to rename — the shape the `unit_test` entry already has
(standalone: caller or instance supplies the patch; chained: an edge does).

`FailureFiles` names where the failure is in the workspace; two instances of it:

| | `STAGED_FAILURE` (default, `failure_inputs=False`) | `PRODUCED_FAILURE` (`failure_inputs=True`) |
|---|---|---|
| conversation | `failed_conversation.json` | `conversation.json` |
| verdict | `failed_verdict.json` | `unit_test.verdict.json` |
| patch | `failed_patch.diff` | `patch.diff` |
| pre-agent baseline | — (the record's patch is against the instance's base) | `patch.base_ref.txt` |
| how it arrives | the instance's `mounts()` — checked for the three names at `mounts()` time | declared **required inputs**, materialised by the engine from the edges; a missing one stops the run before the agent (`required input(s) missing`) |
| brief | `oracle_prompt` — byte-identical to before (the pinned digest test in `tests/test_oracle_analysis.py` still passes) | `produced_failure_prompt` — same brief, the file table under the produced names plus a row for the base ref |
| grading procedure | applies `failed_patch.diff` against the instance's base | applies `patch.diff` against the recorded pre-agent baseline (`patch_baseline=True`, ADR-0014); `BaselineVerifyObserver` verifies and resets to it first |

What does not change between the modes: the golden test patch and — when the
dataset records one — the golden patch are staged the same way
(`privileged_mounts`), the git-history purge is off, no verifier is composed,
and the declared output is `guidebook.md` under the same schema check. The
sample contract (`sample.py`) is untouched, so `oracle_failures` records and
the two phase-B workflows read exactly what they read before.

Tested both ways in `tests/test_from_scratch_guided_trace.py`: the produced
failure placed as an edge would place it is read (SUCCESS, valid outputs, the
entryscript applies `patch.diff` with `patch-baseline=True`, the baseline
verify observer ran, the brief names the produced files and no `failed_*`
name); and a produced failure nobody supplied stops before the agent, naming
the three missing files.

## 4. The verdict travels whole

The grading entry used to persist the verdict's scalars as metrics
(`unit_test.resolved`, `unit_test.score`, the dataset's counts) and nothing an
edge could carry to the Oracle — the brief tells the Oracle that the verdict's
`summary` names the failed tests, and that field lived only in the
`oracle_failures` row's verdict column, reconstructed host-side by re-grading.

Now [`UnitTestParseObserver`](../../../src/swe_lab/evaluation/unit_test.py)
declares a required artifact `unit_test.verdict.json` and emits it inline at
`before_destroy`: `Verdict.facts()` — `resolved`, `score`, `metrics`,
`summary` — the same four keys the `oracle_failures` verdict column carries.
`facts()` is a concrete method on the `Verdict` ABC
([`evaluation/verdict.py`](../../../src/swe_lab/evaluation/verdict.py)), for
the reason ADR-0006 put `resolved` there.

**This is an additive artifact on every evaluation** — `unit_test`,
`gold_unit_test`, every `*_and_unit_test` definition — **and not a change to
the report contract**: `AttemptRecord`, the workflow record, the CLI summary
and `swe_lab.reporting` are untouched; an attempt's `artifact_keys` lists one
more name. `tests/test_unit_test_method.py` pins the artifact's content
against `facts()`.

## 5. The reading

[`src/swe_lab/trace_synthesis/guided_gain.py`](../../../src/swe_lab/trace_synthesis/guided_gain.py),
surfaced as `python -m swe_lab guided-gain`
([`cli/guided_gain.py`](../../../src/swe_lab/cli/guided_gain.py)).

Input: the store. The sweep's attempt shards (`Store.read_manifests(sweep)`)
say which `(instance, rollout)` runs exist; for each, the reading opens the
run's **workflow record** (`<sweep>/<instance>/r<n>/workflow.json`, the
roll-up `Workflow.execute` writes last, whatever the outcome) and takes both
verdicts from it — the entry under the baseline grading key and the entry
under the guided grading key (defaults `baseline_unit_test` /
`guided_unit_test`; both are options), each read off its final attempt's
`*.resolved` metric (by suffix, as the CLI's exit code reads it) and only when
the entry `succeeded`.

**Why the record and not the shards.** The store keeps every shard it was
given. `swe_lab run` defaults to `resume=False`, so a re-run overwrites `a0`
and leaves an older run's `a1` behind; a re-run whose Oracle failed leaves the
previous run's `guided_unit_test/a0` beside its own fresh
`baseline_unit_test/a0`. Selecting by highest attempt number pairs the current
baseline with a verdict from a run that no longer exists in the first case,
and grades a guidebook the current run never wrote in the second. The record
names the invocation (`run_ts`) and rolls up exactly the attempts it spent,
which is how the task runner already reads its own terminal marker. Both
shapes are pinned on a real `FilesystemStore` through the real engine in
`tests/test_from_scratch_guided_trace.py` (the two re-run tests), and both go
red when the selection is put back to "highest attempt on disk".

**Why the record's presence is not enough.** Nothing clears the previous
invocation's `workflow.json` when a `resume=False` run starts; the engine
overwrites it last. A run killed after its fresh shards landed and before its
record leaves new shards under an old record, and reading that record would
report the *previous* run's cell for an invocation that has no verdicts. So
the record is consulted only if it is **current**: no shard under the run may
carry a `run_ts` later than the record's (`run_ts` is sortable by
construction). Not "all shards equal the record's `run_ts`" — a `--resume`d
entry's shards and a completed forced re-run's outlived attempts are both
legitimately *older* than their record. Pinned through the real engine by
killing the record write of a forced re-run over a complete old run; red when
the generation test is removed (the old `kept` comes back).

Placement rule, in order:

1. No workflow record → `IncompleteRun` missing `workflow.json`: the run never
   reached the end of an invocation.
2. A record older than a shard under the run → `IncompleteRun` naming
   `workflow.json predates shards`: an earlier invocation's record, left in
   place by a later one that was killed before writing its own.
3. A grading key whose entry is absent from the record, never ran (blocked),
   ended failed, or carries no `*.resolved` → `IncompleteRun` naming the
   key(s). **Counted and listed, never folded into a cell** — "not graded" and
   "graded as failing" are different facts, and only one of them is a zero.
4. Otherwise a `RunPair`, carrying the record's `run_ts`, placed by
   `cell_of(baseline_pass, guided_pass)`.

Output, JSON on stdout:

```json
{
  "sweep_id": "…", "baseline_key": "baseline_unit_test", "guided_key": "guided_unit_test",
  "runs": [{"instance_id": "…", "rollout_id": 0, "run_ts": "20260906-020000", "baseline_pass": true, "guided_pass": false, "cell": "regressed"}],
  "cells": {"kept": 0, "gained": 0, "regressed": 1, "unsolved": 0},
  "solved_at_baseline": 1,
  "gained_with_guidebook": 0,
  "incomplete": [{"instance_id": "…", "rollout_id": 0, "missing": ["guided_unit_test"]}]
}
```

and the table on stderr, whose lines spell the marginals out so nobody adds
cells by hand:

```text
sweep <id>: N (instance, rollout) pair(s) graded by both baseline_unit_test and guided_unit_test, K incomplete
                guided pass       guided fail
baseline pass   kept n            regressed n
baseline fail   gained n          unsolved n
solved at baseline (kept + regressed): n / N
gained with the guidebook (baseline fail, guided pass): n / N
regressed with the guidebook (baseline pass, guided fail): n / N
incomplete, not counted above: K
  <instance> r<rollout>: missing <key>[, <key>]
```

Every cell and the incomplete count print at zero ("none incomplete" and
"incompleteness not reported" must not look alike), and a sweep with **no**
records is refused with exit 1 rather than rendered as four zeros. Not a
`Rate` line: the incomplete set is "no verdict for one half", not an ending
of ours, and `Rate`'s excluded slot would misname it
([ADR-0023, alternatives](../../decisions/ADR-0023-phase-a-returns-as-an-entry-of-the-from-scratch-chain.md#a-rate-line-adr-0015-5--adr-0016-for-the-marginals)).

`tests/test_guided_gain.py` writes literal fixture runs — shards plus the
workflow record, one per cell, one per kind of incompleteness — into a real
`FilesystemStore` and asserts the cells, both marginals, the JSON, the table
text, that an ungraded run is incomplete and not `unsolved`, that a record
without the grading keys (another workflow's run under the same coordinates)
is not this chain's run, that a record older than a shard under it is not the
current run, that two rollouts of one instance are two pairs, and the
command's stdout / stderr / exit code including the empty-sweep refusal.

## 6. Running it, and where things land

```sh
# one instance, all five steps, records under sweep <id> in the T1 store
python -m swe_lab run from_scratch_guided_trace <instance_id> --persist --sweep <id> [--rollout-id n]

# the reading over that sweep (JSON on stdout, table on stderr)
python -m swe_lab guided-gain <id>                       # the repo's T1 store, .cache/store/runs
python -m swe_lab guided-gain <id> --store-root <dir>    # a run directory's own store/, or a copy taken off the box
```

`--persist --sweep <id>` is what makes the records readable afterwards: the
reading is taken from the store's attempt shards, and a run without
`--persist` leaves only the run directory. The sandbox credential and model
knobs are the shipped entries' (`--baseline_rollout.harness.model …`,
`--guided_rollout.…` — the override path is the entry key).

Where a run's artifacts land, with `<key>` one of the five above:

```text
.cache/store/runs/<sweep>/<instance>/r<rollout>/<key>/a<attempt>/<artifact>     # the T1 store (--persist)
.cache/runs/from_scratch_guided_trace/<instance>/r<rollout>/                     # the run directory (--output-root moves it)
```

so a run's two patches are `…/baseline_rollout/a0/patch.diff` and
`…/guided_rollout/a0/patch.diff`, its two verdicts
`…/baseline_unit_test/a0/unit_test.verdict.json` and
`…/guided_unit_test/a0/unit_test.verdict.json`, and the guidebook
`…/oracle_analysis/a0/guidebook.md`.

**Cost and scale are the consumer's.** Each run is two rollouts, two gradings
and one Oracle call, every time — there is no early exit. The repo's ceiling
on paid rollouts (AGENTS.md, Boundaries) applies to this workflow as it does
to any other, and the [downstream note](../downstream-scale-note.md) is where
the per-rollout cost is measured.

## 7. What is tested, and what deliberately is not

Tested, all without a container, a model or a credential:

- declaration: five distinct keys; the edge map through the engine's resolver;
  the two control arms (guided grading unbound → ambiguous; two gradings under
  one key → duplicate);
- the Oracle both ways (§3), and its staged brief byte-identical;
- the verdict artifact (§4), and the whole non-docker suite — every
  `UNIT_TEST`-containing definition, `gold_unit_test` and the `control_*`
  variants included — green with it added;
- five fake tasks executed by the real `Workflow.execute` under the real
  definition's bindings, asserting each grader saw its own rollout's patch, the
  recorded edges equal the map, and the store holds both patches under both
  keys; the resulting records read as one `regressed` pair through
  `guided_gain`;
- three re-runs over the same store through the same engine: a forced re-run
  after a two-attempt grading reads as the re-run's verdict, not the outlived
  `a1`'s; a re-run whose Oracle failed reads as incomplete, not as a pair
  completed by the previous run's guided shard; a forced re-run killed before
  its record write reads as incomplete (`workflow.json predates shards`), not
  as the previous run's `kept`.

Not tested here, on purpose: **no live rollout or Oracle run**. A run is paid
work at the consumer's expense and the owner's magnitude rule governs it; the
first live run is task 08's batch or the consumer's. When it happens, its
report belongs in `experiments/`, not in this record.

## 8. Decisions taken here, and what is left open

- **Producer-named Oracle inputs, not edge renaming** — §3, and ADR-0023's
  alternatives.
- **The verdict as an artifact on every evaluation, not a report-contract
  change** — §4.
- **Cell names `kept` / `gained` / `regressed` / `unsolved`**, with the two
  marginals spelled out in the table rather than left to be added.
- **Absent is not zero**: an ungraded run is incomplete, an empty sweep is
  refused.
- **Open, not decided here:** a skip-on-baseline-pass flag (and which cell it
  would surrender); the policy stamp on this chain's phase-B / phase-C records
  (task 07 — a from-scratch run's records are as unstamped as an
  `oracle_guided_trace` run's, and pooling them with benchmark numbers is as
  wrong as it was); whether the guidebook helps at all (ADR-0018's open
  question — this chain produces the pair of verdicts that question needs and
  claims nothing about their difference).
