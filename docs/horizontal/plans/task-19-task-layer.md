# Task 19 — The `Task` layer, and both compositions rewritten on it

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Implements ADR-0007 §§1–5 (the `Task` itself). §6–7 (task retry keyed by
> record, resume) are Task 20; workflows are Task 21. **This task is the
> falsification checkpoint**: if the two rewrites come out ugly, stop and amend
> the ADR — the ADR is wrong, not the rewrite.

---

## 1. Purpose & scope

Give the five-step shape both compositions share a name (`Task`), and prove it
by making `run_rollout` and `run_unit_test` thin wrappers over it with
**unchanged signatures and byte-identical behavior**.

### In scope

- `workflow/` package: `Task`, `TaskResult`; `OutputSchema` +
  `SandboxObserver.output_schema()` land in `sandbox/` (the observers are the
  ones declaring).
- `CodingAgentTask` and `UnitTestEvalTask` — the two shipped subclasses.
- Both compositions rewritten as wrappers; both CLIs untouched.
- In-run eval retry (ADR-0005) preserved exactly where it is.

### Out of scope

- Task-level retry, output-validation-gated (Task 20 — needs the record key).
- Resume markers, workflow, edge mounting from the store (Tasks 20–21).
- `steps` inside a task (ADR-0007 defers it), `related_files` migration.
- Removing `UnitTestSpec` (it keeps compiling eval; `Task` consumes it).

---

## 2. Interface definitions

New package **`src/swe_lab/workflow/`** — deliberately not `tasks/`: "task"
collides with too many neighbouring concepts (the old `pipelines/`, dataset
task instances), while "workflow" names the umbrella unambiguously. `Task` and
(in Task 21) `Workflow` both live here.

### 2.1 Observers declare their outputs: `OutputSchema`

There is no producer concept. A producer was an observer factory carrying a
`name` and a `required` flag — two fields of *description* strapped to a level
of indirection. The description moves onto the thing being described: **every
observer declares its own output schema**, and a task's output schema is the
merge of its composed observers'.

An output is what ends up in the **store** — so a schema entry is pure data,
deliberately JSON-schema-in-spirit but minimal:

```python
# sandbox/observer.py — beside SandboxObserver
@dataclass(frozen=True, slots=True)
class OutputSchema:
  """What one output *is*: its store name, whether it must exist, and why.

  Pure data — no parse concept: an output is the artifact as persisted, and
  how an observer computed it is the observer's business.

  Attributes:
    name: The artifact name as it appears in the store (format-suffixed:
      ``patch.diff``, ``conversation.json``).
    required: Whether a completed run without this output is a failed run.
      Advisory in this task; becomes the validation/retry gate in Task 20.
    description: One line saying what this output is, for a reader of the
      merged schema.
  """

  name: str
  required: bool = True
  description: str = ""


class SandboxObserver:
  ...
  def output_schema(self) -> Sequence[OutputSchema]:
    """Declare the outputs this observer produces. Default: none."""
    return ()
```

Existing observers self-describe (no wrappers, no new classes):

- `DiffExtractObserver` → `("patch.diff", required, "the extracted clean
  patch")`
- `ConversationObserver` → `("conversation.json", required, "the canonical
  typed trace")`
- `EvalParseObserver` → its parsed result + logs (`eval.output.json`
  required; the logs best-effort)
- `HarnessOutcomeObserver` → the harness's declared native outputs
  (best-effort, from `harness.native_outputs()`)
- `HostMetricsObserver` → `()` (metrics only)

A task's schema is derived, and merging is where conflicts surface:

```python
def merge_output_schemas(
    *schemas: Sequence[OutputSchema],
) -> tuple[OutputSchema, ...]:
  """Merge observers' schemas; a duplicate store name is an error.

  The same rule `merge_mounts` already applies to mount targets, for the same
  reason: two observers writing one store name is a composition bug, and it
  should fail at assembly, not at persist.
  """
```

The grader needs no vehicle anymore: `UnitTestEvalTask` constructs
`EvalParseObserver(grader, ...)` directly, with the grader still supplied by
the dataset (ADR-0007 §4 — no `Evaluator`, nothing special-cased).

### 2.2 `Task` — one sandbox; assembles mounts, observers, outputs

