# Task 21 — Workflow: the declared list, edges from the store, CLI rewire

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Implements ADR-0007 §§5, 9–10 on top of Task 20's `run_task`. After this
> task, both CLIs run workflows; `run_rollout` / `run_unit_test` are
> deprecated compat shims.

---

## 1. Purpose & scope

Give the chain a name: a **workflow** is a declared, ordered list of
`(key, task)` entries over one `(sweep, instance, rollout)`; edges are store
artifacts matched **by name** between one task's declared outputs and a later
task's declared inputs; execution is resume-aware, all-or-nothing, and leaves
a derived workflow record.

### In scope

- `Workflow` in `workflow/workflow.py`: declaration, **static validation at
  construction**, execution, the derived record.
- Edge resolution — including the answer to "two upstream tasks produce the
  same output name" (§3).
- Edge-input validation as the **distinct** failure of ADR-0007 §5.
- CLI rewire: `rollout --grade` = a two-task workflow; `eval` = a one-task
  workflow; wrappers marked deprecated.
- Closing ADR-0007's §2.6 question (instance mounts vs the eval trio) —
  resolved as *won't-move*, §6.

### Out of scope

- A DAG / parallel branches (list first — ADR-0007 §9); `optional` tasks
  (§10, deferred until an auxiliary task exists); serialized workflow format.
- `pipelines/related_files` migration — it is the abstraction's acid test
  and runs **after** this lands, as its own task.
- Deleting the wrappers or the in-run eval retry (deprecation first; removal
  + the ADR-0005-superseding ADR when the wrappers go).

---

## 2. Declaration

```python
@dataclass(frozen=True)
class WorkflowEntry:
  """One step: a key naming it in the store, the task, and explicit bindings.

  Attributes:
    key: The task segment of the ADR-0004 key (`[a-z0-9_-]+`, unique in the
      workflow). Also the retry-budget owner: change the task, change the key
      (ADR-0007's manual identity discipline).
    task: The constructed task (declaration data — Task 19).
    inputs: Explicit edge bindings, input name → producing entry's key. Only
      needed where name matching alone is ambiguous (§3); an entry may also
      bind an input that matching would have resolved, for documentation.
    retries: Task-level retry budget for this entry (Task 20's `run_task`).
  """
  key: str
  task: Task
  inputs: Mapping[str, str] = field(default_factory=dict)
  retries: int = 0


@dataclass(frozen=True)
class Workflow:
  store: Store
  sweep_id: str
  rollout_id: int
  entries: Sequence[WorkflowEntry]
  # sandbox construction is the caller's, per entry — injected as a factory
  # factory(entry) -> Callable[[], Sandbox], so backend/network/pull knobs
  # never enter this class
  sandbox_factory: Callable[[WorkflowEntry], Callable[[], Sandbox]]
```

The caller owns the topological order (it is a list); the instance comes from
each task's own binding (`task.instance`), and one workflow runs one
`(sweep, instance, rollout)` — mixed-instance workflows are not a case we
have.

Usage, the shape the ADR promised:

```python
wf = Workflow(
    store=store, sweep_id="s1", rollout_id=0,
    entries=[
        WorkflowEntry("rollout", CodingAgentTask(instance=inst, harness=h)),
        WorkflowEntry("eval", UnitTestEvalTask(instance=inst, patch=UPSTREAM),
                      retries=1),
    ],
    sandbox_factory=...,
)
outcome = wf.execute(output_dir=..., timeout=..., run_ts=...)
```

## 3. Edge resolution — and the same-name question, settled

All resolution is **static**, at `Workflow` construction, before anything
runs. For every entry `E` and every `ArtifactSchema` in
`E.task.input_schema()`:

1. **Explicit binding wins.** If `E.inputs[name]` names an earlier entry,
   that entry's `output_schema` must declare `name` — else construction
   fails (`"eval binds patch.diff to rollout, which does not declare it"`).
   A binding to a later or unknown key also fails.
2. **Otherwise match by name** over *earlier* entries' merged
   `output_schema()`s (derivable at declaration time — Task 19 made schemas
   construction-time data):
   - exactly one producer → that is the edge;
   - **zero** producers → construction fails: the workflow cannot ever
     satisfy the input;
   - **two or more** producers → construction fails with the candidates
     listed, and the fix is an explicit binding. **Never nearest-wins, never
     first-wins** — the repo's merge rules (mounts, output schemas) already
     refuse to guess on duplicates, and an edge silently bound to the wrong
     producer is the worst kind of wrong answer.

Why the binding lives on the **entry, not the task**: a task is declaration
data reusable in any workflow — it knows *what* it consumes (`patch.diff`),
not *who* produces it; producer keys are workflow-topology knowledge, so the
workflow declaration is where they are written. (Store-side disambiguation
needs nothing at all: Task 20's key layout separates same-named artifacts by
the task segment.)

