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
  """One step: a key naming it in the store, the task, and how it runs.

  Attributes:
    key: The task segment of the ADR-0004 key (`[a-z0-9_-]+`, unique in the
      workflow). Also the retry-budget owner: change the task, change the key
      (ADR-0007's manual identity discipline).
    task: The constructed task (declaration data — Task 19).
    sandbox_factory: Builds this entry's sandbox — called once per attempt
      (Task 20's contract: every call returns a sandbox over a fresh, empty
      workspace; the factory owns that allocation). Per entry, so backend /
      network / pull knobs stay the caller's and two entries can differ.
    inputs: Explicit edge bindings, each `"<producer key>/<input name>"`
      (`"rollout/patch.diff"`) — the producer's key followed by the input's
      store name, mirroring how the artifact reads in the store itself. Only
      needed where name matching alone is ambiguous (§3); an entry may also
      bind an input that matching would have resolved, for documentation.
    retries: Task-level retry budget for this entry (Task 20's `run_task`).
  """
  key: str
  task: Task
  sandbox_factory: Callable[[], Sandbox]
  inputs: Sequence[str] = ()
  retries: int = 0


@dataclass(frozen=True)
class Workflow:
  store: Store
  sweep_id: str
  rollout_id: int
  entries: Sequence[WorkflowEntry]
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
        WorkflowEntry("rollout", CodingAgentTask(instance=inst, harness=h),
                      sandbox_factory=rollout_sandbox),
        WorkflowEntry("eval", UnitTestEvalTask(instance=inst),
                      sandbox_factory=eval_sandbox, retries=1),
    ],
)
outcome = wf.execute(output_dir=..., timeout=..., run_ts=...)
```

## 3. Input resolution, exactly — declaration to bytes in the workspace

One naming contract underlies all of it, worth stating before the
algorithm:

> **An input name is three things at once**: the name in the producer's
> `output_schema` (what `merge_output_schemas` de-duplicates), the artifact
> name in the store (`record.artifacts` key, last segment of the full store
> key), and the **workspace-relative mount target** the consuming task's
> script reads (`git apply "$SANDBOX_WORKSPACE"/patch.diff`). No renaming
> at any hop — the identity *is* the contract, which is why matching by
> name is sufficient and why a resolved input lands exactly where the
> script expects it.

Resolution has two phases: **A (static)** turns names into producer keys at
`Workflow` construction; **B (runtime)** turns producer keys into staged
bytes right before the consuming entry runs.

### Phase A — static: name → producing entry (at construction, nothing runs)

```python
def _resolve_edges(entries) -> dict[str, dict[str, str]]:
  """entry.key → {input name → producer entry.key}; raises on any ambiguity."""
  edges: dict[str, dict[str, str]] = {}
  produced: dict[str, list[str]] = {}   # name → keys of entries declaring it
  for entry in entries:                  # declared order == topological order
    bound: dict[str, str] = {}
    declared_inputs = {s.name: s for s in entry.task.input_schema()}
    # (a) parse "producer/name" bindings; a binding for an input the task
    #     never declared is dead — an error, not ignored: it means the
    #     author believes something false. Split on the FIRST "/": entry
    #     keys are [a-z0-9_-]+ (never contain one), names may contain dots.
    explicit: dict[str, str] = {}        # input name → producer key
    for binding in entry.inputs:
      producer, sep, name = binding.partition("/")
      if not sep or not producer or not name:
        raise WorkflowError(f"{entry.key}: malformed binding {binding!r}; "
                            'expected "<producer key>/<input name>"')
      if name not in declared_inputs:
        raise WorkflowError(f"{entry.key} binds {name!r}, which its task "
                            "does not declare as an input")
      if name in explicit:
        raise WorkflowError(f"{entry.key} binds {name!r} twice")
      explicit[name] = producer
    for name in declared_inputs:
      if name in explicit:
        # (b) EXPLICIT BINDING WINS — and is verified, not trusted:
        producer = explicit[name]
        if producer not in produced.get(name, []):
          # unknown key, a LATER entry, or an entry not declaring the name
          raise WorkflowError(f"{entry.key} binds {name!r} to {producer!r}, "
                              "which is not an earlier producer of it")
        bound[name] = producer
      else:
        # (c) MATCH BY NAME over earlier entries' declared outputs
        candidates = produced.get(name, [])
        if len(candidates) == 1:
          bound[name] = candidates[0]
        elif not candidates:
          raise WorkflowError(f"no earlier entry produces {name!r}, "
                              f"required by {entry.key}")
        else:  # >= 2 — NEVER nearest-wins, never first-wins
          raise WorkflowError(f"{name!r} is produced by {candidates}; "
                              f"bind it explicitly on {entry.key}")
    edges[entry.key] = bound
    # this entry's declared outputs become available to LATER entries
    for schema in merge_output_schemas(
        *(o.output_schema() for o in entry.task.observers())):
      produced.setdefault(schema.name, []).append(entry.key)
  return edges