```python
class Task(ABC):
  """One unit of work in one sandbox (ADR-0007 §1).

  Owns exactly what the manager does not: assembling the four mount sources,
  the three observer sources, and the declared outputs. Lifecycle is
  mount → run → outputs (§2); subclasses supply the parts, `execute` runs the
  five steps once. Single-run, like the observers it composes.
  """

  A task author answers **three questions** — has it a runner, what does it
  produce, what does it run. There is exactly **one channel** for "what it
  produces": the task lists its observers, and each observer self-declares
  its outputs (§2.1) — no second bookkeeping to keep consistent with it.

  | you have | you write |
  |---|---|
  | an agent that does the work | `runner()` returns it — its observers come with it |
  | a deliverable | the observer that extracts it, in `observers()` — its schema declares the store name |
  | files of your own to stage | `mounts()` |
  | the main action | `action()` |
  | (a caller with persistence etc.) | passes `extra_observers` to `execute` |

  # ---- what a subclass declares -------------------------------------------

  def runner(self) -> Harness | None:
    """The runner doing this task's work, or ``None`` (e.g. eval: the action
    is just the entryscript, per ADR-0007 §4). A task with a runner returns
    it and its observers are composed automatically via the runner's own
    factory (Task 18's `Harness.observers()`)."""
    return None

  @abstractmethod
  def mounts(self) -> Mounts:
    """The task's own files (e.g. eval entryscript). NOT the instance's or
    the runner's — those are gathered by `execute`."""

  @abstractmethod
  def observers(self) -> Sequence[SandboxObserver]:
    """The task's own observers — one per thing it extracts (e.g.
    `DiffExtractObserver`). Fresh instances per call, like
    `Harness.observers()`: observers are single-run, and Task 20's retry
    re-invokes this. A task that keeps a reference for `action` (eval's
    retry loop drives its parse observer) stores it on itself — the task is
    single-run too."""

  @abstractmethod
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    """The run's main action: exec the runner, or run the entryscript."""

  # ---- what the base class provides ---------------------------------------

  def instance_mounts(self) -> Mounts:
    """The bound instance's material; default `self.instance.mounts()`."""

  @final
  def execute(
      self,
      sandbox: Sandbox,
      *,
      output_dir: epath.PathLike,
      timeout: float,
      extra_observers: Sequence[SandboxObserver] = (),
  ) -> TaskResult:
    """Run the five steps once. See pseudocode below."""
```

`execute` pseudocode — this *is* the five-step shape, written once:

```python
def execute(self, sandbox, *, output_dir, timeout, extra_observers=()):
  # 1. observers, three sources, backend first (ADR-0007 §3)
  runner = self.runner()
  observers = [
      *sandbox.observers(),                 # backend: runtime metrics
      *(runner.observers() if runner else ()),  # runner: trace, completion
      *self.observers(),                    # task: what it extracts
      *extra_observers,                     # caller: e.g. persist
  ]
  # The task's output schema is derived — and a duplicate store name across
  # observers fails HERE, at assembly, like a duplicate mount target.
  schema = merge_output_schemas(*(o.output_schema() for o in observers))
  # 2. mounts — instance's + runner's + the task's own; the observers'
  #    arrive via the manager, which already merges each observer.mounts().
  #    merge_mounts refuses duplicate targets across sources.
  mounts = merge_mounts(
      self.instance_mounts(),
      runner.mounts(sandbox.spec.workdir) if runner else {},
      self.mounts(),
  )
  manager = SandboxManager(
      sandbox=sandbox, output_dir=epath.Path(output_dir),
      observers=observers, mounts=mounts,
  )
  # 3. the action, inside the session
  exec_result = None
  try:
    with manager.session() as sb:
      started = time.monotonic()
      try:
        exec_result = self.action(sb, timeout=timeout)
      finally:
        # The handoff both compositions do by hand today, generalized: every
        # composed observer carrying the exec_result / wall_seconds fields
        # (HarnessOutcomeObserver, EvalParseObserver) gets them before
        # teardown, so before_destroy can report how the action ended.
        _hand_exec_outcome(observers, exec_result, time.monotonic() - started)
  except SandboxError:
    pass                                    # recorded in manager.result
  # 4.–5. observers already contributed at teardown; assemble the result
  return TaskResult(
      run=manager.result,
      exec_result=exec_result,
      output_schema=schema,
      observers=tuple(observers),
  )
```

