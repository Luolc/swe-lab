# ADR-0023: Phase A returns as an entry of the from-scratch chain, unconditionally, and its two rollouts are told apart by key

## Status

Accepted. The owner asked for the from-scratch form on 2026-09-05; the
workflow, the analysis reading and the trace-synthesis spec reconciliation
land with this record. On 2026-09-06 the owner ruled, after the accepting
PR's review escalated it, that the engine retires the previous workflow
record before the first entry of every invocation runs (§6) — the reading's
"a present record is the current invocation's" rests on that, not on any
read-side test.

This supersedes one paragraph of
[`trace-synthesis/spec.md` §3](../trace-synthesis/spec.md#phase-a--baseline-rollout-and-eval)
— *"Phase A is not re-run by the pipeline"* — **for the from-scratch workflow
only**. The `oracle_guided_trace` and `oracle_analysis` workflows keep
starting at phase B over an `oracle_failures` record, exactly as that
paragraph describes, and nothing here changes them.

It also **partly supersedes two accepted ADRs on the workflow record's
lifecycle**, by the 2026-09-06 ruling recorded in §6; the older ADRs are not
edited (an accepted ADR is superseded, never rewritten), and the sentences
this ADR replaces are quoted here so the boundary is exact:

- [ADR-0007 §10](ADR-0007-task-and-workflow-layer.md) describes the record's
  discipline as *"Same discipline as a task's marker, one level up: written
  **last**, after every task record is durable"*. The record is now written
  last **and removed first**: `Workflow.execute` deletes the previous
  invocation's record before its first entry runs. The "written last" half
  stands; the discipline is no longer only a write.
- [ADR-0009](ADR-0009-workflow-record-always-written.md) states the record's
  absence semantics as *"Absence now means something stricter and more useful:
  **the workflow never got past binding.** A `WorkflowError` from
  `_resolve_edges` still raises before any entry runs, so nothing is written —
  correct, since no work was attempted."* Absence now means **the latest
  invocation never finished**: either it never got past binding (nothing is
  written, and nothing is removed either), or it retired the previous record,
  ran, and died before writing its own. ADR-0009's list of *"the two
  properties that made it trustworthy"* — written last, atomically — gains a
  third, removed first. Everything else in ADR-0009 stands: the record is
  still written whatever the outcome, it still carries `succeeded` and each
  entry's status and metrics, and resume is still task-marker driven and
  reads no workflow record.

## Date

2026-09-05

## Context

### The pipeline had one entry point, and it presupposed a cached failure

The trace-synthesis pipeline is four phases: A, a blind rollout graded; B, the
Oracle writing a guidebook for that failure; C, a supervised rollout under the
guidebook; D, collection. The owner ruled on 2026-09-01 that phase A is not
re-run by the pipeline — a full eval sweep caches the failures anyway, so the
pipeline's entry point is phase B over a hand-assembled `oracle_failures` row
carrying the instance and the failed run. That is what shipped:
`oracle_analysis` is a one-entry workflow over such a row, and
`oracle_guided_trace` is B → C → grading over the same row.

Both presuppose the failure exists. For an instance no sweep has failed on —
or a fresh batch on a dataset nobody has swept — there was no registered
workflow that produced the failure and then used it; an operator had to run
`rollout_and_unit_test`, run the `oracle_failures` builder over the run
directory, load the row, and then run `oracle_guided_trace` over it, keeping
the pieces together by hand.

### The reading the owner wants needs both verdicts of the same run

The owner's question is a pairing: how many instances does the blind rollout
already solve, and how many does the guided rollout solve that the blind one
did not? That is a 2×2 over `(baseline verdict, guided verdict)`, and two of
its cells — solved blind and lost under the guidebook, unsolved both times —
exist only if **every** instance goes through all five steps. A chain that
skipped the guidebook and the guided rollout whenever the blind rollout
passed could report the gain but never the regression; the `oracle_failures`
builder, which refuses a resolved run by design, encodes exactly that skip.

### Two rollouts and two gradings in one workflow need telling apart

A workflow entry's key is the task segment of every store key its run
persists (ADR-0007 §6): `<sweep>/<instance>/r<rollout>/<task>/a<attempt>`.
The shipped entries are keyed `rollout` and `unit_test`. Two entries with one
key are refused at declaration (`validate_declaration`), so the naive
concatenation of two `ROLLOUT_AND_UNIT_TEST` pairs does not construct — and
that refusal is the right mechanism, not an obstacle: it is what guarantees the
two rollouts' same-named artifacts (`patch.diff`, `conversation.json`) never
land under one prefix.

### An edge carries a name, not a rename

Workflow edges match an entry's declared inputs to earlier entries' declared
outputs **by store name** (ADR-0007 §5); an explicit binding
`"<producer key>/<input name>"` picks the producer, never a different name.
The Oracle task reads the failure under the sample contract's names —
`failed_conversation.json`, `failed_verdict.json`, `failed_patch.diff`
([`sample.py`](../../src/swe_lab/trace_synthesis/sample.py)) — while a rollout
produces `conversation.json` and `patch.diff`. Nothing can bind one to the
other without either a rename facility in the edge layer or a second set of
input names on the Oracle.

The grading entry also persisted no verdict artifact: its metrics carry the
verdict's scalars (`unit_test.resolved`, `unit_test.score`, the dataset's
counts) and its artifacts are the entryscript and the raw test logs. The
Oracle's brief tells it that the verdict's `summary` names the tests it
failed, and that field was reconstructable only by re-grading a persisted
workspace — which is what the `oracle_failures` builder does.

