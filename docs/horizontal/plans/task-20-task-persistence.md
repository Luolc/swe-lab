# Task 20 — Task-keyed persistence: records, validation, retry, resume

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Implements ADR-0007 §§6–7 and the key amendment to ADR-0004. Workflows
> (the list, edges, CLI rewire) are Task 21 — this task builds the
> **single-task orchestration** Task 21 loops over.

---

## 1. Purpose & scope

Make one task's execution durable and repeatable: every attempt persisted
under a **task-keyed** store prefix, outputs **validated** against the task's
declared schema, failed attempts **retried** with a fresh sandbox, and a
completed task **resumable** (skipped, or recognized as terminally failed) by
a later process.

### In scope

- ADR-0004 key amendment: the `task` component, in the key and on
  `RunRecord` — **final shape directly, no compatibility layer**: everything
  in today's stores is debug-stage output, discarded rather than migrated.
- The terminal marker: format, location, atomic write, read-back.
- `run_task(...)` — the per-task orchestrator: resume check → attempt loop
  (execute → validate → persist → retry) → terminal marker. This is the
  single unit Task 21's workflow calls once per entry.
- Output validation against `TaskResult.output_schema` (`required` stops
  being advisory).
- Task-level retry with a fresh sandbox per attempt (via a sandbox
  *factory*), absorbing both validation failures and infra failures.

### Out of scope

- The workflow list, edge resolution, input mounting, CLI rewire (Task 21).
- Removing the in-run eval retry loop. It **stays** in this task: the frozen
  wrappers receive a single constructed sandbox and cannot re-execute, so
  they cannot adopt task-level retry. The loop dies **with the wrappers**
  (post-Task-21 deprecation), at which point a new ADR supersedes ADR-0005 —
  see §7.
- Fingerprint-based task identity (ADR-0007 leaves it deferred; key
  discipline stays manual).

---

## 2. The store layout (amends ADR-0004)

### 2.1 The key gains a task segment

```
runs/                                  ← store root namespace (unchanged)
  <sweep>/<instance>/r<rollout>/<task>/a<attempt>/<artifact>
  <sweep>/<instance>/r<rollout>/<task>/a<attempt>/run.json      ← record shard
  <sweep>/<instance>/r<rollout>/<task>/complete.json            ← terminal marker
```

- `<task>` is the **workflow-entry key** (Task 21's `(key, task)` pair —
  `"rollout"`, `"eval"`), caller-chosen, `[a-z0-9_-]+`. It sits **between
  rollout and attempt** because the task owns its attempts (ADR-0007 §6): two
  attempts of the eval task are `eval/a0`, `eval/a1`, unrelated to the
  rollout task's `rollout/a0`.
- **This is the answer to the "two tasks, same output name" question on the
  write side**: a workflow whose task 1 and task 2 both produce `patch.diff`
  writes `r0/task1/a0/patch.diff` and `r0/task2/a0/patch.diff` — distinct
  keys by construction, no store-side convention needed beyond the segment.
  (The read side — *which* one a consumer takes — is edge resolution,
  answered in Task 21 §3; short version: ambiguity is a declaration-time
  error resolved by an explicit binding, never a guess.)
- The marker lives at the **task** prefix (not per attempt): "is this task
  terminal" is one question per task, and attempts underneath it are the
  history of getting there.

### 2.2 `RunRecord` gains `task` (required)

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RunRecord:
  sweep_id: str
  instance_id: str
  rollout_id: int = 0
  task: str               # NEW, required — every record names its task
  attempt: int = 0
  ...                     # everything else unchanged
```

- **Final shape directly, no default and no compatibility path**: we are
  prototyping and every existing store is debug output — old shards are
  discarded, not migrated, so `from_json` may simply fail on them and
  `run_prefix` always emits the segment. `sort_key` gains `task` (between
  `rollout_id` and `attempt`).
- `Store.read_manifest(sweep, instance, rollout)` gains
  `task: str | None = None` — `None` reads all shards of the rollout (the
  aggregation shape); a value narrows to one task's attempts, which is what
  the resume/retry check reads. `FilesystemStore` / `FakeStore` updated; a
  downstream `Store` subclass must add the parameter (flagged in the PR).

### 2.3 The terminal marker

```
<sweep>/<instance>/r<rollout>/<task>/complete.json
{
  "outcome": "succeeded" | "failed",
  "attempts": <int>,            # how many were spent (last index + 1)
  "run_ts": "<injected launch timestamp>"
}
```

- **Written last**, after the final attempt's artifacts and record shard are
  durable (ADR-0007 §7: a crash before the marker re-runs the task; a crash
  after it loses nothing).
- **Atomic**: `FilesystemStore` writes to a temp name in the same directory
  and renames; a future object store gets atomicity from single-PUT
  semantics. `Store` gains `put_bytes(key, data)` for this (marker content
  is constructed, not a host file).
- **Absence means "not terminal"** — a task killed mid-run leaves attempts
  and shards but no marker, and resume simply runs it again from scratch.
  Both outcomes are terminal: `failed` blocks, it is never re-entered
  (ADR-0007 §7 — a permanently failing task must not burn a budget per
  resume).

---

## 3. Validation, and the one retry callback

The layer owns one rule and the task owns one hook.

**The layer's rule** — `outputs_valid(result)`, a public module function —
decides the terminal marker, uniformly:

```python
def outputs_valid(result: TaskResult) -> bool:
  """The attempt produced what the task declared it would."""
  if result.run.status is not RunStatus.SUCCESS:
    return False                       # TIMEOUT / RUN_ERROR / SETUP_ERROR
  produced = result.run.artifacts      # canonical name → host path
  return all(
      schema.name in produced
      for schema in result.output_schema
      if schema.required
  )