```

Properties, spelled out:

- **Everything here is declaration data** (Task 19): `input_schema()` is a
  fixed function of task configuration, and a task's output schema derives
  from its observers at construction time — so the whole edge map is
  computable, and *checked*, before any sandbox exists. A workflow that
  would ever dangle is refused at declaration.
- **Only earlier entries are candidates** — the list is the topological
  order (ADR-0007 §9), so a forward or self edge cannot even be expressed.
- **Ambiguity is an error, not a policy**: two earlier producers of one
  name fail construction with both keys named; the fix is one line of
  explicit `inputs=`. Same DNA as `merge_mounts` / `merge_output_schemas`
  — the repo refuses to guess on duplicates everywhere.
- Why the binding lives on the **entry, not the task**: a task is reusable
  declaration data — it knows *what* it consumes, not *who* produces it;
  producer keys are topology knowledge, so the workflow declaration is
  where they are written. (Store-side, same-named artifacts never collide
  at all: Task 20's key layout separates them by the task segment.)
- The static pass also validates: entry keys unique and `[a-z0-9_-]+`; each
  task's observer schemas merge cleanly (raises here, not mid-run).

### Phase B — runtime: producer key → staged bytes (per entry, just before it runs)

For entry `E`, for each `(name → producer_key)` in `edges[E.key]`:

```
b1. THE PRODUCER'S FINAL RECORD — never a guessed key:
      ran this process  → the producer's run_task outcome already holds its
                          final AttemptRecord
      resumed           → shards = store.read_manifest(sweep, instance,
                          rollout, task=producer_key); take the highest
                          attempt — the marker guarantees it is the final one
    The record is the authority because only IT knows which attempt was
    final; assembling `<sweep>/<instance>/r<rollout>/<producer>/a?/<name>`
    by hand would have to re-derive that.

b2. LOOK UP THE ARTIFACT: full_key = record.artifacts.get(name)
      absent → the producer succeeded but never registered this name (a
      best-effort output that did not land) → required: EDGE FAILURE;
      optional: skip (not mounted; noted in the workflow record).

b3. FETCH: store.get(full_key, staging) where
      staging = <output_dir>/edges/<E.key>/<name>
    Always through Store.get onto a host-side staging path — never a
    LocalFile aimed into the store's internals — so an object store (R2/S3)
    works unchanged.

b4. VALIDATE THE BYTES (ADR-0007 §5): staged file exists and is non-empty.
      required + missing/empty → EDGE FAILURE (the empty patch is caught
      here — no container is spent on it); optional → skip as in b2.

b5. MOUNT: extra_mounts[name] = Mount(LocalFile(staging), read_only=True)
      target = the input name itself (the naming contract above), so the
      bytes land at the exact workspace path the task's script reads.
```

**Edge failure semantics**: the entry's outcome is `EDGE_FAILED` — distinct
from a task failure (ADR-0007 §5: a broken edge and a failed evaluation
call for opposite responses). No sandbox is built, no retry budget is
spent, no attempt is persisted, and **no terminal marker is written**
(nothing ran; a re-entry re-checks the edge, so a store repaired by hand —
or a corrected workflow — can proceed where a terminal marker would have
blocked forever). The workflow then fails per §4's gate.

**The end-to-end walk for the shipped pair**, every hop explicit:

| hop | rollout → eval, `patch.diff` |
|---|---|
| producer declares | `DiffExtractObserver.output_schema()` → `patch.diff` (required) — part of `CodingAgentTask.observers()` |
| consumer declares | `UnitTestEvalTask(apply_patch=True).input_schema()` → `patch.diff` (required) |
| phase A | sole earlier producer of `patch.diff` is entry `"rollout"` → edge `eval.patch.diff ← rollout` (an explicit `inputs=("rollout/patch.diff",)` would also be accepted, and required if a second patch-producing entry ever precedes eval) |
| producer runs | attempt `a0` persists `s1/<inst>/r0/rollout/a0/patch.diff`; its `AttemptRecord.artifacts["patch.diff"]` holds exactly that key |
| phase B | record lookup → `store.get(key, out/edges/eval/patch.diff)` → non-empty ✓ → `extra_mounts={"patch.diff": Mount(LocalFile(...), read_only=True)}` |
| consumer runs | the file sits at `$SANDBOX_WORKSPACE/patch.diff`; the compiled entryscript's `git apply` reads it; `execute`'s required-input check would have refused to build the sandbox had the mount been missing |

## 4. Execution

Per entry, in declared order — thin around Task 20's `run_task`:

```
1. RESOLVE INPUTS: phase B above (phase A already ran at construction) →
   extra_mounts; a required-input edge failure fails the entry here, before
   any sandbox exists.

2. RUN: outcome = run_task(entry.task, store=store, address=(sweep,
      rollout, entry.key), sandbox_factory=entry.sandbox_factory,
      retries=entry.retries, extra_mounts=extra_mounts, ...)
   — run_task handles resume/attempts/validation/marker (Task 20).

3. GATE (all-or-nothing, ADR-0007 §10): outcome FAILED (fresh or resumed)
   or an edge failure → every remaining entry is BLOCKED (not attempted),
   the workflow fails.

4. AFTER ALL ENTRIES SUCCEED: derive the workflow record — a roll-up of the
   entries' final AttemptRecords (keys, statuses, attempt counts, edge map) —
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