## Decision

### 1. Phase A returns, as two entries of a new workflow

`from_scratch_guided_trace` is registered beside the existing definitions:

```text
baseline_rollout → baseline_unit_test → oracle_analysis → guided_rollout → guided_unit_test
```

Its first two entries are the shipped `rollout_and_unit_test` pair under new
keys — same tasks, same budgets, same credential — and its last two are the
`oracle_guided_trace` tail under new keys. It runs over a **plain** instance
(`--dataset swebench_pro`, the default), not an `oracle_failures` row.

The spec's paragraph "Phase A is not re-run by the pipeline" is rewritten to
say what is now true: the phase-B entry point stands for a cached failure, and
the from-scratch workflow is the one that pays for phase A because it wants
the pair of verdicts. The two forms coexist; neither replaces the other.

### 2. The chain is unconditional

Every entry runs whatever the baseline verdict was. An instance the blind
rollout solves still gets its guidebook and its guided rollout. The reason is
the reading (context above): the regression cell is a fact about the guidebook
this chain exists to make observable, and it cannot be observed by a chain
that stops early. Nothing in this PR adds a skip flag; a cost-saving conditional
is a later, separate decision, and it will have to say which cell it is
willing to give up.

### 3. The key is the namespace — there is no second one

The five keys are `baseline_rollout`, `baseline_unit_test`, `oracle_analysis`,
`guided_rollout`, `guided_unit_test`. The phase is read off the key, the key is
the store's task segment, and the workflow layer's existing refusal of a
repeated key is the whole guarantee that no artifact of one rollout collides
with the other's. No artifact is renamed, no suffix is appended, no
per-phase namespace is introduced anywhere else: two `patch.diff`s in one run
live at `…/r0/baseline_rollout/a0/patch.diff` and
`…/r0/guided_rollout/a0/patch.diff`, and the store already told them apart.

The one consequence the definition has to spell: by the time the guided
grading binds, two earlier entries produce `patch.diff` and
`patch.base_ref.txt`, and the engine refuses to resolve an unbound name with
two producers. So `guided_unit_test` binds both explicitly to
`guided_rollout`. Every other binding in the definition is written out too,
as documentation, so the graph can be read off the definition rather than
inferred from name matching.

### 4. The Oracle takes the failure by edge, under the producers' own names

`OracleAnalysisTask` gains one boolean, `failure_inputs`. Off (the default,
and the only behaviour before this ADR), the instance stages the failure
under the sample contract's names and the task declares only its brief as an
input. On, the task declares four inputs under the names the solving pipeline
already produces — `conversation.json`, `patch.diff`, `patch.base_ref.txt`
and `unit_test.verdict.json` — briefs the Oracle by those names, and compiles
the grading procedure to apply `patch.diff` against the recorded pre-agent
baseline (ADR-0014), verifying and resetting to it first.

This is the shape the shipped `unit_test` entry set: one task class, standalone
mode fed by the caller or the instance, chain mode fed by an edge, no
special-casing between them. The alternative — a rename facility on edges —
was rejected below. The sample contract is untouched: `oracle_failures`
records and the two existing workflows read exactly what they read before.

### 5. The grading entry persists the verdict whole

`UnitTestParseObserver` declares and emits `unit_test.verdict.json`, the
verdict as `Verdict.facts()` — `resolved`, `score`, `metrics`, `summary` —
the same four keys an `oracle_failures` row's verdict column carries, so a
consumer reads one shape whichever way a verdict reaches it. `facts()` is a
concrete method on the `Verdict` ABC for the reason ADR-0006 gave `resolved`
one: a projection every dataset would otherwise restate.

