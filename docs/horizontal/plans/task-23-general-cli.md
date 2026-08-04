# Task 23 — The general CLI: registered workflows, dotted-path overrides

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Picks up the surface [task 22](task-22-late-binding-workflows.md) deferred:
> §6 there voided the CLI sketch on purpose, and §5 recorded the override
> grammar this task designs.

---

## 1. Purpose & scope

Task 22 made a workflow a **statically-writable definition** and put three of
them in a registry (`solve`, `grade`, `solve_and_grade`). Nothing invokes them
by name yet: both commands still hand-build their entries, because a
definition bakes its harness and an invocation has no way to adjust it.

This task builds that way — **one generic mechanism**, not a hand-picked flag
per knob — and puts the shipped commands on it.

```
swe-lab run solve_and_grade instance_flipt-io__flipt-6fe76d0 \
    --backend host --no-pull --persist --sweep smoke \
    --rollout.harness.model=opus --eval.retries=2
```

### In scope

- `swe-lab run <workflow> <instance>`: the one command that runs anything
  registered.
- The **override grammar** `--<entry>.<field-path>=<value>`, its resolution
  rules, its coercion table, and its errors.
- The summary + exit-code contract for a workflow run.
- Retiring `rollout` / `eval` as separate commands (§7), including the CI
  workflow files that call them.

### Out of scope

- **K-rollout sweeps** (`--rollout-id` exists; driving K of them is a runner,
  not this command).
- **A harness registry** (`--rollout.harness=codex`): the grammar reserves the
  form, but nothing registers a second harness yet (§6).
- DAG scheduling, `optional` entries (unchanged deferrals).

---

## 2. Why a grammar rather than flags

The flag-per-knob approach does not survive the layering task 22 built. A
workflow's shape is `entries × (task fields, harness fields, sandbox config,
timeout, retries)`, all of them dataclasses, and *every* level already has
knobs a run might want to change. Enumerating them as flags means:

- the same `--model` flag has to mean "the model of whichever entry happens to
  be the agent", which stops being true the moment a workflow has two agents;
- a downstream user's own task or backend config is unreachable from the CLI
  without editing swe-lab;
- each new field is a CLI change, a help-text change, and a plumbing change.

Everything on the path is a **frozen dataclass**, so one mechanism covers all
of it: walk the path by `dataclasses.fields`, coerce the leaf by its annotated
type, rebuild with nested `replace()`. That is the whole design; the rest of
this document is the rules that make it predictable.

---

## 3. The command

```
swe-lab run <workflow> <instance> [options] [overrides]
```

| option | meaning |
|---|---|
| `--dataset` | dataset the instance belongs to (default `swebench_pro`) |
| `--backend` | which registered sandbox backend to build on (default `host`) |
| `--pull / --no-pull` | image pull, where the backend has the knob (task 22's `invocation_config` rule) |
| `--sweep` | sweep id the records are keyed under (default `adhoc`) |
| `--rollout-id` | which sample of the instance (default `0`) |
| `--persist / --no-persist` | T1 store, or a throwaway one under the run directory |
| `--resume / --no-resume` | honor terminal markers (default **`--no-resume`**: a one-off command re-runs) |
| `--input NAME=PATH` | a caller-supplied workflow input, repeatable (§5) |
| `--output-dir` | where the run's attempts and workspaces land (default: the cache) |

`swe-lab run --list` (or `swe-lab workflows`) prints the registered names with
their entry keys — the discoverability the registry buys.

**Open question (for review): the command's name.** `run` is short and
honest; `swe-lab run solve_and_grade <id>` reads well. The alternative is to
let the *workflow* be the subcommand (`swe-lab solve_and_grade <id>`), which
reads better still but makes every registered name a top-level command,
including a downstream user's — a namespace we do not control.

---

## 4. The override grammar

### 4.1 Shape

```
--<entry>.<field>[.<field>…]=<value>
```

Click cannot declare options it has not seen, so the command is written with
`context_settings={"ignore_unknown_options": True, "allow_extra_args": True}`
and parses the leftovers itself. Two consequences worth stating:

- a typo'd *option* (`--rollout.harnes.model=opus`) reaches our parser rather
  than Click's, so **we** own the error message — which is the point (§4.4);
- a typo'd *known* flag (`--persits`) is no longer caught by Click either. The
  parser therefore refuses any extra argument that does not contain `=`, and
  names the registered entries in the error.

**Alternative considered:** a repeatable `--set <path>=<value>`, which keeps
Click's own parsing intact and needs no `ignore_unknown_options`. It is
strictly safer and one word noisier per override. Recorded here because the
safety argument is real; the dotted form is chosen for how much it is typed.

### 4.2 What a path resolves against

The first segment is a **workflow entry key**. After that the walk starts at
the `WorkflowEntry` and falls through to its task:

| path | resolves to |
|---|---|
| `rollout.timeout=600` | `WorkflowEntry.timeout` |
| `rollout.retries=2` | `WorkflowEntry.retries` |
| `rollout.sandbox.network=false` | `WorkflowEntry.sandbox.network` |
| `rollout.harness.model=opus` | the entry's **task**'s `harness.model` |
| `rollout.task.harness.model=opus` | the same, spelled unambiguously |
| `eval.patch_name=candidate.diff` | the eval **task**'s `patch_name` |

Fall-through is what makes the common case short. Its cost is a shadowing
rule: an entry field wins over a task field of the same name (`timeout`,
`retries`, `sandbox`, `inputs`, `key`, `task`), and `--<entry>.task.<field>`
always reaches the task. The help text lists the six shadowed names.

**Alternative considered:** no fall-through — every task field spelled
`--rollout.task.…`. Unambiguous, and rejected because the overwhelmingly
common override *is* a task field, and `.task.` in every one of them is pure
ceremony.

### 4.3 Coercion

The leaf's annotated type decides:

| annotation | accepted |
|---|---|
| `str` | verbatim |
| `int` / `float` | Python literal |
| `bool` | `true/false`, `1/0`, `yes/no` (case-insensitive) |
| `epath.Path` | any path-like string |
| `X \| None` | `none` → `None`, else coerce as `X` |
| `StrEnum` (e.g. `Capture`) | by value |
| `tuple[str, ...]` / `Sequence[str]` | comma-separated |
| `Mapping[str, str]` | `k=v,k=v` (values may not contain `,` or `=`) |

Anything else — a callable, a nested non-dataclass, a union of two concrete
types — is **not overridable**, and says so by name and type. The table grows
only when a real field needs it.

### 4.4 Errors

Every failure is refused **before the workflow runs**, and names what it saw
against what exists:

```
--rollout.harnes.model: 'harnes' is not a field of CodingAgentTask
  (harness, inputs_builder, extra_inputs, exclude_globs, agent_env, proxy)
  or of WorkflowEntry (key, task, timeout, sandbox, inputs, retries)