```

This is where `ArtifactSchema.required` (advisory since Task 19) becomes
enforced, and the invariant gets its named test
(`test_a_missing_required_output_fails_the_attempt`).

**The task's hook** — the single retry callback, overridable, handed the
whole `TaskResult` (artifacts as host paths, metrics, the composed observers
with their typed results):

```python
class Task:
  ...
  def should_retry(self, result: TaskResult) -> bool:
    """Given everything this attempt produced, does it need another one?

    Default: retry exactly when the outputs are invalid (a required output
    missing, or the run not SUCCESS — infra failures land here too). A
    subclass composes its own judgment on top of the artifacts and typed
    results::

        # eval: absorb flakes — an unresolved verdict might be harness noise
        def should_retry(self, result):
          return super().should_retry(result) or not self._parse.verdict.resolved

        # a rollout task could inspect specific outputs just as finely,
        # e.g. retry an empty patch or an agent that never completed
    """
    return not outputs_valid(result)
```

**Retry-desire is not failure.** The terminal marker keys off
`outputs_valid` of the *final* attempt, never off `should_retry`: an eval
that exhausts its budget still unresolved has produced a legitimate verdict
— the task **succeeded** and the answer is "not resolved". Tying the marker
to the callback would fail the workflow for every genuinely failing patch.

## 4. `run_task` — the per-task orchestrator, and the full write path

New in `workflow/run_task.py`. The signature (the sandbox arrives as a
**factory** because each attempt needs a fresh one — ADR-0007 §6):

```python
@dataclass(frozen=True)
class TaskAddress:
  """Where one task's runs live in the store (the ADR-0004 key, sans attempt)."""
  sweep_id: str
  rollout_id: int
  task: str                 # the workflow-entry key; validated [a-z0-9_-]+