This is an **additive artifact on every evaluation** — `unit_test`,
`gold_unit_test`, every `*_and_unit_test` chain — and it is not a change to
the report contract: the `AttemptRecord` schema, the workflow record, the
CLI summary and `swe_lab.reporting` are untouched; one more name appears in
an attempt's `artifact_keys`.

### 6. The reading is a 2×2 with an explicit incomplete count

`swe_lab.trace_synthesis.guided_gain` discovers a sweep's runs from its
attempt shards (`Store.read_manifests`) and reads **both verdicts of each
`(instance, rollout)` off that run's workflow record** — the roll-up
`Workflow.execute` writes last for one invocation, naming the run (`run_ts`)
and each entry's final-attempt metrics. The shards themselves are not the
authority on a verdict. A store keeps every shard it was given, so a forced
re-run (`resume=False`, the CLI's default) overwrites `a0` and leaves an older
run's `a1` behind, and a re-run that stopped early leaves the previous run's
downstream shards beside its own fresh upstream ones; reading by highest
attempt number pairs a verdict with a run that no longer exists in the first
case and grades a guidebook the current run never wrote in the second. The
record is the same authority the task runner already uses for itself: its
terminal marker names `run_ts` and the attempts spent, and resume reads that
shard, never the last one on disk.

The record must also be the **current** invocation's, and the engine — not
the reader — makes that true (owner's ruling, 2026-09-06): `Workflow.execute`
**removes the previous invocation's record before its first entry runs** and
writes its own last, on every invocation and not only `resume=False` — a
resumed run that re-runs a failed entry and dies mid-way would otherwise leave
the same stale record behind. Only that one roll-up is removed; shards and
terminal markers are the tasks' own, and resume reads them. So a run killed
anywhere between its first shard and its record leaves shards and **no**
record, and reads as incomplete (`missing workflow.json`); a record that is
present is the latest invocation's own. `Store` gains an idempotent
`delete(key)` for exactly this.

No read-side rule could have established that, and two were tried and
retired during the accepting PR's review (rounds 3 and 4): ordering by
`run_ts` fails because the clock has one-second grain, so two invocations can
share a timestamp; agreement between the record's copied metadata and the
shards fails because a re-run can rewrite a shard's artifacts — a different
`patch.diff` — without moving the metrics and artifact keys the record
copies. Neither is invocation identity.

Four cells are counted — `kept`, `gained`, `regressed`, `unsolved` — plus the
two marginals the chain is run for, **solved at baseline** (kept + regressed)
and **gained with the guidebook**. A run whose record gives no verdict for a
grading key — the entry absent, never run, ended failed, or without a
`*.resolved` metric — or that has no current record (shards from an
invocation that never finished, with or without an earlier invocation's
record still in place) is **incomplete**: counted, named with what it lacks,
and never folded into a cell. The `guided-gain` subcommand prints the JSON (each
pair carrying its `run_ts`) to stdout and the table to stderr, and refuses a
sweep with no runs rather than rendering four zeros.

## Alternatives Considered

### Keep starting at phase B, and document the manual three-step

Rejected. It leaves the pairing the owner asked for as an operator's
bookkeeping across three commands and a hand-built dataset row, and the row
builder's own refusal of a resolved run means the regression cell can never be
assembled that way at all.

### Skip the guidebook and the guided rollout when the baseline passes

Rejected for this PR, and the reason is recorded rather than assumed: the
skip deletes the regression cell, and a reading with three cells cannot say
whether the guidebook hurt. A cost-saving flag is a legitimate later request;
it must be declared, off by default, and its reading must say which pairs were
skipped.

### Namespace the second rollout's artifacts (`guided.patch.diff`, a suffix)

Rejected. The store key already namespaces by task segment; a second scheme
on top of it would be two homes for one fact, and the `unit_test` task's
input name would then have to vary with its position in the chain. The key
does the job, and the engine enforces it.

### Add a rename to edge bindings (`"producer/output as input"`)

Rejected. It changes the edge layer's contract (ADR-0007 §5: edges are
outputs in the store, matched by name), the recorded edge map, and
`_materialize_inputs`, to serve one consumer. The Oracle declaring the
producers' names is the shape the codebase already uses for the grading
entry, and it costs the Oracle one boolean and a second brief builder.

### Have the Oracle re-grade the persisted workspace instead of persisting a verdict artifact

Rejected. That is what the `oracle_failures` builder does host-side, and it
needs the grading workspace on disk, the dataset's grader, and a run
directory — none of which an edge can carry into a sandbox. Persisting the
verdict is one inline artifact, and it is useful to every consumer of a
grading record, not only this chain.

### Take each grading's verdict from its highest-numbered attempt shard

Rejected — it was the first cut, and the review of the accepting PR showed
on a real `FilesystemStore` what it gets wrong: an old run's baseline
`a0=fail, a1=pass` and guided `a0=pass`, then a forced re-run overwriting both
`a0` shards with fail / fail, reads back as new-`a0`, old-`a1`, new-`a0` and
reports `regressed` where the current run is `unsolved`. The task runner
solved the same problem for itself with its terminal marker; the workflow
record is that marker one level up, and the reading now uses it. Two
regression tests pin both shapes (the outlived attempt, and a stopped-early
re-run beside the previous run's downstream shard) through the real engine.

### Read-side generation tests: timestamp order, then metadata agreement

Tried in review rounds 3 and 4 of the accepting PR and retired on real-store
control arms. Ordering by `run_ts` cannot separate two invocations launched
in the same second (`persist_wiring.run_ts` has one-second grain); requiring
the record's copied `metrics` / `artifact_keys` to match the shards at its
recorded attempts cannot see a re-run that rewrote a rollout's artifacts
under identical metadata before it was killed. Metadata equality is not
invocation identity; only the engine can make a present record mean
"current", which is the decision above.

### A per-invocation generation signal on the shards and the record

Not adopted. A unique invocation id on every shard and on the record would
also close the class, and would additionally give a pair a traceable
identity — but it changes the shape of `AttemptRecord` and of the workflow
record, which is the report contract's and is ask-first, while retiring the
record at the start of an invocation closes the same class with no shape
change. Left as the follow-up if provenance on the shards is ever wanted for
its own sake.

### Have the runner clear the previous record when a forced run starts

**Adopted, widened to every invocation** — this is the decision in §6. The
first cut of the accepting PR rejected it as buying nothing over a read-side
test; the review's control arms showed the read-side tests cannot establish
identity at all, and the owner ruled for this mechanism on 2026-09-06.

### A `Rate` line (ADR-0015 §5 / ADR-0016) for the marginals

Not adopted, deliberately. `Rate` reports a rate over counted runs with its
excluded (ours) and unclassified counts; the 2×2 is a pairing over runs that
both entries graded, and its incomplete set is "no verdict for one half", not
"the actor's ending was ours". Rendering the incomplete count in `Rate`'s
excluded slot would misname it. The reading keeps counts and prints the
incomplete count — including when it is zero — for the same reason `Rate`
prints its counts: an absent measurement must not read as a low one.

## Consequences

- A downstream consumer can run the whole pipeline on a plain instance with
  one command and read the pairing with another:

  ```sh
  python -m swe_lab run from_scratch_guided_trace <instance_id> --persist --sweep <id>
  python -m swe_lab guided-gain <id>
  ```

- Every evaluation record gains one artifact, `unit_test.verdict.json`;
  nothing that reads records by name is affected, and the report contract
  is unchanged.
- `OracleAnalysisTask.failure_inputs` is a public field; the two existing
  workflows leave it at its default and are byte-identical in what they stage
  and brief (the pinned brief digest is unchanged).
- The `_segmented_rollout`, rollout and grading entries are built by small
  factories taking a key, so a future chain that needs a third solve + grade
  pair adds a key rather than a copy.
- **The reading needs the workflow record, and a present record is
  current.** The engine removes the previous invocation's record before the
  first entry runs and writes its own last, so a run killed in between reads
  as incomplete, `missing workflow.json`. A run persisted by anything other
  than `Workflow.execute` (shards written by hand) reads as incomplete too,
  by design; a store written *before* this rule can still hold an earlier
  invocation's record beside newer shards, and nothing read-side can tell.
- **`Store` gains `delete(key)`**, idempotent — a missing key is not an
  error. `FilesystemStore` and `FakeStore` implement it; a future vendor (the
  S3 store of task 13) must.
- **What this does not decide.** Whether the guidebook helps is still the
  empirical question ADR-0018 left open; this chain produces the pair of
  verdicts that question needs and claims nothing about their difference.
  The policy stamp on phase-B / phase-C records (spec §14, task 07) is not
  shipped here either — a from-scratch run's records are as unmarked as an
  `oracle_guided_trace` run's, and pooling them with benchmark numbers is as
  wrong as it was.

### Spec reconciliation in the accepting PR

- **§3, Phase A:** the paragraph "Phase A is not re-run by the pipeline" is
  rewritten to state both entry points and which workflow is which.
- **§13:** row A names `from_scratch_guided_trace` as the form that runs
  phase A, beside the skipped form.
- **§15 / §16:** re-checked. No success criterion is met or invalidated —
  the chain makes the pass@1 pairing measurable and measures nothing — and
  nothing on the out-of-scope list ships. Stated in the spec rather than
  assumed.
