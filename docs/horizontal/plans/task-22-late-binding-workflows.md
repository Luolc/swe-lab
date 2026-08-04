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
- CLI **plumbing only** (absorbed from task 21): the existing commands
  keep their flags, internally re-plumbed onto workflows so the wrappers can
  retire. The command surface itself is deferred to its own design round
  (§6).
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
@dataclass(kw_only=True)
class Task(ABC):
  """A pure declaration: configuration fields only. One execute = one run
  against one (sandbox, instance) pair. With the in-run retry retired
  (§7) nothing is stashed on self anymore: hooks derive everything from
  their arguments, and the validity hooks read typed results back from
  ``AttemptResult.observers`` — a task is re-executable without caveats.

  A dataclass so the base FIELD below is truly inherited — subclasses stop
  redeclaring it; ``kw_only`` exempts it from the positional-ordering rule,
  so subclasses keep their own required positional fields.
  """

  inputs_builder: InputsBuilder | None = None
  # THE base field: execute() consumes it uniformly (§3.1) — generated
  # bytes land in the workspace, may only fill declared inputs, and collide
  # loudly with an edge supplying the same name. A subclass redeclares it
  # ONLY to change the default (CodingAgentTask → instance_prompt).

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

### 3.1 `InputsBuilder` — the general standalone-mode filler

One concept covers prompt, patch, and anything a task will ever consume.
The **schema stays static** — `input_schema()` declares what the task needs,
period; the builder is one of two interchangeable *suppliers*:

```python
# Generates declared inputs inside the live session — so it can compose
# from the instance AND the workspace (`sb.run_command("git status")`, an
# already-mounted edge input). A plain callable, InstanceFix-style.
type InputsBuilder = Callable[[SandboxFs, TaskInstance[Any]], Mapping[str, bytes]]
```

`Task.execute` handles it uniformly (base class, once). Because the builder
needs the live sandbox, its bytes land by ``sb.write`` **after session start
and before the action** — same workspace files, later timing — and the
checks move with it:

```python
with manager.session() as sb:
  generated = self.inputs_builder(sb, instance) if self.inputs_builder else {}
  declared = {s.name for s in self.input_schema()}
  # the schema is the contract: a builder may only fill declared inputs,
  # and colliding with an edge-supplied name is a bug, loud as ever
  if generated.keys() - declared: raise SandboxError(...)
  if generated.keys() & staged_input_names: raise SandboxError(...)
  for name, data in generated.items():
    sb.write(name, data)
  # requiredness, verified before the action either way:
  #   builder is None  → at assembly, as today (before any container)
  #   builder present  → here, post-build: every required name must now
  #                      exist in the workspace
  ...
  exec_result = self.action(sb, instance, timeout=timeout)
```

- **As a downstream task the builder is simply absent** (`None`) — the edge
  supplies the inputs. Same task, both modes, zero special-casing.
- One timing consequence, stated plainly: with a builder present, a missing
  required input or a collision surfaces *in-session* (recorded as the
  attempt's failure, message intact) rather than at assembly — the builder
  is opaque until it runs, and it cannot run without the sandbox it probes.
  Edge-supplied inputs keep their assembly-time strictness.
  Two ways to move that earlier were weighed. **Mock-sandbox dry-run:
  rejected** — a builder's keys may depend on what it reads (different
  branch under fake data, or a crash), so the inference can be wrong in
  both directions, and a check that can false-positive cannot gate; it
  would be a second, weaker truth. **Declared coverage: the sanctioned
  future path** — if bind-time coverage checking ever earns its keep, pair
  the builder with a `fills` declaration (the observer/output_schema
  precedent: opaque behavior + a self-declaration, verified where it
  lands): bind time checks `fills ⊆ declared` and `fills ∩ edges = ∅`;
  in-session, the builder's actual keys must equal its declaration, loudly.
  A declaration can lie, but the landing check catches the lie — that is a
  contract; a dry-run catches nothing — that is a guess. Not built now: a
  misconfiguration today costs one container startup on the first attempt,
  with an exact message.

The shipped tasks each ship their standalone default:

```python
PROMPT_NAME = "prompt.md"    # the coding task's declared prompt input

def instance_prompt(sb, instance) -> Mapping[str, bytes]:
  """CodingAgentTask's default: the dataset's own task statement."""
  del sb
  return {PROMPT_NAME: instance.prompt().encode()}

def gold_patch(sb, instance) -> Mapping[str, bytes]:
  """UnitTestEvalTask's gold self-check filler (the --gold CLI shape).

  Fills the DEFAULT patch name; a task with a custom ``patch_name`` pairs
  with its own builder — a mismatch trips the only-declared-inputs check,
  loudly, rather than applying nothing.
  """
  del sb
  patch = instance.gold_patch()
  if patch is None:
    raise SandboxError("this dataset carries no gold patch")
  return {PATCH_NAME: patch.encode()}


@dataclass
class CodingAgentTask(Task):
  harness: Harness
  inputs_builder: InputsBuilder | None = instance_prompt
  extra_inputs: tuple[ArtifactSchema, ...] = ()   # extra declared inputs
                                                  # (a chain binds "plan.md"
                                                  # here)
  exclude_globs: tuple[str, ...] = ()
  agent_env: Mapping[str, str] | None = None
  proxy: AbstractContextManager[object] | None = None

  def mounts(self, instance):
    return merge_mounts(
        super().mounts(instance),
        self.harness.mounts(instance.sandbox_spec().workdir),
    )

  def input_schema(self):              # static: the prompt is ALWAYS a
    return (                           # declared input now — default-built
        ArtifactSchema(PROMPT_NAME, description="the task prompt"),
        *self.extra_inputs,            # standalone, edge-supplied in a chain
    )

  def action(self, sb, instance, *, timeout):
    del instance
    # Harness contract untouched (prompt as a string, ADR-0007 §8): the
    # task reads its mounted input and hands over text — where the harness
    # lands it stays the harness's own business.
    prompt = sb.read(PROMPT_NAME).decode("utf-8", "backslashreplace")
    with self.proxy or nullcontext():
      return self.harness.run(sb, prompt=prompt, timeout=timeout,
                              env=self.agent_env)


@dataclass
class UnitTestEvalTask[V: Verdict](Task):
  apply_patch: bool = True
  patch_name: str = PATCH_NAME       # static config: input_schema declares
                                     # it, the spec compiles against it
  # inputs_builder: inherited (default None) — the downstream shape, where
  # the edge or the workflow's caller inputs supply patch.diff. Standalone
  # gold self-check = UnitTestEvalTask(inputs_builder=gold_patch), zero
  # other change.
  eval_env: Mapping[str, str] | None = None
  # `retries` is GONE: in-run retry retires (§7); the budget is the entry's.

  def _compile(self, instance):
    # No self-stash: each hook compiles from the instance directly.
    # Compilation is pure and repeatable by contract (the fixes seam already
    # promises "compiling twice yields the same thing twice") and cheap (the
    # auxiliary files are disk-cached), so paying it twice per execute buys
    # a genuinely stateless task — no lazy assign, no hook-order coupling.
    return instance.unit_test_spec(
        apply_patch=self.apply_patch, patch_name=self.patch_name
    )

  def observers(self, instance):
    spec = self._compile(instance)
    return (
        EvalParseObserver(spec.grader, native_outputs=spec.native_outputs),
    )

  def mounts(self, instance):
    spec = self._compile(instance)   # same spec, by the purity contract
    return merge_mounts(
        dict(spec.mounts),
        {ENTRYSCRIPT_NAME: Mount(Inline(spec.eval_script.encode()),
                                 executable=True)},
    )
  # action(sb, instance): ONE entryscript run — no loop (§7), no spec needed

  def outputs_valid(self, result):
    # the verdict is read back from the result's own observers — the very
    # reason AttemptResult carries them — not from task state
    parse = next(
        o for o in result.observers if isinstance(o, EvalParseObserver)
    )
    return super().outputs_valid(result) and parse.verdict is not None

  # should_retry: same read-back; unresolved → spend budget
```

### 3.2 The instance contract sheds the patch bytes (kills the placeholder)

Today's `unit_test_spec(patch: str | None)` conflates two things: *whether*
the script applies a patch (a boolean baked into the script) and *which
bytes* (a mount). The task-19 seam papered over that with a compile-time
placeholder — `patch=""`, then delete the empty mount — which is **correct
for SWE-Bench Pro only by implementation accident**: the script depends
only on `patch is not None` and reads the bytes from the `patch.diff`
workspace mount. Nothing in the contract forbids another dataset from
embedding the bytes *into* the script, which would make the placeholder
silently apply an empty patch and ignore the mounted real one.

