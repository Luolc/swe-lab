# Spec: SandboxRun — unified sandboxed-task engine + pluggable axes

> **Status: Draft — for review.** The design confirmed across the 2026-07-18
> interview + interface sessions. The **concept and the core interface are now
> settled**; the still-open items (naming, code placement, persistence, on-error
> specifics, sampling) are in [Open Questions](#open-questions). Do not proceed to
> the plan / implementation until this is approved.
>
> **Date:** 2026-07-18 · **Scope:** project-core (horizontal), consumed by every
> workstream. Names below (`SandboxManager`, `Sandbox`, …) are provisional — see
> Open Questions.

## Objective

Re-architect the project's execution core around **one sandboxed-task engine**
plus **three orthogonal plug-in axes**, replacing today's too-specific `rollout`
and `evaluation` subsystems.

- **What.** A `SandboxManager` owns a container's lifecycle and drives a composed
  set of **observers** around a single **main action** run in the container.
  *Solving* (rollout) and *grading* (eval) become two **configurations** of that
  one engine — a choice of main action + observers — not two subsystems.
- **Why now.** **Harness** plurality is imminent — **Codex** and **Grok Build**
  soon, not just Claude Code — and a harness is *not* a trivial adapter (it
  decides the in-container invocation, what to mount, and whether output is
  captured via **stream** or **proxy**). **Dataset** and **evaluation-method**
  are near-future second axes. Meanwhile the current code leaks layers,
  duplicates machinery, and misnames things.
- **Users.** Every workstream builds on this core: **W1** annotation, **W2**
  solve+eval, **W3** auditing. SWE-Bench-Pro unit-test grading is just *one*
  `(dataset × eval-method)` instance.
- **Success looks like.** Adding a harness / dataset / eval-method / cross-cutting
  concern is adding a **plug-in** (an observer or a main action), not a rewrite;
  `rollout` and `eval` share one engine; the flipt rollout + 731/731 gold sweep
  still pass.

## Assumptions

Correct any of these before we build on them:

1. **Harness is the primary axis** (Claude Code now; Codex, Grok Build next),
   dataset second (SWE-Bench Pro only today; a 2nd in the not-too-distant
   future), eval-method third (unit-test only today; model-/rubric-based later).
2. **A harness wraps a real off-the-shelf agent CLI** (mount its binary/config,
   write its invocation, capture stream-or-proxy) — unlike the sibling
   `locode-core`, which reimplements tools. The **patch is harness-agnostic**
   (`git diff` of the workdir).
3. **Execution = model A** (host-orchestrated persistent container: bring the
   container up, then drive it), realized by **two backends** —
   **A-host** (`docker create/start/exec/rm` on any runner, workspace
   bind-mounted) and **A-ghjob** (the GitHub job *is* the container). The old
   `docker run` one-shot ("B") is just the degenerate "everything in one exec"
   case, not a separate model.
4. **This effort implements W2** (rollout + eval) on the new core and **designs**
   the core so W1/W3 fit later; it does **not** migrate W1 annotation now.
5. **Sync** (not async) — matches the current code; the batch concurrency is at
   the job/matrix level (one sandbox per job). Revisit only if we build an
   in-process concurrent driver.

## Current state & motivation

Grounded in the code (2026-07-18). Today `rollout/` and `evaluation/` are separate
subsystems, each with a private copy of the same machinery, and the "general"
layer leaks SWE-Bench-Pro specifics. The concrete smells this redesign fixes:

- **`EvalSpec` conflates two things**: a *run context* (`image_ref, workdir,
  base_commit, instance_id`) and a *unit-test grading spec* (`run_script, parser,
  before_repo_set_cmd, tests`). `rollout` reads only the 4 run-context fields but
  carries all 10; grading hard-codes one dataset's string format
  (`before_repo_set_cmd.splitlines()[-1]`).
- **`evaluation` is a general word owning a specific (unit-test) implementation**
  — model-/rubric-based judgment is coming.
- **Duplicated machinery** between `rollout` and `evaluation` (workspace staging,
  `MOUNT_AT`/`ENTRYSCRIPT_NAME`/`PATCH_NAME` constants, divergent artifact names).
- **`proxy` is not legacy** — it is a capture *strategy* a future harness will
  need. Same for the agent error taxonomy.
- Naming: `Eval*` consumed by non-eval code, `Annotation*`-prefixed general
  errors, three meanings each of "run_script" and "patch", bare-string verdicts.

## The core model

A **`SandboxManager`** owns the container lifecycle and drives a composed set of
**observers**; a task is a choice of a **main action** (the body) + observers.
Adapted from a proven manager/observer pattern (an xAI/Hades sandbox lifecycle
manager), with one deliberate change (see the Sandbox note).

### The `Sandbox` hosts its own context

Because we own the whole stack, the **`Sandbox` *is* the context** — a dataclass
carrying the live handle + run state, yielded to the body and passed to every
observer hook. (The reference pattern kept a separate context that *held* the
sandbox only because the sandbox was externally owned; we don't need that.)

```python
@dataclass
class Sandbox:
    label: str
    workspace: Path                  # host-visible dir shared into the container
                                     #   A-host → `docker create -v` mount · A-ghjob → local dir
    backend: SandboxBackend          # A-host | A-ghjob
    artifacts: dict[str, Any] = {}   # observers + body read/write (patch, trace, result, metrics)
    error: Exception | None = None
    metadata: dict[str, Any] = {}
    def exec(self, script, *, timeout, env=..., stream_to=None) -> ExecResult: ...  # run in the container
```

### The observer — the original five hooks only

No per-phase hooks: `after_create`→`before_main` (and `after_main`→
`before_destroy`) had nothing between them, so they were redundant. The five
lifecycle hooks are enough; most observers touch just `after_create` and/or
`before_destroy`.

```python
class SandboxObserver:                # all no-op by default; override what you need
    def before_create(self, sb): ...
    def after_create(self, sb): ...       # sandbox is up → SETUP runs here
    def before_destroy(self, sb): ...     # EXTRACT / PERSIST / eval-parse — ALWAYS run (finally)
    def after_destroy(self, sb): ...
    def on_error(self, sb): ...           # failure metrics; may `exec` into the still-live sb

class CompositeObserver(SandboxObserver): # fan out to a list, in registration order
    observers: list[SandboxObserver]
```

### The manager yields the sandbox; `main` is the body

```python
with manager.sandbox() as sb:                 # before_create → backend.up → after_create (setup ran)
    sb.artifacts["trajectory"] = run_agent(sb)     # the ONE action: rollout — or run_eval_script(sb): eval
# on exit → before_destroy observers ALWAYS run (extract → sb.artifacts["patch"]; persist);
#           on any exception → on_error observers (exec metrics into the live sb); then teardown
```

**Post-processing always runs** (the `finally` semantics) — we deliberately keep
it simple and have **no** main→teardown signal for now; add a conditional-skip
later only if a real case needs it. On error, `before_destroy` still runs, so its
observers may guard on `sb.error` where relevant.

### Phase → hook mapping (nothing beyond the five hooks)

| Concern | Where | Provided by |
|---|---|---|
| setup script (per instance; `exit 0` or abort) | `after_create` observer | dataset |
| **main** — agent run (rollout) / eval script (eval) | the **body** | harness / eval-method |
| diff-extract | `before_destroy` observer | shared |
| trace capture | around the body / `before_destroy` | harness |
| eval parse → result | `before_destroy` observer | eval-method |
| persist artifacts | `before_destroy` observer | shared |
| metrics / logging / retry | across hooks + `on_error` | shared, reusable |

### The three axes compose a task

- **Harness** (Claude Code / Codex / Grok Build) → the `main` body (the agent run:
  invocation, mounts, **capture = stream \| proxy**) + a trace observer.
- **Dataset** (SWE-Bench Pro / future) → `image/workdir/base_commit` + a setup
  observer + (for eval) the grading inputs.
- **Eval method** (unit-test now; model/rubric later) → the `main` body (run the
  eval script) + an eval-parse observer.

`rollout` and `eval` are two such compositions. The engine
(`SandboxManager` + observers) is harness-, dataset-, and eval-method-agnostic.

### Two backends, one interface

`SandboxBackend` is implemented by **A-host** (`docker create/start/exec/rm`,
workspace bind-mounted, host outside the container) and **A-ghjob** (the job *is*
the container; workspace is a local dir) — the manager and observers are
backend-agnostic. Files move via the **shared `workspace` dir** (bind-mount for
A-host, local for A-ghjob) — no `docker cp`.

### Decoupling that follows

- **Split `EvalSpec`** into a small shared **run context** (image/workdir/
  base_commit/id) and an **eval-method-specific spec** (the unit-test grading
  inputs). rollout takes only the run context; the unit-test evaluator takes
  run-context + its own spec; a future model-judge takes run-context + a rubric.
- **Free the general words**: `evaluation` stays the general axis; the current
  implementation becomes a named method (e.g. `unit_test`). Fix the `Eval*` /
  `Annotation*` / `run_script` / `patch` overloads (final names → Open Questions).
- **De-duplicate** rollout/eval staging + constants + artifact-naming into the
  engine.
- **`proxy` + the agent error taxonomy are first-class** capture/observability
  pieces, not W1 leftovers.

## Tech Stack

Unchanged baseline: Python 3.13 + uv; **sync** subprocess driving
`docker create/start/exec/rm` (A-host) or in-job commands (A-ghjob); prebuilt
jefzda images (`linux/amd64`); git for patch extraction. The existing `core/`
infra (`DockerProvider`, `patch`, `agent/{binary,trace,proxy}`, `datasets/loader`)
is refactored *into* this model.

## Commands

The CLIs become thin configurations over the engine (exact surface → Open
Questions). Today's, for reference:

```sh
python -m swe_lab.rollout <instance_id> [--grade] [--model] [--timeout] [--no-pull]
python -m swe_lab.evaluation <instance_id> (--gold | --patch-file <p>) [--no-network]
python -m swe_lab.evaluation.verify --shard i/N [--aggregate]   # golden sweep
```

## Project Structure (PROPOSED — placement is an Open Question)

A sketch to react to, **not** decided (Open Questions #2). The axes suggest
per-axis packages parallel to the existing `datasets/`:

```
core/
  sandbox/        SandboxManager, Sandbox, SandboxObserver, CompositeObserver, backends (A-host, A-ghjob)
  harnesses/      one per harness: claude_code/ (now), codex/, grok_build/ (next)
  datasets/       one per dataset: swebench_pro/ (now)   [exists]
  evaluation/     the eval axis; methods/ (unit_test/ now, model_judge/ later)
  <shared observers: setup, diff-extract, persist, metrics, logging>
```

Open: top-level vs under `core/`; how to split the Claude-Code-specific `agent/*`
into general-sandbox vs the `claude_code` harness; whether `rollout`/`evaluation`
remain as thin CLI entrypoints.

## Code Style

Repo conventions unchanged: pyink (2-space, line 80), strict camelCase acronyms
(`SweBenchProInstance`), typed frozen dataclasses for records, `Protocol`s for
seams. Each observer / backend / axis plug is a small self-contained module; the
engine never imports a concrete harness/dataset/eval-method.

## Testing Strategy

- **Engine unit tests** — manager lifecycle + observer ordering + the failure
  matrix (which hooks fire when create fails vs body fails vs on_error) — against
  a **fake `SandboxBackend`** (no Docker), so the lifecycle is tested with zero
  container spend.
- **Observer/backend contract tests** in isolation.
- **Integration checkpoints that must not regress**: flipt rollout still produces
  a graded patch; `python -m swe_lab.evaluation --gold` still resolves; the gold
  sweep stays 731/731.
- Existing quality bar (`uv run pytest` + `uv run pre-commit`) stays the gate.

## Boundaries

- **Always:** keep the manager/observers harness-/dataset-/eval-method-agnostic;
  each plug self-contained; never break the working flipt rollout / gold sweep;
  log any dropped artifact (no silent loss).
- **Ask first:** final naming; the code organization/placement; the persistence
  mechanism; on-error specifics; adding a conditional-teardown signal back.
- **Never:** leak a specific harness/dataset/eval-method into the engine;
  re-conflate run-context with grading spec; treat `proxy` as removable.

## Success Criteria

1. `rollout` and `eval` are two **configurations of one `SandboxManager`** — a
   main action + observers — with no duplicated staging / constants / artifact
   naming.
2. **`EvalSpec` is split**: rollout consumes only a run context; the unit-test
   evaluator consumes run-context + its own grading spec.
3. The three axes exist as plug points; **claude_code × swebench_pro × unit_test**
   works end-to-end, and a **second harness** (or a stub) registers **without
   touching the engine**.
4. Both backends work behind one `Sandbox` (A-host locally / in CI; A-ghjob in a
   container job), and `proxy` is available as a harness capture strategy.
5. Regression-free: flipt rollout still yields a graded patch; gold sweep still
   731/731; `pytest` + `pre-commit` green.

## Out of scope

- Migrating **W1 annotation** onto the engine (designed-for, not done now).
- A **2nd dataset** or **2nd eval-method** implementation (design the seams;
  don't build them yet).
- A conditional-teardown **signal** (dropped for simplicity; revisit if needed).
- The deferred **mechanics** below.

## Open Questions

Settled this session and **removed** from here: the execution model (A, two
backends), the engine interface (manager/observer, five hooks, yield the sandbox,
sync, always-post-process). Still open:

1. **Naming** — `SandboxManager` / `Sandbox` / `SandboxObserver`? the split names
   for `EvalSpec` (run-context vs grading-spec)? the eval-method rename?
2. **Code organization / placement** — top-level vs `core/`; splitting the
   Claude-Code-specific `agent/*` into general-sandbox vs `claude_code`; whether
   `rollout`/`evaluation` survive as thin CLIs; final package names.
3. **Persistence** — how artifacts leave the ephemeral sandbox (Hugging Face, à
   la W1 traces? elsewhere?) and when (a `PersistObserver` in `before_destroy`).
4. **On-error specifics** — what intermediate metrics an `on_error` observer
   collects and how (it `exec`s into the still-live sandbox).
5. **Sampling / batching** — N-sample rollout and the matrix sweep sit *above* a
   single sandbox run; confirm they're orchestration, not engine.
```
