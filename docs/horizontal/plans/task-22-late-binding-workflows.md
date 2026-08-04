# Task 22 — Late-bound instances: static workflow definitions, a registry, one CLI

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Builds on tasks 19–21. Absorbs task 21's deferred CLI rewire, and retires
> the ADR-0005 in-run eval retry (a new ADR supersedes it here).

---

## 1. Purpose & scope

A workflow becomes a **pure, statically-writable definition** — no instance,
no sandbox factory, no run-time values — registrable in an open registry and
invoked by name: `swe-lab run solve_and_grade <instance_id>`. The instance
binds at `execute`, everything instance-derived becomes a hook argument, and
sandbox construction is synthesized by the runner from declared
`SandboxConfig` + invocation flags + the instance's spec.

### In scope

- `Task` loses its `instance` field; hooks take the instance as an argument.
- `WorkflowEntry.sandbox_factory` retired → `sandbox: SandboxConfig`.
- Workflow definitions + `register_workflow` / `build_workflow` registry.
- The CLI rewire (absorbed from task 21): `swe-lab run <workflow>` plus the
  existing commands rebuilt over registered workflows.
- **The in-run eval retry retires** (with the wrappers that kept it alive);
  ADR-0008 supersedes ADR-0005.

### Out of scope

- DAG scheduling, `optional` entries (unchanged deferrals).
- `pipelines/related_files` migration (the acid test, after this).

---

## 2. Where `instance` lives today, and where each use goes

| site | use | after |
|---|---|---|
| `Task.instance` field | the binding | **deleted** — `execute(sandbox, instance, …)` |
| `Task.mounts()` default | `instance.mounts()` | `mounts(instance)` |
| `CodingAgentTask.mounts()` | `instance.sandbox_spec().workdir` for the harness | `mounts(instance)` |
| `CodingAgentTask.action()` | `instance.prompt()` fallback | `action(sb, instance, …)` |
| `UnitTestEvalTask.__post_init__` | compiles `unit_test_spec(patch=…)` | compiled **per execute** (first hook call), stashed for the run |
| `run_task` | `instance_id` → key/record | `run_task(task, instance, …)` |
| `Workflow` consistency check + `instance_id` | key/record | **dissolves** — the one instance arrives at `execute(instance)` |

`input_schema()` needs no change: since the UPSTREAM retirement it is a fixed
function of task configuration — which is what keeps a definition's *input
side* checkable at declaration time (§5).

## 3. The `Task` contract, late-bound

```python
class Task(ABC):
  """A pure declaration: configuration fields only. One execute = one run
  against one (sandbox, instance) pair; per-run state (a compiled spec, a
  kept observer reference) is stashed on self during that run — sequential
  re-execution stays allowed, concurrent does not (unchanged)."""

  def mounts(self, instance: TaskInstance[Any]) -> Mounts:
    return dict(instance.mounts())

  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    return ()

  def input_schema(self) -> Sequence[ArtifactSchema]:      # static, unchanged
    return ()

  @abstractmethod
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult: ...

  def outputs_valid(self, result: AttemptResult) -> bool: ...   # unchanged
  def should_retry(self, result: AttemptResult) -> bool: ...    # unchanged

  @final
  def execute(self, sandbox: Sandbox, instance: TaskInstance[Any], *,
              output_dir, timeout, extra_mounts=None, extra_observers=()
  ) -> AttemptResult:
    # unchanged shape; hooks now receive `instance`
```

The two shipped tasks:

```python
@dataclass
class CodingAgentTask(Task):
  harness: Harness
  prompt_input: str | None = None
  # The prompt is this task's patch-shaped input, same treatment (§ eval):
  #   None     → the instance's own prompt (the dataset's task statement) —
  #              the common solve case, not an external input at all;
  #   a name   → the prompt arrives as this declared input, by mount — an
  #              upstream agent's output in a chain, or the caller's bytes
  #              via the workflow's `inputs`. Configurable rather than a
  #              fixed contract name because, unlike `patch.diff` (which
  #              DiffExtract already emits), no natural producer-side name
  #              exists: agent 2 binds directly to whatever agent 1 calls
  #              its artifact ("plan.md"), no re-emission.
  # The old `prompt: str` literal field dies with the wrapper: a caller
  # literal is `inputs={name: Mount(Inline(...))}` — one channel.
  exclude_globs: tuple[str, ...] = ()
  agent_env: Mapping[str, str] | None = None
  proxy: AbstractContextManager[object] | None = None

  def mounts(self, instance):
    return merge_mounts(
        super().mounts(instance),
        self.harness.mounts(instance.sandbox_spec().workdir),
    )

  def input_schema(self):              # still config-static
    if self.prompt_input is None:
      return ()
    return (ArtifactSchema(self.prompt_input, description="the task prompt"),)

  def action(self, sb, instance, *, timeout):
    # The harness contract is untouched (prompt as a string, ADR-0007 §8):
    # the task reads the mounted input and hands over text.
    prompt = (
        sb.read(self.prompt_input).decode("utf-8", "backslashreplace")
        if self.prompt_input is not None
        else instance.prompt()
    )
    with self.proxy or nullcontext():
      return self.harness.run(sb, prompt=prompt, timeout=timeout,
                              env=self.agent_env)


@dataclass
class UnitTestEvalTask[V: Verdict](Task):
  apply_patch: bool = True
  eval_env: Mapping[str, str] | None = None
  # `retries` is GONE: in-run retry retires (§7); the budget is the entry's.

  def observers(self, instance):
    # per-run compilation, stashed for mounts()/action() of this run —
    # observers() is execute's first hook call, so it is the compile site
    self._spec = instance.unit_test_spec(
        patch="" if self.apply_patch else None
    )
    self._parse = EvalParseObserver(
        self._spec.grader, native_outputs=self._spec.native_outputs
    )
    return (self._parse,)
  # mounts(instance): spec mounts (minus placeholder) + entryscript, as today
  # action(sb, instance): ONE entryscript run — no loop (§7)
```

`UnitTestSpec.retries` is deleted with the loop (§7); a dataset that knows an
instance's measured flake rate expresses it as workflow-entry configuration
where the budget now lives.

## 4. Sandbox construction: declared config, synthesized per attempt

`WorkflowEntry.sandbox_factory` retires — and **`SandboxConfig` splits
first**, because today's flat class carries Docker-host mechanics
(`workspace`, `pull`, `shell`) that a remote backend has no use for, and its
"each factory takes only what applies" rule is a silent-ignore trap.

### 4.1 The split

```python
@dataclass(frozen=True)
class SandboxConfig:
  """Backend-agnostic run SEMANTICS — what an entry may declare statically.

  Every backend must honor these or refuse loudly at construction — never
  silently ignore (ghjob cannot cut network on an already-running job
  container: network=False there is an error, not a no-op).
  """
  network: bool = True                 # the run may/may not reach the net
  env: Mapping[str, str] = ...         # vars set on each exec
  pass_env: Sequence[str] = ()         # secrets inherited by reference


@dataclass(frozen=True)
class DockerHostSandboxConfig(SandboxConfig):
  """A-host mechanics: how THIS backend realizes a run."""
  workspace: epath.Path | None = None  # bind-mounted host dir
  pull: bool = True
  shell: str = "/bin/bash"


@dataclass(frozen=True)
class GhjobSandboxConfig(SandboxConfig):
  """A-ghjob mechanics (the job container is already the sandbox)."""
  workspace: epath.Path | None = None
  shell: str = "/bin/bash"
```

Deliberately flat (base + one subclass per backend, `workspace`/`shell`
repeated) rather than an intermediate `LocalSandboxConfig` — a three-level
hierarchy for two backends is premature. A downstream backend brings its own
subclass, Resource-style ownership as before.

### 4.2 Who supplies what

