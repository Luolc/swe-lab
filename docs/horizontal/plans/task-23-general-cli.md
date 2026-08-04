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
them in a registry (`rollout`, `unit_test`, `rollout_and_unit_test`). Nothing
invokes them
by name yet: both commands still hand-build their entries, because a
definition bakes its harness and an invocation has no way to adjust it.

This task builds that way — **one generic mechanism**, not a hand-picked flag
per knob — and puts the shipped commands on it.

```
swe-lab run rollout_and_unit_test instance_flipt-io__flipt-6fe76d0 \
    --backend host --no-pull --persist --sweep smoke \
    --rollout.harness.model=opus --unit_test.retries=2
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

Everything on the path is **dataclass-shaped configuration**, so one mechanism
covers all of it: walk the path by `dataclasses.fields`, coerce the leaf by its
annotated type, rebuild with nested `replace()`. That is the whole design; the
rest of this document is the rules that make it predictable.

Frozen-ness is *not* what makes this work, and the levels differ: `WorkflowEntry`,
`SandboxConfig` and `ClaudeCodeHarness` are frozen; the tasks are plain
`@dataclass` (they carry a mutable `Mapping` field or two). `replace()` works on
any dataclass, and the override path only ever *rebuilds* — it never assigns
through a reference — so a mutable task is rebuilt exactly like a frozen config
and the original object a definition holds is never touched. That last property
is what a registry needs: overriding a run must not edit the definition every
other run will use.

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
honest; `swe-lab run rollout_and_unit_test <id>` reads well. The alternative
is to let the *workflow* be the subcommand (`swe-lab rollout_and_unit_test
<id>`), which
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
| `unit_test.patch_name=cand.diff` | the grading **task**'s `patch_name` |

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
--unit_test.timeout=soon: 'soon' is not a float (WorkflowEntry.timeout)
```

