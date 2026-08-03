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

- `workflow/` package: `Task`, `TaskOutput`, `OutputProducer`, `TaskResult`.
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

### 2.1 `TaskOutput` — a declared output: a name plus a producer

```python
@dataclass(frozen=True, slots=True)
class TaskOutput:
  """One output a task promises: its artifact name, and who produces it.

  ADR-0007 §4: every observer here already produces an output; `Contribution`
  already splits raw (`artifacts`) from parsed (`inline_artifacts`). So an
  output *is* a name plus a producer, and the eval grader is one producer —
  supplied by the dataset, not special-cased.

  Attributes:
    name: Artifact name, format-suffixed (``patch.diff``, ``output.json``).
    producer: Builds the observer that extracts/parses this output.
    required: Whether a completed run without it is a failed run. Advisory in
      this task (recorded in metrics); becomes the retry/validation gate in
      Task 20.
  """

  name: str
  producer: OutputProducer
  required: bool = True


class OutputProducer(ABC):
  """Factory for the observer that realizes one declared output.

  A factory rather than an observer because observers are single-run: the
  task builds a fresh one per attempt, and Task 20's retry loop re-invokes it.
  """

  @abstractmethod
  def observer(self) -> SandboxObserver:
    """Return a fresh single-run observer producing this output."""
```

Shipped producers (wrapping the existing observers unchanged):

```python
@dataclass(frozen=True)
class PatchOutput(OutputProducer):        # wraps DiffExtractObserver
  exclude_globs: tuple[str, ...] = ()
  def observer(self) -> DiffExtractObserver: ...

@dataclass(frozen=True)
class VerdictOutput[V: Verdict](OutputProducer):   # wraps EvalParseObserver
  grader: Grader[V]                       # ← the dataset's, per ADR-0007 §4
  native_outputs: Mapping[str, str]
  def observer(self) -> EvalParseObserver[V]: ...
```

### 2.2 `Task` — one sandbox; assembles mounts, observers, outputs

```python
class Task(ABC):
  """One unit of work in one sandbox (ADR-0007 §1).

  Owns exactly what the manager does not: assembling the four mount sources,
  the three observer sources, and the declared outputs. Lifecycle is
  mount → run → outputs (§2); subclasses supply the parts, `execute` runs the
  five steps once. Single-run, like the observers it composes.
  """

  # ---- what a subclass declares -------------------------------------------

  @abstractmethod
  def mounts(self) -> Mounts:
    """The task's own files (e.g. eval entryscript). NOT the instance's, the
    runner's, or the observers' — those are gathered by `execute`."""

  @abstractmethod
  def outputs(self) -> Sequence[TaskOutput]:
    """The outputs this task promises (ADR-0007 §4)."""

  @abstractmethod
  def observers(self) -> Sequence[SandboxObserver]:
    """The *runner's* observers (trace, completion). Task-output observers
    come from `outputs()`; sandbox observers from the backend. Default ()."""

  @abstractmethod
  def action(
      self,
      sb: SandboxFs,
      outputs: Mapping[str, SandboxObserver],
      *,
      timeout: float,
  ) -> ExecResult:
    """The run's main action: exec the harness, or run the entryscript.

    ``outputs`` are the built output observers (name → observer), because
    eval's in-run retry loop (ADR-0005) drives its parse observer between
    attempts. A task without that need ignores the argument."""

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
  produced = [(out, out.producer.observer()) for out in self.outputs()]
  observers = [
      *sandbox.observers(),                 # backend: runtime metrics
      *self.observers(),                    # runner: trace, completion
      *(obs for _, obs in produced),        # task: declared outputs
      *extra_observers,                     # caller: e.g. persist
  ]
  # 2. mounts, three sources here + observers' own via the manager;
  #    merge_mounts refuses duplicate targets across sources
  mounts = merge_mounts(self.instance_mounts(), self.mounts())
  manager = SandboxManager(
      sandbox=sandbox, output_dir=epath.Path(output_dir),
      observers=observers, mounts=mounts,
  )
  # 3. the action, inside the session; the built output observers are handed
  #    to it, because eval's in-run retry loop mutates its parse observer
  #    (attempts / exec_result) between attempts
  outputs_by_name = {out.name: obs for (out, obs) in produced}
  exec_result = None
  try:
    with manager.session() as sb:
      started = time.monotonic()
      try:
        exec_result = self.action(sb, outputs=outputs_by_name, timeout=timeout)
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
      outputs=outputs_by_name,
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
    outputs: Declared-output name → the observer that produced it, still
      holding its parsed value (e.g. `EvalParseObserver.verdict`,
      `DiffExtractObserver.patch`). Typed accessors live on the subclasses'
      results — this base mapping is for generic consumers (Task 20's
      validation; the workflow's edge mounting).
    observers: Every composed observer, in composition order — for a caller
      that needs runner-observer state the outputs map does not carry (the
      rollout wrapper reads `complete` and the conversation here).
  """

  run: RunResult
  exec_result: ExecResult | None
  outputs: Mapping[str, SandboxObserver]
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

  def mounts(self):        return {}        # harness mounts stay the runner's
  def outputs(self):       return (TaskOutput("patch.diff", PatchOutput(self.exclude_globs)),)
  def observers(self):     return self.harness.observers()   # Task 18's factory
  def action(self, sb, outputs, *, timeout):   # outputs unused here
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
  def outputs(self):       return (TaskOutput("verdict", VerdictOutput(self._spec.grader, self._spec.native_outputs)),)
  def observers(self):     return ()        # eval has no runner (no Evaluator — ADR-0007 §4)
  def action(self, sb, outputs, *, timeout):
    # the existing _attempt_until_resolved loop, verbatim, driving
    # outputs["verdict"] (its parse observer): in-run retry is ADR-0005's and
    # stays inside the action
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

1. `workflow/` package: `TaskOutput` / `OutputProducer` / `TaskResult` + producer
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