```

```
--eval.timeout=soon: 'soon' is not a float (WorkflowEntry.timeout)
```

```
--lint.timeout=60: workflow 'solve_and_grade' has no entry 'lint'
  (entries: rollout, eval)
```

### 4.5 Where they apply

One point, on the built workflow, before `execute`: each override rebuilds its
entry with nested `replace()`, and the workflow is rebuilt from the new
entries — so the declaration-time validation (task 22 §5) runs again over the
*overridden* definition, and an override that breaks a binding is refused
there rather than mid-run.

Sandbox overrides land on `WorkflowEntry.sandbox`, which the runner then
merges onto the invocation's prototype exactly as it does today: the override
changes what the entry *declares*, which is what an override should mean.

---

## 5. Caller inputs, and where `--gold` goes

`grade` needs a patch from outside the run. That is `Workflow.execute(inputs=)`
and it gets one generic flag:

```
--input patch.diff=./candidate.diff        # repeatable, NAME=PATH
```

**`--gold` stops being a flag and becomes a workflow.** Grading the reference
solution is a *definition* — the eval task with `inputs_builder=gold_patch`
(task 22 §3.1) — so it registers as `gold_grade` and is invoked like anything
else:

```
swe-lab run gold_grade <instance>
```

This is the pattern the whole task is for: a variant that used to need a flag
and a branch in the command is now four lines of definition, and the command
does not know it exists. `verify.py`'s golden run is the same definition,
which is worth checking as we go (it runs `Task.execute` directly today, and
should keep doing so — it has its own store layout).

---

## 6. The harness swap, reserved

Changing the agent is not a field override — it is a different class — and the
grammar reserves the **bare-name form** for it:

```
--rollout.harness=codex            # a registered harness constructor
--rollout.harness.model=opus       # a field on whatever harness is there
```

Deferred until a second harness exists (`ClaudeCodeHarness` is the only one).
When it lands, it is a registry mirroring backends/stores/workflows, and the
rule is: a bare name whose target field is a **non-dataclass-leaf** looks up
the registry; anything deeper is a field walk. Both forms in one command
resolve the swap first, then the fields — otherwise `--rollout.harness=codex
--rollout.harness.model=…` would set a field on the harness being replaced.

---

## 7. What happens to `rollout` and `eval`

**They are deleted.** Both are `run` invocations once this lands:

| today | after |
|---|---|
| `swe-lab rollout <id>` | `swe-lab run solve <id>` |
| `swe-lab rollout <id> --grade` | `swe-lab run solve_and_grade <id>` |
| `swe-lab rollout <id> --model X --capture proxy` | `… --rollout.harness.model=X --rollout.harness.capture=proxy` |
| `swe-lab eval <id> --gold` | `swe-lab run gold_grade <id>` |
| `swe-lab eval <id> --patch-file p.diff` | `swe-lab run grade <id> --input patch.diff=p.diff` |
| `swe-lab eval <id> --eval-retries 2` | `… --eval.retries=2` |

The two `.github/workflows/*-ghjob.yml` files move with them, in the same PR.
`promote` is untouched (it is a store operation, not a run).

**Alternative considered:** keep both as thin aliases. Rejected for the
prototyping-stage reason the repo already applies elsewhere — two surfaces
means two things to keep true, and the aliases would immediately drift from
the definitions they wrap. (If a shorthand proves missed, it comes back as a
*registered workflow name*, not as a second code path.)

---

## 8. The summary and the exit code

One shape for every workflow, derived from what the run already records — no
command-specific assembly:

```json
{
  "workflow": "solve_and_grade",
  "instance_id": "instance_flipt-io__flipt-6fe76d0",
  "succeeded": true,
  "entries": [
    {"key": "rollout", "status": "succeeded", "attempts": 1,
     "metrics": {"agent_complete": 1.0, "patch_is_empty": 0.0},
     "artifacts": {"patch.diff": "adhoc/…/rollout/a0/patch.diff"}},
    {"key": "eval", "status": "succeeded", "attempts": 2,
     "metrics": {"eval.score": 1.0, "eval.resolved": 1.0}}
  ],
  "record_key": "adhoc/…/r0/workflow.json"
}
```

Metrics carry the answer, so the command needs no verdict-shaped knowledge:
`eval.resolved` is already there because the eval observer reports it. The
instance's `run_provenance()` is merged in at the top level, as today.

**Exit codes** — three, because "did it run" and "did the patch pass" are
different questions and one code cannot say both:

| code | meaning |
|---|---|
| `0` | the workflow completed; nothing graded it as failing |
| `1` | a task failed, an edge failed, or the run was refused |
| `2` | the workflow completed and a graded verdict was **unresolved** |

**Open question (for review):** `2` is a behavior change from
`rollout --grade`, which exits `1` on an unresolved patch. CI that greps the
exit code would need updating; the alternative is to keep `1` for both and
lose the distinction.

**Open question (for review): should a task contribute report detail?** The
metrics-only summary above cannot print `first_missing` or `output_state`
(they are strings, not metrics). A fourth hook — `Task.report(result) ->
Mapping[str, object]` — would let it, at the cost of another thing a task
must implement. Recommendation: **not now**; the artifacts are persisted and
`--persist` prints their keys, which is where a human goes for detail anyway.

---

## 9. Steps

1. `overrides.py`: parse `<entry>.<path>=<value>`, walk `fields(type(obj))`,
   coerce by annotation, rebuild with nested `replace()`. Pure, and tested
   directly — this is where the design's weight is.
2. Apply-to-workflow: entry-then-task fall-through, re-validation of the
   overridden definition, error messages that name the alternatives.
3. `swe-lab run`: the command, `--input`, the summary, the exit codes.
4. `gold_grade` as a registered definition; `verify.py` checked against it.
5. Delete `rollout` / `eval`; move the two CI workflow files; update
   `docs/conventions.md`'s command examples.
6. Live smoke: `run solve_and_grade` on the flipt parity instance with an
   override that demonstrably changes the run (`--rollout.harness.model=…`).

## 10. Risks & open questions

- **`ignore_unknown_options` weakens Click's own typo detection** for the
  fixed flags (§4.1). Mitigated by refusing any extra argument without `=`.
- **The coercion table is a growing surface.** It starts at the primitives and
  grows only when a field needs it; an unrepresentable field type is simply
  not overridable, which is honest and reversible.
- **Overrides can express nonsense the definition would have caught** — e.g.
  `--eval.inputs=…` pointing at a producer that does not exist. Re-validating
  the overridden workflow (§4.5) catches exactly the class the declaration
  check catches; the bind-time class is caught at bind time, as always.
- **A downstream user's task fields become CLI surface** the moment their
  workflow is registered. That is the intent, and it means field *names* are
  now part of a public contract — worth a line in the extensibility guide.