```
--lint.timeout=60: workflow 'rollout_and_unit_test' has no entry 'lint'
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

### 4.6 The contract, precisely

The rules a reader of the implementation should not have to infer:

**Annotations are resolved, not read.** Every module here uses
`from __future__ import annotations`, so `Field.type` is a *string*. Coercion
walks `typing.get_type_hints(type(obj))` instead (cached per class) and looks
each field's real type up in it. A field whose annotation cannot be resolved —
a forward reference to something not importable at run time — is reported as
not overridable, by name, rather than coerced by guesswork.

**A repeated path is refused, not last-one-wins.** Two
`--rollout.harness.model=` on one command line is a typo or a script bug, and
silently keeping the last one hides both. Duplicates are an error everywhere
else in this codebase (mount targets, output names, bindings); this is the same
rule.

**Ordering is not the caller's problem.** All overrides are parsed first,
conflicts refused, and only then applied — shortest path first. That single
ordering rule subsumes §6's swap-then-fields case: `--rollout.harness=codex`
is a shorter path than `--rollout.harness.model=o3`, so the swap lands before
the field, and no special case is needed to say so. Nothing else in the set can
interact, because a repeated path is already refused.

**Two fields are not overridable, by name and with the reason.**

- `key` — an entry's identity: the store segment its records live under, what
  resume matches, and what every binding on later entries names. Changing it
  while resolving other overrides against the old name is order-dependent in
  the one way that matters, and it silently re-homes a run's records.
- `task` — a `Task` is not a value a command line can spell. (Swapping the
  *harness* inside it is §6's registry form.)

`inputs` (the binding list) *is* overridable — it coerces as a string sequence,
and §4.5 re-validates the rebuilt workflow — so an ambiguity that only shows up
for one invocation can be resolved without editing the definition.

**Values are checked for meaning, not just for type.** `timeout=nan` and
`retries=-1` coerce fine and are nonsense. The check belongs on
`WorkflowEntry.__post_init__` (which already refuses a declared workspace), not
in the override layer: an entry built by hand deserves the same refusal as one
built by a flag. So step 2 adds it there — `timeout` finite and `> 0`,
`retries >= 0` — and the CLI inherits it for free, reporting it as a refused
override.
---

## 5. Workflows that need a value from you

Registered definitions come in two kinds, and the command has to make the
difference visible rather than let the second kind look broken:

| kind | runnable from | examples |
|---|---|---|
| **self-sufficient** | `(name, instance)` alone | `rollout`, `rollout_and_unit_test`, `gold_unit_test` |
| **parameterized** | `(name, instance)` + a value the invoker holds | `unit_test` — *which* patch? |

`unit_test` alone genuinely runs nothing, and that is correct: it grades a
patch someone else produced (a previous sweep's, a competitor's, a hand-written
one), and only the invoker knows which. Today it does not even bind — the
declared `patch.diff` has no producer, so the workflow is refused before any
container, with an engine-flavored message:

```
nothing produces 'patch.diff', required by unit_test: no earlier entry declares
it, the workflow's inputs do not provide it, and the task builds no inputs of
its own
```

That is the right *behavior* and the wrong *sentence* for someone typing a
command. Three things fix the ergonomics, and none of them is a per-workflow
flag:

1. **The command supplies inputs generically**, feeding
   `Workflow.execute(inputs=)`:

   ```
   swe-lab run unit_test <id> --input patch.diff=./candidate.diff
   ```

2. **One unbound input needs no name.** When exactly one required input is
   unbound after binding — the case for every workflow we ship or foresee —
   the name may be omitted:

   ```
   swe-lab run unit_test <id> --input ./candidate.diff
   ```

   With two or more, `NAME=PATH` is required and the error lists them. The
   rule is mechanical, so nothing is guessed: a store name is an edge-contract
   detail, and a person grading one patch should not have to know it.

   A repeated `--input` for one name is refused, like a repeated override: two
   values for one input is a mistake in either direction, and picking one
   hides it.

3. **The refusal names what it wants, using the schema's own description**, and
   `--list` / `--help` show it up front:

   ```
   workflow 'unit_test' needs an input you did not supply:
     patch.diff — the candidate patch to grade
   supply it with:  --input ./your.diff        (or --input patch.diff=./your.diff)
   ```

### `--gold` stops being a flag and becomes a workflow

Grading the reference solution is a *definition*, not a mode: the unit-test
task with `inputs_builder=gold_patch` (task 22 §3.1). It registers as
`gold_unit_test` and is invoked like anything else:

```
swe-lab run gold_unit_test <instance>
```

This is the pattern the whole task is for — a variant that used to need a flag
and a branch inside the command is four lines of definition, and the command
does not know it exists. It also *cannot* be the same definition as
`unit_test`: a task that builds its own patch cannot also be handed one (the
in-session collision is deliberate), which is exactly why the two are separate
names rather than one name with a switch.

`verify.py`'s golden run is the same shape, and is worth checking against this
definition as we go — it runs `Task.execute` directly today and should keep
doing so, since it owns its own store layout.

**Alternative considered — a patch-file *field* instead of an input.** A
builder could be a small dataclass (`PatchFile(path=…)`), making the patch
reachable by the override grammar alone (`--unit_test.inputs_builder.path=…`)
and the workflow self-sufficient after overrides. Rejected: it moves "where
the bytes come from" back into the task's static configuration — exactly what
task 22 took out of it — and a task carrying such a builder can no longer be
the tail of the chain, so the one shared grading entry would have to fork into
two. `--input` keeps inputs as inputs, and generalizes to any workflow with an
unbound one (a future task consuming `plan.md` gets it for free).

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
the registry; anything deeper is a field walk. Both forms in one command work
without a rule of their own — §4.6 applies shorter paths first, so the swap
lands before any field set on it, rather than setting a field on the harness
being replaced.

---

## 7. What happens to `rollout` and `eval`

**They are deleted.** Both are `run` invocations once this lands:

| today | after |
|---|---|
| `swe-lab rollout <id>` | `swe-lab run rollout <id>` |
| `swe-lab rollout <id> --grade` | `swe-lab run rollout_and_unit_test <id>` |
| `swe-lab rollout <id> --model X --capture proxy` | `… --rollout.harness.model=X --rollout.harness.capture=proxy` |
| `swe-lab eval <id> --gold` | `swe-lab run gold_unit_test <id>` |
| `swe-lab eval <id> --patch-file p.diff` | `swe-lab run unit_test <id> --input patch.diff=p.diff` |
| `swe-lab eval <id> --eval-retries 2` | `… --unit_test.retries=2` |

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
  "workflow": "rollout_and_unit_test",
  "instance_id": "instance_flipt-io__flipt-6fe76d0",
  "succeeded": true,
  "entries": [
    {"key": "rollout", "status": "succeeded", "attempts": 1,
     "metrics": {"agent_complete": 1.0, "patch_is_empty": 0.0},
     "artifacts": {"patch.diff": "adhoc/…/rollout/a0/patch.diff"}},
    {"key": "unit_test", "status": "succeeded", "attempts": 2,
     "metrics": {"unit_test.score": 1.0, "unit_test.resolved": 1.0}}
  ],
  "record_key": "adhoc/…/r0/workflow.json"
}
```