| layer | supplies | as |
|---|---|---|
| **entry** (static definition) | run semantics | base `SandboxConfig` — `network=False` for eval, `pass_env=("CLAUDE_CODE_OAUTH_TOKEN",)` for the agent |
| **invocation** (CLI flags) | the backend + its mechanics | a backend-config *prototype*: `--backend host --no-pull` → `DockerHostSandboxConfig(pull=False)` |
| **runner** (per attempt) | the merge + the workspace | `replace(prototype, network=…, env=…, pass_env=…, workspace=output_dir/"ws"/f"a{attempt}")` |

```python
@dataclass(frozen=True)
class WorkflowEntry:
  key: str
  task: Task
  timeout: float
  sandbox: SandboxConfig = SandboxConfig()   # base semantics only in the
      # shipped definitions. An entry MAY declare a backend subclass — that
      # binds the workflow to the backend, its own trade — and then the
      # invocation prototype's type must match, or the bind refuses.
  inputs: Sequence[str] = ()
  retries: int = 0
```

The fresh-workspace factory contract dissolves — allocation is the runner's
again. The registry factory signature stays
`(SandboxSpec, SandboxConfig) -> Sandbox`; each factory narrows to its own
config type and **rejects** what it cannot honor. The flat-kwargs
`build_sandbox(...)` convenience is rebuilt per-backend (it constructs the
host prototype for the CLI); `run_task`'s public signature swaps
`sandbox_factory` for `(backend, prototype, instance)` — a direct caller who
wants a hand-built sandbox still has `Task.execute`.

## 5. Definitions, the registry, and bind-time validation

```python
# The static definition: entries only. Statically writable anywhere.
type WorkflowDef = tuple[WorkflowEntry, ...]

register_workflow(name: str, definition: WorkflowDef) -> None
registered_workflows() -> list[str]
build_workflow(name, *, store, sweep_id, rollout_id) -> Workflow
```

- `Workflow` keeps `(store, sweep_id, rollout_id, entries)`; it loses
  `inputs` as a field — **caller-input values move to
  `execute(instance, inputs={name: Mount}, …)`** (they are run-time values;
  the reserved `"inputs"` producer semantics are unchanged).