Since every input now arrives by mount, the bytes parameter has no reason
to exist. The contract becomes explicit (this **changes
`TaskInstance.unit_test_spec` and `compile_unit_test`** — the ask-first
surface; this plan is the ask):

```python
def unit_test_spec(
    self,
    *,
    apply_patch: bool,
    patch_name: str = PATCH_NAME,        # which workspace file the script
    checkout_golden_tests: bool = True,  # applies; default = the store-name
) -> UnitTestSpec[V]:                    # contract ("patch.diff")
  """… When ``apply_patch``, the compiled script applies the workspace file
  named ``patch_name``; the bytes are NOT the spec's to carry — they arrive
  as the eval task's declared input. ``False`` grades the base commit
  untouched."""
```

- The filename is **task configuration threaded through**: the eval task
  carries `patch_name: str = PATCH_NAME` (static — `input_schema()` reads
  it, so the declaration side stays declaration-time), passes it into
  `unit_test_spec`, and `UnitTestSpec` records it as a field — the compiled
  spec self-describes which file its script reads. Consistency between the
  declared input and the compiled script is by construction, not by
  convention. The default keeps the edge contract with `DiffExtractObserver`
  (`patch.diff`); a custom name is for datasets whose harnesses expect
  another filename — knowingly opting out of that default edge match.
- `compile_unit_test` loses its `patch` parameter and the patch mount
  (gaining `patch_name`); the spec never carries patch bytes, so there is
  no placeholder to drop.
- The named invariant test:
  `test_the_eval_script_reads_the_patch_from_the_workspace_mount` — the
  compiled apply-mode script references exactly ``patch_name`` and embeds
  no bytes.
- `verify.py` migrates off the dying wrapper onto the task: golden run =
  `UnitTestEvalTask(inputs_builder=gold_patch)`, base run =
  `UnitTestEvalTask(apply_patch=False)` — the two self-check modes are the
  two standalone shapes, no special path.

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
  shell: str = "/bin/bash"             # the interpreter run_script uses —
                                       # every backend execs scripts, so the
                                       # knob is a run semantic, not a
                                       # backend mechanic


@dataclass(frozen=True)
class DockerHostSandboxConfig(SandboxConfig):
  """A-host mechanics: how THIS backend realizes a run."""
  workspace: epath.Path | None = None  # bind-mounted host dir
  pull: bool = True


@dataclass(frozen=True)
class GhjobSandboxConfig(SandboxConfig):
  """A-ghjob mechanics (the job container is already the sandbox)."""
  workspace: epath.Path | None = None