### 2.3 `TaskResult`

```python
@dataclass(frozen=True)
class TaskResult:
  """What one execution of a task yields.

  Attributes:
    run: The engine result (status, artifacts as host paths, metrics).
    exec_result: The main action's own outcome; None if it never ran.
    output_schema: The task's merged schema — what this run was *supposed*
      to produce. Task 20's validation gate reads it against `run.artifacts`
      (a required name with no artifact fails the attempt); the workflow's
      edge mounting resolves upstream names through it.
    observers: Every composed observer, in composition order — the typed
      results live on them (`EvalParseObserver.verdict`,
      `DiffExtractObserver.patch`), and the wrappers read them back by type.
  """

  run: RunResult
  exec_result: ExecResult | None
  output_schema: tuple[OutputSchema, ...]
  observers: tuple[SandboxObserver, ...]
```

### 2.4 The two shipped subclasses

```python
@dataclass
class CodingAgentTask(Task):
  """An agent solves the bound instance; outputs a patch and a trace."""

  instance: TaskInstance
  harness: Harness
  prompt: str | None = None                 # default: instance.prompt()
  exclude_globs: tuple[str, ...] = ()
  agent_env: Mapping[str, str] | None = None
  proxy: AbstractContextManager[object] | None = None

  def runner(self):        return self.harness
  def mounts(self):        return {}        # the harness's own mounts arrive
                                            # via runner() in execute()
  def observers(self):     # the deliverable: its observer declares patch.diff
    return (DiffExtractObserver(exclude_globs=self.exclude_globs),)
  def action(self, sb, *, timeout):
    return self.harness.run(sb, prompt=self.prompt or self.instance.prompt(),
                            timeout=timeout, env=self.agent_env)


@dataclass
class UnitTestEvalTask[V: Verdict](Task):
  """Grade a patch against the bound instance's unit tests."""

  instance: TaskInstance[V]
  patch: str | None                         # None = grade the base commit
  retries: int = 1                          # ADR-0005 in-run retry, unchanged
  eval_env: Mapping[str, str] | None = None

  # compiled once in __post_init__: self._spec = instance.unit_test_spec(...)
  def mounts(self):        return {ENTRYSCRIPT_NAME: Mount(Inline(self._spec.eval_script.encode()), executable=True)}
  def instance_mounts(self): return dict(self._spec.mounts)   # until 19, the spec *is* the instance's mounts
  def observers(self):
    # the grader arrives here directly — dataset-supplied, no vehicle
    # (ADR-0007 §4); the reference is kept because action's retry loop
    # drives this observer (the task is single-run, like the observer)
    self._parse = EvalParseObserver(
        self._spec.grader, native_outputs=self._spec.native_outputs
    )
    return (self._parse,)
  # runner(): inherited None — eval has no runner (no Evaluator, ADR-0007 §4)
  def action(self, sb, *, timeout):
    # the existing _attempt_until_resolved loop, verbatim, driving
    # self._parse: in-run retry is ADR-0005's and stays inside the action
    ...
```

### 2.5 The wrappers (signatures frozen)

```python
def run_rollout(sandbox, harness, *, prompt, output_dir, timeout,
                proxy=None, agent_env=None, exclude_globs=(), observers=()):
  task = CodingAgentTask(instance=_SpecOnlyInstance(sandbox.spec), harness=harness,
                         prompt=prompt, exclude_globs=exclude_globs, agent_env=agent_env)
  with proxy or contextlib.nullcontext():
    result = task.execute(sandbox, output_dir=output_dir, timeout=timeout,
                          extra_observers=observers)
  return RolloutOutcome(...)   # assembled from result, exactly today's fields

def run_unit_test(sandbox, unit_test_spec, *, output_dir, timeout,
                  retries=1, eval_env=None, observers=()):
  task = _SpecEvalTask(spec=unit_test_spec, retries=..., eval_env=eval_env)
  result = task.execute(sandbox, output_dir=output_dir, timeout=timeout,
                        extra_observers=observers)
  return result.run, result.outputs["verdict"].verdict
```