The static check also validates: entry keys unique and well-formed; every
task's observers compose (schema merge raises here, not mid-run).

## 4. Execution

Per entry, in declared order — thin around Task 20's `run_task`:

```
1. RESOLVE INPUTS (only for entries with an input_schema)
   producer_record = the producing entry's final RunRecord — from this
     process's own run_task outcome, or (resume) read back via
     read_manifest(..., task=producer.key)
   key = producer_record.artifacts[name]     # full store key, never guessed
   store.get(key, staging/<entry.key>/<name>)
   EDGE VALIDATION (ADR-0007 §5): the staged file must exist and be
     non-empty. A required input that is missing or empty fails the ENTRY
     with the distinct edge status — no sandbox is built, no retry budget
     spent (an empty patch is caught here, not paid for in a container).
   extra_mounts[name] = Mount(LocalFile(staged), read_only=True)

2. RUN: outcome = run_task(entry.task, store=store, address=(sweep,
      rollout, entry.key), sandbox_factory=factory(entry),
      retries=entry.retries, extra_mounts=extra_mounts, ...)
   — run_task handles resume/attempts/validation/marker (Task 20).

3. GATE (all-or-nothing, ADR-0007 §10): outcome FAILED (fresh or resumed)
   or an edge failure → every remaining entry is BLOCKED (not attempted),
   the workflow fails.

4. AFTER ALL ENTRIES SUCCEED: derive the workflow record — a roll-up of the
   entries' final RunRecords (keys, statuses, attempt counts, edge map) —
   and write it LAST, atomically:
     <sweep>/<instance>/r<rollout>/workflow.json
   Its absence means the workflow did not complete (ADR-0007 §10); v1
   records success only, and a failed workflow's re-entry costs one marker
   read per already-terminal task.
```

`WorkflowOutcome` reports per-entry results (`succeeded` / `failed` /
`blocked` / `edge_failed` / `resumed`), the failing entry if any, and the
record. Edge failure is its own value — a broken edge and a failed
evaluation call for opposite responses.

## 5. CLI rewire, wrapper deprecation

- `swe-lab rollout --grade` builds the two-entry workflow above (persist on
  → real store; persist off → a throwaway `FilesystemStore` under the
  workspace, so the edge machinery is identical either way and `--grade`
  stops hand-carrying the patch).
- `swe-lab rollout` (no grade) and `swe-lab eval` are one-entry workflows.
- `--eval-retries` maps to the eval entry's task-level budget with in-run
  `retries=0` (Task 20 §7); the flake-absorption semantics move up a level,
  summaries keep reporting `attempts` / `flaky` from the records.
- `run_rollout` / `run_unit_test`: `DeprecationWarning` + docstring pointer;
  behavior untouched. Removal (with the in-run retry loop and the
  ADR-0005-superseding ADR) is a later task, once downstream confirms
  migration.

## 6. Closing §2.6: the eval trio does not move to `instance.mounts()`

Settled as **won't-move**, for the two reasons recorded in Task 19's Result
note: (a) spec-holding callers (the wrappers' whole clientele) would strand,
and (b) `CodingAgentTask.mounts()` merges `instance.mounts()`, so the trio —
including the held-out `required_tests.json` — would be staged into
**solving** runs: an evaluation-integrity leak. The compiled spec remains the
self-contained carrier of eval material; `TaskInstance.mounts()` remains the
home of *solving-run* material (empty for SWE-Bench Pro). ADR-0007 §2's
mount-source table gets a one-line amendment note saying exactly this.

## 7. Steps

1. `WorkflowEntry` + `Workflow` + static validation (unit tests: unique
   keys, zero-producer, ambiguity → error, explicit binding resolves,
   binding to non-declaring/later entry → error).
2. Edge staging + validation (FakeStore + FakeSandbox: missing input, empty
   input → edge failure, no sandbox built; resolved input mounted
   read-only; resumed producer's record read back from the store).
3. Execution loop over `run_task`: all-or-nothing gate, blocked entries,
   workflow record derived + written last (assert write order), resume
   re-entry does no work on a terminally failed workflow.
4. CLI rewire + live smoke: `rollout --grade` end-to-end through the
   workflow on the flipt parity instance; summary parity with the pre-rewire
   output (fields, not bytes — records now carry task keys).
5. Wrapper deprecation warnings + docs (`conventions.md` command examples
   unchanged; `plans/README.md` statuses).

## 8. Risks

- **The CLI rewire is the behavior-sensitive half** (persisted record shape
  under `--grade` changes: two task-keyed records instead of one flat one).
  Mitigation: summary JSON keeps its fields; the record change is the
  amendment's point and is called out in the PR.
- **Throwaway store for unpersisted runs** doubles writes for large
  artifacts (workspace + store copy). Accepted for uniformity; revisit only
  if a real artifact is big enough to notice.
- **List order is the caller's** until a DAG exists (ADR-0007 §9, accepted
  there).