- **Validation splits by what each phase can know.** At declaration
  (registry/`Workflow` construction): key uniqueness and shape, binding
  syntax, bindings against the *static* `input_schema()` (a dead binding is
  still refused early). At **bind time** (`execute`, before any container):
  full edge resolution — output schemas may now be instance-derived (the
  eval observer's `native_outputs` come from the compiled spec), so
  producer matching, ambiguity, and the dead-caller-input check run once the
  instance and the provided inputs are in hand. Still fail-before-run;
  no longer fail-at-import. Ambiguity rules themselves are unchanged.
- Built-in definitions ship in `workflow/definitions.py` and register at
  import (like backends/stores/fixes): `solve` (rollout only),
  `solve_and_grade` (rollout → eval), `grade` (eval, patch from `inputs`).

**Agent knobs (model / capture / bare) are the one thing a constant
definition cannot carry** — they are invocation config living inside the
constructed `Harness`. Resolution: a registry value may be either a constant
`WorkflowDef` **or a builder** `Callable[[RunOptions], WorkflowDef]`, where
`RunOptions` is a small dataclass of the agent-ish invocation knobs (model,
capture, bare, agent_env, proxy wiring); a builder ignores what it does not
use, mirroring how `SandboxConfig` serves every backend. Built-ins that run
an agent register builders; grade-only workflows register constants.

## 6. The CLI

```
swe-lab run <workflow> <instance_id>
    --dataset swebench_pro --sweep adhoc --rollout-id 0
    --backend host --pull/--no-pull --timeout-scale?          (invocation)
    --model … --capture … --bare                              (RunOptions)
    --input patch.diff=@candidate.diff | --gold               (caller inputs)
    --resume/--no-resume (default: no-resume — a re-run re-runs)
    --persist/--no-persist (off → throwaway FilesystemStore under the run dir)
```

- `--gold` is sugar for `--input patch.diff=<instance.gold_patch()>`.
- The existing `rollout` / `eval` commands are **rebuilt as thin aliases**
  over `solve` / `solve_and_grade` / `grade` with their current flags mapped
  (summary JSON keeps its fields; `attempts`/`flaky` now carry task-level
  semantics; persisted records are task-keyed shards, one per attempt).
- `run_rollout` / `run_unit_test` (and their private shims) are **deleted**,
  not deprecated — nothing in-repo calls them after the rewire, and the
  in-run retry they preserved is retiring with them (§7). Downstream
  migrates to workflows or `Task.execute`; prototyping, no compat layer.

## 7. The in-run eval retry retires (ADR-0008 supersedes ADR-0005)

Task-level retry now answers the same question strictly better: each attempt
is a **fresh sandbox** (stronger isolation than a warm-container re-run),
each attempt is **persisted separately** under its own `a<N>` prefix, and
the flake-absorption trigger survives as `UnitTestEvalTask.should_retry`
(unresolved → spend budget). So:

- `_attempt_until_resolved` and the loop inside `action` are deleted; the
  action runs the entryscript once.
- `EvalParseObserver.attempts` / `retained` and `_retain_attempt` are
  deleted — retention existed because attempts shared one workspace; task
  attempts do not share anything, the store already keeps every attempt's
  outputs apart.
- `UnitTestSpec.retries` and `UnitTestEvalTask.retries` are deleted; the
  budget is `WorkflowEntry.retries`, where run configuration lives.
- `Verdict.attempts` / `flaky` reduce to constants at verdict level; the
  real signal moves to the records (`flaky` = resolved at `attempt > 0` with
  an earlier unresolved-but-valid shard) and is written into the final
  record's `extra` by `run_task`. The `known_flaky` registry keeps its
  meaning (measured rates), unchanged.
- **ADR-0008** records this supersession: what ADR-0005 established (retry
  absorbs harness nondeterminism, same patch every attempt, budget bounded),
  what moves (the level it runs at, the isolation, the evidence layout), and
  what is deliberately given up (warm-container re-runs — an attempt now
  pays container setup, accepted for one retry mechanism instead of two).

## 8. Steps

1. Task contract late-binding (hooks take `instance`; both tasks; `execute`
   signature) + test updates.
2. §7 retirement + ADR-0008 (its own commit; `Verdict` cleanup).
3. The `SandboxConfig` split (base semantics + per-backend subclasses;
   silent-ignore becomes loud rejection) — its own commit, backends and
   their tests updated.
4. `WorkflowEntry.sandbox` + prototype merge + runner synthesis (+
   config-subclass and type-mismatch tests); `run_task` signature;
   workspace allocation moves in.
5. Registry + built-in definitions + bind-time validation split (+ tests:
   declaration-time vs bind-time failures).
6. CLI: `swe-lab run` + alias rebuild; wrappers deleted; CLI tests rebuilt
   over a registered fake backend (no monkeypatched wrappers).
7. Live smoke: `swe-lab run solve_and_grade` on the flipt parity instance
   (agent + grade through the registry path); persisted keys inspected.
8. Docs: plans/README statuses; conventions command examples; ADR-0007
   §6 amendment note (budget location).

## 9. Risks & open questions

- **Validation moves from import time to bind time** for the output side —
  a registry full of workflows is syntax-checked at import, edge-checked on
  first bind. Accepted: instance-derived output schemas make it inherent;
  every failure still precedes any container.
- **`RunOptions` shape** — the builder argument starts minimal (model,
  capture, bare, agent_env, proxy) and grows only with real flags; the
  alternative (baking a harness into a constant definition) freezes model
  choice into code, which is worse.
- **Deleting the wrappers is breaking** for any downstream caller; the
  migration story is one page (construct a workflow, or call
  `Task.execute`), and prototyping was the agreed bar for compat.
- **Task-level `attempts`/`flaky` reach reports differently** — sweeps that
  read verdict-level flakiness must read record-level; called out for the
  downstream large-run pass this precedes.