(`_SpecOnlyInstance` / `_SpecEvalTask`: private shims for the wrapper paths
where the caller hands a spec, not an instance — the CLIs construct from
instances and won't need them once Task 21 rewires them.)

### 2.6 The instance-mounts migration (SWE-Bench Pro's parser / run_script)

Where they are mounted **today**, end to end:

1. `SweBenchProInstance.unit_test_spec(patch=...)` (`record.py`) reads
   `self.run_script` / `self.parser` (properties fetching + caching the
   upstream harness files) and hands them to `compile_unit_test`;
2. `compile_unit_test` (`datasets/swebench_pro/unit_test.py`) builds
   `UnitTestSpec.mounts = {run_script.sh, parser.py, required_tests.json
   [, patch.diff]}` as `Mount(Inline(...))`;
3. `run_unit_test` copies `spec.mounts` into the `SandboxManager`, adds the
   entryscript, and the manager stages everything at session start.

So the spec's mount dict currently conflates **three owners**, and the
migration is reading each entry its real one:

| mount | real owner | destination |
|---|---|---|
| `run_script.sh`, `parser.py` | the **instance** (fetched per instance, patch-independent) | `SweBenchProInstance.mounts()` |
| `required_tests.json` | the **instance** (`fail_to_pass ∪ pass_to_pass`, patch-independent) | `SweBenchProInstance.mounts()` |
| `patch.diff` | the **task** (the run's input — a candidate, the gold patch, or absent) | `UnitTestEvalTask.mounts()`; a workflow edge in Task 21 |
| `entryscript.sh` | the **task** (built from base commit + golden checkout + flags) | `UnitTestEvalTask.mounts()` |

Migration inside this task, in order:

1. `SweBenchProInstance.mounts()` returns the instance trio (moving the
   `Inline` construction out of `compile_unit_test`);
2. `compile_unit_test` stops emitting the trio; `UnitTestSpec.mounts` shrinks
   to `{patch.diff}` — the one entry that varies per run;
3. `UnitTestEvalTask.instance_mounts()` drops its interim
   `dict(self._spec.mounts)` shim and uses the default
   (`self.instance.mounts()`); its `mounts()` contributes entryscript + patch;
4. the `run_unit_test` wrapper keeps working unchanged — its `_SpecEvalTask`
   shim treats whatever the spec still carries as instance material, so a
   downstream caller holding a pre-split spec is unaffected until Task 21.

The fixes seam is the regression risk: `fixes/_seam.py::with_setup` merges a
fix's mounts into `spec.mounts` and splices bash against the spec's script.
The seam keeps working on the spec (whose script it owns); only the trio's
*location* moves, and `test_every_workspace_file_a_script_names_is_staged_or_produced`
already fails if a script names a file that no source stages.

---

## 3. The falsification gate (checkpoint, human-reviewed)

Hard acceptance criteria, in order:

1. **Signatures unchanged**: `run_rollout` / `run_unit_test` callers (CLIs,
   verify.py, tests) compile with zero edits.
2. **Full suite green with zero test edits** outside the new `workflow/` tests.
3. **Live equivalence, byte-level**: one gold eval (the flipt parity instance)
   and one live rollout, run on `main` and on the branch — `output.json`,
   verdict summary, patch, and artifact name sets identical.
4. **The wrappers are thin**: no logic beyond construction + result reshaping.
   If either wrapper needs to reach around `Task`, that is the ADR failing —
   stop and amend, don't force it.

## 4. Steps

1. `OutputSchema` + `SandboxObserver.output_schema()` + `merge_output_schemas`
   in `sandbox/`, existing observers self-describing; then `TaskResult` + task
   wrappers; unit tests with `FakeSandbox`.
2. `Task.execute` + composition-order test (backend observers before runner's
   before outputs'; duplicate mount target across sources refused).
3. `UnitTestEvalTask` + `run_unit_test` wrapper; suite + live gold eval.
4. `CodingAgentTask` + `run_rollout` wrapper; suite + live rollout.
5. Checkpoint review (§3), then one PR.

## 5. Risks

- **The in-run retry loop resists extraction** — it mutates the parse observer
  between attempts. Mitigation: it stays inside `UnitTestEvalTask.action`
  verbatim; only Task 20 may reshape it.
- **Proxy lifetime**: today the proxy wraps the whole session. The wrapper
  keeps that placement (proxy around `execute`), not `Task`'s concern.
- **Hidden coupling to mount timing** (`prompt.txt` was composition-staged;
  after Task 18 the harness writes it in `run`) — the live-rollout equivalence
  run is what catches any residue.