def run_task(
    task: Task,
    *,
    store: Store,
    address: TaskAddress,
    sandbox_factory: Callable[[], Sandbox],   # fresh sandbox per attempt
    output_dir: epath.PathLike,               # host side; per-attempt subdirs
    timeout: float,
    retries: int = 0,                         # extra attempts after the first
    run_ts: str,                              # injected, never read inside
    backend: str = "",
    model: str = "",
    extra_mounts: Mounts | None = None,       # Task 21 feeds resolved inputs
    extra_observers: Sequence[SandboxObserver] = (),
) -> TaskRunOutcome
```

Step by step — **this is the persistence walk-through**, exact and in order:

```
0. RESUME CHECK (read side, before anything runs)
   marker = read complete.json at <sweep>/<instance>/r<rollout>/<task>/
   ├─ "succeeded" → return TaskRunOutcome(resumed=True, outcome=SUCCEEDED,
   │                record=last attempt's shard). No sandbox is built.
   ├─ "failed"    → return TaskRunOutcome(resumed=True, outcome=FAILED).
   │                Never re-run (terminal is terminal).
   └─ absent      → fall through; any attempt shards lying around are a
                    previous process's death throes — IGNORED, and this run's
                    attempts overwrite from a0 (deterministic overwrite is
                    ADR-0004's existing rule for re-runs).

1. ATTEMPT LOOP — for attempt in 0..retries:
   a. sandbox = sandbox_factory()          # fresh container, every attempt
   b. result  = task.execute(sandbox,
                    output_dir=<output_dir>/a<attempt>,   # host files kept
                    timeout=timeout,
                    extra_mounts=..., extra_observers=...)
   c. valid = outputs_valid(result)          # §3 — the layer's rule
   d. PERSIST THE ATTEMPT — success or not (failures are evidence):
        prefix = <sweep>/<instance>/r<rollout>/<task>/a<attempt>
        for name, host_path in result.run.artifacts:
            store.put(f"{prefix}/{name}", host_path)
        record = RunRecord(..., task=address.task, attempt=attempt,
                           status=result.run.status.value,
                           artifacts={name: full_key, ...},
                           metrics=result.run.metrics,
                           extra={"outputs_valid": valid, ...})
        store.append_manifest(record)        # the shard: <prefix>/run.json
   e. if not task.should_retry(result): break   # §3 — the task's one hook
      if attempt == retries: break              # budget spent
      # else: next attempt — a fresh sandbox; nothing carries over

2. TERMINAL MARKER (write side, last, atomic)
   outcome = "succeeded" if outputs_valid(last_result) else "failed"
   store.put_bytes(<task-prefix>/complete.json, marker_json)   # atomic
   return TaskRunOutcome(resumed=False, outcome=..., result=last_result,
                         record=last_record, attempts=attempt+1)
```

- **What each attempt costs**: a fresh sandbox (image already local → seconds
  of setup; the warm-container advantage of ADR-0005's in-run loop is
  deliberately given up in exchange for one retry mechanism instead of two
  and stronger isolation between attempts). Recovery data (ADR-0005: nearly
  all flakes recover in one retry) was measured on warm re-runs; fresh
  sandboxes only isolate harder, so the rate should hold or improve.
- **Infra failure and validation failure share the budget** (ADR-0007 §6):
  `SETUP_ERROR` is just an invalid attempt.
- **Preemption costs nothing**: retries are counted by the surviving
  orchestrator; a killed process writes neither shard nor marker for the
  in-flight attempt, and resume re-runs from scratch (§0).
- **Flaky signal**: `flaky` at task level = `valid ∧ resolved` at
  `attempt > 0` with an earlier unresolved attempt — derivable from the
  attempt shards; recorded in the final record's `extra` so readers do not
  re-derive it.

## 5. The read side (what a later process does with all this)

| reader | reads | via |
|---|---|---|
| **resume** (this task) | the terminal marker | `read_marker(store, address)` — §4 step 0 |
| **retry accounting** | one task's attempt shards | `store.read_manifest(sweep, instance, rollout, task=...)` |
| **a downstream task** (Task 21) | the *producing* task's final record → `artifacts[name]` → full store key → `store.get(key, staging_dir)` → `Mount(LocalFile(staged), read_only=True)` | the workflow edge; never `LocalFile` into the store's internals, so an S3 store works unchanged |
| **aggregation** (`index`, pass@K) | all shards of a sweep | `read_manifests` — unchanged in shape; shards now sort by `(instance, rollout, task, attempt)` |

The downstream row is deliberately spelled out here even though the workflow
lands in Task 21: **artifact resolution goes through the record, not through
key construction**. A consumer takes `record.artifacts["patch.diff"]` (a full
key the producer wrote) rather than assembling
`<sweep>/<instance>/r0/rollout/a?/patch.diff` itself — the record knows which
attempt was final; a key-guessing consumer would have to re-derive that.

## 6. CLI wiring (minimal in this task)

`--persist` on both CLIs keeps working through `persist_run` and now stamps
the task segment: `rollout` runs write `task="rollout"`, `eval` runs
`task="eval"` (constants beside the CLI, not magic strings). They still
persist exactly one attempt (the CLIs have no retry loop until Task 21 moves
them onto workflows); the records simply become addressable next to future
workflow-written ones. No signature changes.

## 7. What happens to the in-run eval retry (direction, not this task)

The in-run loop (`_attempt_until_resolved`) stays untouched. After Task 21
rewires the CLIs onto workflows, the workflow path runs `UnitTestEvalTask`
with in-run `retries=0` and a task-level budget instead; the loop then serves
only the deprecated wrapper, and both leave together — at which point a new
ADR supersedes ADR-0005 with the task-level semantics of §4. Doing it now
would leave the frozen wrapper (single injected sandbox, no factory) without
a retry mechanism.

## 8. Steps

1. `RunRecord.task` + `run_prefix` + `sort_key`; `read_manifest(task=...)`;
   `Store.put_bytes`; `FilesystemStore`/`FakeStore` updated. No migration:
   existing debug stores are discarded (prototyping — final shape directly).
2. Marker write/read (`write_marker` atomic, `read_marker`), tests incl. the
   torn-write case (temp file present, no marker → not terminal).
3. `outputs_valid` + `Task.should_retry` + named invariant tests.
4. `run_task` with `FakeSandbox` factories: resume-skip, resume-blocked,
   retry-on-validation-failure, retry-on-infra-failure, budget exhaustion →
   `failed` marker, a `should_retry` override absorbing flakes, per-attempt persistence
   (every attempt has a shard; marker written last — assert store write
   order).
5. CLI `--persist` stamps `task=`; live smoke: one persisted eval, keys
   inspected.

## 9. Risks

- **Store interface changes** (`read_manifest` param, `put_bytes`) break
  downstream `Store` implementers — mechanical, called out in the PR.
- **Attempt overwrite on resume-after-death** (step 0: dead attempts are
  overwritten from `a0`) discards a dead process's partial evidence. Accepted:
  ADR-0004 already defines deterministic overwrite for re-runs, and keeping
  ghost attempts would need cross-process attempt discovery for no reader.
- **The eval `should_retry` override doubles the cost of every genuinely-failing eval** (same
  trade ADR-0005 took; budget default stays small).