```

Deliberately flat (base + one subclass per backend, `workspace` repeated)
rather than an intermediate `LocalSandboxConfig` — a three-level hierarchy
for two backends is premature. A downstream backend brings its own
subclass, Resource-style ownership as before.

### 4.2 Who supplies what

| layer | supplies | as |
|---|---|---|
| **entry** (static definition) | run semantics | base `SandboxConfig` — `network=False` for eval, `pass_env=("CLAUDE_CODE_OAUTH_TOKEN",)` for the agent |
| **invocation** (CLI flags) | the backend + its mechanics | a backend-config *prototype*: `--backend host --no-pull` → `DockerHostSandboxConfig(pull=False)` |
| **runner** (per attempt) | the merge + the workspace | `replace(prototype, network=…, env=…, pass_env=…, shell=…, workspace=output_dir/"ws"/f"a{attempt}")` |

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

**Forward note — generic CLI overrides** (`--workflow.<entry>.sandbox.
<field>=value`, a later task): no generics and no class field are needed,
because **the config instance is the type carrier** — `type(config)` at run
time is the concrete class, `dataclasses.fields()` yields names and
annotated types for coercion, and `replace()` preserves the subclass.
Overrides apply at the one synthesis point (the merged prototype, just
before the factory call), so base-semantic fields, backend mechanics, and a
downstream subclass's own fields all ride one mechanism; an unknown field
errors listing the class's real ones. (A generic `WorkflowEntry[C]` would
NOT help: type parameters are erased at run time — `__orig_class__` exists
only for explicitly subscripted construction — so the instance, not the
annotation, is the reliable source.)

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

  **Three suppliers, one conflict rule.** With the builder added, an input
  can come from three places, and they are not redundant — each is the
  natural channel for a different *source of the bytes*:

  | supplier | the bytes come from | example |
  |---|---|---|
  | workflow edge | produced inside this run | rollout's `patch.diff` → eval |
  | caller `inputs` at `execute` | held by the invoker at run time | `--patch-file`, `--gold` sugar in the CLI |
  | `inputs_builder` | derivable from (instance, live sandbox) — part of the static definition | the default prompt; `gold_patch` in a registered self-check workflow |

  Conflicts stay pairwise-loud, no new rule needed: edge vs caller inputs is
  ordinary producer ambiguity (bind-time, explicit binding resolves);
  builder vs either is the §3.1 in-session collision error. One consequence
  worth a good error message: a chain that supplies `prompt.md` by edge must
  set the coding task's `inputs_builder=None` — the default builder would
  collide, on purpose, rather than silently losing to the edge. Gold grading
  legitimately exists in two flavors because the caller differs: the CLI's
  `--gold` feeds caller `inputs` (invocation-held), while a *registered*
  golden-verify workflow bakes `inputs_builder=gold_patch` (definition-held,
  what `verify.py` uses).
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

**Agent knobs (model / capture / bare) ride the same override
mechanism.** A definition bakes its default harness
(`ClaudeCodeHarness(model=DEFAULT_MODEL)`); the invocation adjusts fields
through the same dotted-path override the sandbox forward-note describes —
`--rollout.harness.model=…` — because everything on the path is a
dataclass: walk `fields(type(...))` segment by segment, coerce by the
annotated type, rebuild with nested `replace()` (immutability makes the
rebuild mechanical). One mechanism for sandbox mechanics, task config, and
harness knobs alike; `--model` stays as sugar for the common path. This
demotes builder-form definitions (`Callable[[RunOptions], WorkflowDef]`)
from the design: constant definitions + overrides cover the known knobs,
and a builder registry entry can be added later only if something
*structural* must vary per invocation.

**Swapping the harness itself** (Claude ↔ a future Codex harness) is not a
field override — it is a different class — and wants a harness registry
mirroring backends/stores (`--rollout.harness=codex` naming a registered
constructor). Deliberately deferred until a second harness exists; noted so
the override grammar reserves the bare-name form for it.

## 6. The CLI — DEFERRED, designed last

The concrete CLI sketch that stood here is void. The command surface will
get its own design round **after** the layers below it land, built around
the **generic dotted-path override grammar** (§5): statically-registered
workflows, dynamically adjusted per invocation —
`--<entry>.<field-path>=value` over tasks, harnesses, and sandbox configs
alike — rather than a hand-picked flag set. Nothing else about that UX is
decided here.

What this task still does to the CLIs, as **plumbing only**: the existing
`rollout` / `eval` commands keep their exact flags and summaries but are
re-plumbed internally onto the workflow machinery — required so the
wrappers (and with them the in-run retry, §7) can retire. No new commands,
no new flags, no removed flags in this task.

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
2. The `unit_test_spec(apply_patch=…)` contract change (§3.2): compile
   sheds the bytes parameter and the patch mount; invariant test; verify.py
   onto the task's two standalone shapes.
3. §7 retirement + ADR-0008 (its own commit; `Verdict` cleanup).
4. The `SandboxConfig` split (base semantics + per-backend subclasses;
   silent-ignore becomes loud rejection) — its own commit, backends and
   their tests updated.
5. `WorkflowEntry.sandbox` + prototype merge + runner synthesis (+
   config-subclass and type-mismatch tests); `run_task` signature;
   workspace allocation moves in.
6. Registry + built-in definitions + bind-time validation split (+ tests:
   declaration-time vs bind-time failures).
7. CLI plumbing: existing commands re-plumbed onto workflows, flags and
   summaries unchanged; wrappers deleted; CLI tests rebuilt over a
   registered fake backend (no monkeypatched wrappers).
8. Live smoke: `rollout --grade` (re-plumbed) on the flipt parity
   instance — agent + grade through the registry path; persisted keys
   inspected.
9. Docs: plans/README statuses; conventions command examples; ADR-0007
   §6 amendment note (budget location).

## 9. Risks & open questions

- **Validation moves from import time to bind time** for the output side —
  a registry full of workflows is syntax-checked at import, edge-checked on
  first bind. Accepted: instance-derived output schemas make it inherent;
  every failure still precedes any container.
- **The override grammar's coercion table** (str → field type) starts with
  the primitives + epath.Path and grows only with real needs; an
  unrepresentable field type simply is not overridable from the CLI.
- **Deleting the wrappers is breaking** for any downstream caller; the
  migration story is one page (construct a workflow, or call
  `Task.execute`), and prototyping was the agreed bar for compat.
- **Task-level `attempts`/`flaky` reach reports differently** — sweeps that
  read verdict-level flakiness must read record-level; called out for the
  downstream large-run pass this precedes.

---

## Result (2026-08-03)

Landed as five commits: the `SandboxConfig` split, late binding, per-attempt
sandbox synthesis, the eval/rollout contract change + retirement, and the
registry. All of §§1–5 and §7 shipped as designed. What differs from the design
above, and why:

- **`Verdict.attempts` / `flaky` are deleted, not reduced to constants.** §7
  said "reduce to constants at verdict level"; a field that can only hold one
  value reads as data and invites exactly the confusion the change is meant to
  end. It also said `run_task` would write a derived `flaky` into the final
  record — it does not: the runner is generic and cannot judge "resolved". The
  evidence is the attempt sequence the store already keeps (`a0` with
  `eval.resolved = 0`, `a1` with `1`), and the CLI prints `attempts` / `flaky`
  from the task run's own report, so both summaries keep their keys.
- **A task with an `inputs_builder` may declare an input nothing produces.**
  §5's three-supplier table implied this; phase A had to be taught it, or every
  standalone task would be refused as a dangling edge. An unproduced name with
  a builder present is left unbound and verified in-session; a name that *is*
  produced still binds by edge, and then the builder's collision check has the
  last word — the "must set `inputs_builder=None`" rule, enforced where the
  plan says.
- **`CodingAgentTask.inputs_builder` needs `field(..., kw_only=True)`.** The
  sketch redeclares the base field plainly, which silently drops its
  keyword-only status and orders a defaulted field ahead of `harness`
  (a `TypeError` at class creation).
- **`--pull` is filtered per backend at the CLI seam.** The config split made
  `build_sandbox("ghjob", …, pull=True)` a loud error, which would have broken
  `eval --backend ghjob` in CI, where the flag is never passed and its default
  still travels. `cli/sandbox_wiring.invocation_config` passes `pull` only to a
  config that declares it: a flag's *default* is not an instruction. The
  override grammar (task 23) subsumes this.
- **`--persist` decides *where*, not whether.** Task-level running always
  persists (that is what makes every attempt evidence, and how edges resolve),
  so a run without `--persist` writes to a throwaway store under its own output
  directory rather than to the shared T1 store. Both CLIs keep their flag and
  their `persisted` summary key.
- **The record keeps the facts the CLI used to write into it.** The diff-extract
  observer now reports `patch_is_empty` / `patch_binary_stripped` as metrics,
  because `run_task` owns the record and a post-run fact can no longer be
  injected by the command. The eval verdict's scalar summary entries
  (`output_state`, `first_missing`) are no longer copied into the shard —
  they are derivable from the persisted `output.json` artifact.
- **`CodingAgentTask` holds a proxy *factory*, not a proxy** (review
  follow-up). §3's sketch keeps the single-use recorder as a field, which
  contradicts the same task being registrable in a static definition and
  executed once per instance: the second execution would reuse a closed
  recorder. A task holds nothing a run dirties, so the field opens one per
  execution instead. The built-ins were unaffected (their factory is `None`),
  which is exactly why the boundary needed a test rather than an assumption.
- **The shipped definitions are named for the tasks they run**, not for the
  verb: `rollout`, `unit_test`, `rollout_and_unit_test` rather than §5's
  `solve` / `grade` / `solve_and_grade`. The entry keys, the store segments,
  the method package and the task classes already said *rollout* and
  *unit test*; a second vocabulary for the same two things buys nothing.
- **§6's CLI plumbing landed on hand-built workflows, not on `build_workflow`.**
  The registry ships and is tested, but the two commands still construct their
  own entries, because a registered definition bakes its harness and the
  invocation cannot yet adjust it. Moving them onto `build_workflow` is the
  first thing the override grammar buys (task 23).