Metrics carry the answer, so the command needs no verdict-shaped knowledge:
`unit_test.resolved` is already there because the method's observer reports
it. The
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
2. Apply-to-workflow: entry-then-task fall-through, the two non-overridable
   fields, re-validation of the overridden definition, and error messages that
   name the alternatives. Adds `WorkflowEntry.__post_init__` validation for
   `timeout` (finite, `> 0`) and `retries` (`>= 0`) — an entry deserves the
   same refusal however it was built (§4.6).
3. `swe-lab run`: the command, `--input`, the summary, the exit codes.
4. `gold_unit_test` as a registered definition; `verify.py` checked against it.
5. Delete `rollout` / `eval`, and move **everything that invokes or documents
   them** in the same change — the command is the contract, so a stale copy is
   a broken instruction:
   - the three active GitHub workflows: `rollout.yml`, `rollout-ghjob.yml`,
     `eval-ghjob.yml` (`verify-golden.yml` runs `…swebench_pro.verify` and is
     untouched);
   - `docs/conventions.md`'s command block;
   - `docs/workstreams/w2-solve-eval/README.md`'s CLI line;
   - `docs/horizontal/spec.md`'s Commands section — and the rest of the
     spec reconciled, which a status change on this task requires anyway
     (`AGENTS.md`: reconcile the spec at each checkpoint);
   - a repo-wide search for the old invocations afterwards
     (`rg -n "swe_lab (rollout|eval)"`), because the ones above are the ones
     we know about.

   Historical `plans/task-*.md` stay as they are: point-in-time records, not
   instructions.
6. Live smoke: `run rollout_and_unit_test` on the flipt parity instance with an
   override that demonstrably changes the run (`--rollout.harness.model=…`).

## 10. One thing this task makes reachable, so it must fix it

`CodingAgentTask.proxy_factory` is built by the CLI as a closure over the
**first attempt's** workspace (`…/rollout/ws/a0`), because that is where the
in-container conversion reads the recording from and only the runner knows the
per-attempt path. Nothing can reach the bug today: the rollout entry has no
retry budget and no flag exposes one.

`--rollout.retries=2` exposes one. A second attempt would then record into the
first attempt's directory, and its trace would be silently wrong.

The fix is not a bigger closure — it is that a recorder is an **observer**, not
a context manager around the action: `before_create` opens it, `before_destroy`
closes it, and it receives the sandbox it is recording for, which is the only
thing that knows the attempt's workspace. That is a small change to the coding
task and deletes `proxy_factory` along with the a0 assumption; it belongs in
this task because this task is what makes it reachable.

## 11. Risks & open questions

- **`ignore_unknown_options` weakens Click's own typo detection** for the
  fixed flags (§4.1). Mitigated by refusing any extra argument without `=`.
- **The coercion table is a growing surface.** It starts at the primitives and
  grows only when a field needs it; an unrepresentable field type is simply
  not overridable, which is honest and reversible.
- **Overrides can express nonsense the definition would have caught** — e.g.
  `--unit_test.inputs=…` pointing at a producer that does not exist. Re-validating
  the overridden workflow (§4.5) catches exactly the class the declaration
  check catches; the bind-time class is caught at bind time, as always.
- **A downstream user's task fields become CLI surface** the moment their
  workflow is registered. That is the intent, and it means field *names* are
  now part of a public contract — worth a line in the extensibility guide.
