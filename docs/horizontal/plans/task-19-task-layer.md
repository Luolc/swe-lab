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

  Owns exactly what the manager does not: assembling the mounts, the
  observers, and the derived output schema. Lifecycle is mount → run →
  outputs (§2); subclasses supply the parts, `execute` runs the five steps
  once.

  A task is a **declaration** — instance, config, nothing stateful — and each
  `execute` call is one run: everything dirty is either built fresh inside it
  (the observers, the manager) or handed in fresh (the sandbox). That is why
  `sandbox` is an *argument*, not a field: Task 20's retry calls `execute`
  again on the same task with a fresh sandbox per attempt, and the caller
  owns every construction knob (backend, workspace, network) per the repo's
  inject-collaborators rule. Re-executable sequentially; not concurrently
  (a task may keep a per-run observer reference on itself for `action`).
  """

  **Three hooks, one channel each — and each hook is total.** `mounts()` is
  *all* of this task's mounts; `observers()` is *all* of its observers. There
  is no "the task's own" versus "gathered for you": a subclass overrides the
  hook and merges in whatever it uses — the instance's material, a harness's
  files, anything. `execute` takes the hooks' word for it, adds only what a
  task cannot know (the backend's observers, the caller's extras), and runs.

  | you have | you write |
  |---|---|
  | files to stage | override `mounts()` — merge the instance's / a harness's / yours; what it returns **is** the staging set |
  | a deliverable | the observer that extracts it, in `observers()` — its schema declares the store name |
  | an agent / harness | fold it in: mounts into `mounts()`, observers into `observers()`, its `run` in `action()` |
  | the main action | `action()` |
  | (a caller with persistence etc.) | passes `extra_observers` to `execute` |

  instance: TaskInstance                    # the binding (ADR-0007 §2)

  def mounts(self) -> Mounts:
    """ALL files this task stages. Default: the bound instance's material.
    A subclass overrides and merges in whatever else it uses::

        return merge_mounts(super().mounts(), self.harness.mounts(...), ...)
    """
    return dict(self.instance.mounts())

  def observers(self) -> Sequence[SandboxObserver]:
    """ALL of this task's observers — one per thing it extracts, plus a
    harness's own if it uses one. Default: none. Fresh instances per call
    (observers are single-run; Task 20's retry re-invokes this); a task that
    keeps a reference for `action` (eval's retry loop drives its parse
    observer) stores it on itself — the task is single-run too."""
    return ()

  @abstractmethod
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    """The run's main action: exec the harness, or run the entryscript."""

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
  # The hooks are total: the task said everything it knows. Add only what it
  # cannot know — the backend's observers and the caller's extras.
  observers = [*sandbox.observers(), *self.observers(), *extra_observers]
  # The task's output schema is derived — and a duplicate store name across
  # observers fails HERE, at assembly, like a duplicate mount target.
  schema = merge_output_schemas(*(o.output_schema() for o in observers))
  manager = SandboxManager(
      sandbox=sandbox, output_dir=epath.Path(output_dir),
      # The observers' own mounts still arrive via the manager, which merges
      # each observer.mounts() and refuses duplicate targets.
      observers=observers, mounts=self.mounts(),
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

  def mounts(self):        # instance's material + the harness's own files
    return merge_mounts(
        super().mounts(),
        self.harness.mounts(self.instance.sandbox_spec().workdir),
    )
  def observers(self):     # the harness's own + the deliverable's extractor
    return (*self.harness.observers(),
            DiffExtractObserver(exclude_globs=self.exclude_globs))
  def action(self, sb, *, timeout):
    # The proxy records the agent's API traffic, so its lifetime is the
    # agent's — open around the run, closed before before_destroy reads the
    # log (a flush guarantee the old whole-session placement never had).
    with self.proxy or contextlib.nullcontext():
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
  def mounts(self):
    # Interim: the spec still carries the instance trio (§2.6 moves it onto
    # instance.mounts(), after which super().mounts() takes over that half).
    return merge_mounts(
        dict(self._spec.mounts),
        {ENTRYSCRIPT_NAME: Mount(Inline(self._spec.eval_script.encode()),
                                 executable=True)},
    )
  def observers(self):
    # the grader arrives here directly — dataset-supplied, no vehicle
    # (ADR-0007 §4); the reference is kept because action's retry loop
    # drives this observer (the task is single-run, like the observer)
    self._parse = EvalParseObserver(
        self._spec.grader, native_outputs=self._spec.native_outputs
    )
    return (self._parse,)
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
                         prompt=prompt, proxy=proxy, exclude_globs=exclude_globs,
                         agent_env=agent_env)
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
3. `UnitTestEvalTask.mounts()` drops the interim `dict(self._spec.mounts)`
   half in favour of `super().mounts()` (the instance's material via
   `instance.mounts()`), keeping its own entryscript + patch;
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
2. `Task.execute` + composition-order test (backend observers before the
   task's; duplicate mount target across sources refused).
3. `UnitTestEvalTask` + `run_unit_test` wrapper; suite + live gold eval.
4. `CodingAgentTask` + `run_rollout` wrapper; suite + live rollout.
5. Checkpoint review (§3), then one PR.

## 5. Risks

- **The in-run retry loop resists extraction** — it mutates the parse observer
  between attempts. Mitigation: it stays inside `UnitTestEvalTask.action`
  verbatim; only Task 20 may reshape it.
- **Proxy lifetime narrows, deliberately**: today the proxy wraps the whole
  session; in the task it wraps `action` only — the agent is the only thing
  that calls through it, and closing before `before_destroy` means the log is
  flushed before conversion reads it. No observable difference (the
  equivalence gate checks), and no wrapper layer. One consequence stated
  plainly: a context-manager proxy is single-use, so a *re-executed*
  `CodingAgentTask` needs a fresh one — irrelevant today (only eval retries),
  revisited if Task 20 ever retries agent tasks.
- **Hidden coupling to mount timing** (`prompt.txt` was composition-staged;
  after Task 18 the harness writes it in `run`) — the live-rollout equivalence
  run is what catches any residue.
