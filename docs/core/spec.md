# Spec: SandboxRun — unified sandboxed-task engine + pluggable axes

> **Status: Draft — for review.** This writes down the design confirmed in the
> 2026-07-18 design interview. The **concept** is settled; the concrete
> **mechanics** (exact interface, persistence, on-error, code placement) are
> deliberately deferred — see [Open Questions](#open-questions). Do not proceed
> to the plan / implementation until this is approved.
>
> **Date:** 2026-07-18 · **Scope:** project-core (horizontal), consumed by every
> workstream.

## Objective

Re-architect the project's execution core around **one unified sandboxed-task
lifecycle** plus **three orthogonal plug-in axes**, replacing today's
too-specific `rollout` and `evaluation` subsystems.

- **What.** A single `SandboxRun` engine runs any task inside a container as a
  fixed lifecycle (provision → setup → main → extract → teardown → persist, with
  an on-error path). *Solving* (rollout) and *grading* (eval) become two
  **configurations** of that one engine, not two subsystems.
- **Why now.** **Harness** plurality is imminent — we'll run **Codex** and
  **Grok Build** soon, not just Claude Code, and a harness is *not* a trivial
  adapter (it decides the in-container invocation, what to mount, and whether
  output is captured via **stream** or **proxy**). **Dataset** and
  **evaluation-method** are near-future second axes. Meanwhile the current code
  leaks layers, duplicates machinery, and misnames things (see
  [Current state & motivation](#current-state--motivation)).
- **Users.** Every workstream builds on this core: **W1** annotation, **W2**
  solve+eval, **W3** auditing. SWE-Bench-Pro unit-test grading is just *one*
  `(dataset × eval-method)` instance.
- **Success looks like.** Adding a harness / dataset / eval-method is adding a
  **plug-in**, not a rewrite; `rollout` and `eval` share one engine; and the
  already-working flipt rollout + 731/731 gold sweep still pass.

## Assumptions

Correct any of these before we build on them:

1. **Harness is the primary axis** (Claude Code now; Codex, Grok Build next),
   dataset second (SWE-Bench Pro only today; a 2nd in the not-too-distant
   future), eval-method third (unit-test only today; model-/rubric-based later).
2. **A harness wraps a real off-the-shelf agent CLI** (mount its binary/config,
   write its invocation, capture stream-or-proxy) — unlike the sibling
   `locode-core`, which reimplements tools. The **patch is harness-agnostic**
   (`git diff` of the workdir).
3. **Execution model B only** — pull a prebuilt per-instance image and
   `docker run` a bind-mounted entryscript. The container-job "model A" stays
   deferred.
4. **This effort implements W2** (rollout + eval) on the new core and **designs**
   the core so W1/W3 fit later; it does **not** migrate W1 annotation now.
5. The concrete **interface, persistence, on-error mechanism, and code
   placement** are deferred to the next design round (Open Questions) — this spec
   fixes the *concept and contracts*, not the signatures.

## Current state & motivation

Grounded in the code (2026-07-18). Today `rollout/` and `evaluation/` are
separate, each with a private copy of the same machinery, and the "general" layer
leaks SWE-Bench-Pro specifics. The concrete smells this redesign fixes:

- **`EvalSpec` conflates two things.** It carries a *run context*
  (`image_ref, workdir, base_commit, instance_id` — all any container run needs)
  **and** a *unit-test grading spec* (`run_script, parser, before_repo_set_cmd,
  tests`). `rollout` reads only the 4 run-context fields but carries all 10;
  grading hard-codes one dataset's string format
  (`before_repo_set_cmd.splitlines()[-1]` is the golden-test checkout).
- **`evaluation` is a general word owning a specific implementation** — it is
  entirely unit-test-based, but model-/rubric-based judgment is coming.
- **Duplicated machinery** between `rollout` and `evaluation`: same
  workspace-staging pattern, duplicate `MOUNT_AT` / `ENTRYSCRIPT_NAME` /
  `PATCH_NAME` constants, divergent artifact names.
- **`proxy` is not legacy** — it is a capture *strategy* a future harness will
  need (some can't emit stream-json). Same for the agent error taxonomy.
- Naming: `Eval*` consumed by non-eval code, `Annotation*`-prefixed general
  errors, three meanings each of "run_script" and "patch", bare-string verdicts.

## The core model

### The lifecycle (one engine; every task is a configuration of it)

```
provision → setup? → main → extract? → teardown → persist
                                          │
  on-error ─────────────────────────────┘  (any step fails → collect
                                             intermediate metrics, then teardown)
```

| Phase | Responsibility | Notes |
|---|---|---|
| **provision** | bring up the sandbox/container | image + workdir + base_commit |
| **setup** *(optional)* | run a per-instance setup script after the sandbox is up | no-op if absent; **must exit 0** (else abort) |
| **main** | the core action | the pluggable step (see axes) |
| **extract** *(optional)* | derive a primary output (e.g. `git diff`) | skippable when no diff is needed |
| **teardown** | a callback: post-process + collect **artifacts** | always runs |
| **persist** | push artifacts off the ephemeral sandbox | mechanism deferred (HF / elsewhere) |
| **on-error** | a callback on any step's failure: collect intermediate metrics | a hard whole-sandbox crash is unrecoverable |

- **Artifacts are an open set** — trace, diff, eval-result, annotation snippets,
  metrics, … — not a fixed `{trace, diff}`.
- `rollout`, `eval`, and (later) `annotation` are the **same shape**: `eval`
  fits as `setup` = stage `run_script` + `parser` (+ golden-test checkout),
  `main` = run the entry script, `teardown` = `parser` parses → a result.

### The three axes (each plugs into specific phases)

| Axis | Plugs into | Owns | First instances |
|---|---|---|---|
| **Harness** (how the agent solves) | rollout **main** + its trace **teardown** | invocation script, mounts, **capture = stream \| proxy**, trace parsing | `claude_code` (now); `codex`, `grok_build` (next) |
| **Dataset** (what the instances are) | **provision** + optional **setup** + eval material | the instance's run context, its setup, and (for eval) its grading inputs | `swebench_pro` (now); a 2nd later |
| **Eval method** (how a solution is judged) | eval **main** + **teardown** | how a result is produced from a run | `unit_test` (now); `model_judge`, `rubric` (later) |

The engine is **harness-, dataset-, and eval-method-agnostic**; it only sequences
the phases and calls the plugged-in pieces.

### Decoupling that follows

- **Split `EvalSpec`** into a small shared **run context** (image / workdir /
  base_commit / id — what `provision` needs) and an **eval-method-specific spec**
  (the unit-test grading inputs). `rollout` takes only the run context (+ the
  task description for the prompt); the unit-test evaluator takes the run context
  + its own spec; a future model-judge takes the run context + a rubric.
- **Rename so the general words are free**: `evaluation` stays the general axis;
  the current implementation becomes a named method (e.g. `unit_test`). Fix the
  `Eval*` / `Annotation*` / `run_script` / `patch` overloads. (Final names →
  Open Questions.)
- **De-duplicate** the workspace-staging / constants / artifact-naming shared by
  rollout and eval into the one engine.
- **`proxy` and the agent error taxonomy are first-class** capture/observability
  pieces of the harness axis, not W1 leftovers.

## Tech Stack

Unchanged from the repo baseline: Python 3.13 + uv; Docker (`linux/amd64`,
prebuilt jefzda images); git for patch extraction; the existing `core/` infra
(`DockerProvider`, `patch`, `agent/{binary,trace,proxy}`, `datasets/loader`) is
refactored *into* this model rather than replaced wholesale.

## Commands

The CLIs evolve into thin configurations over the engine (exact surface → Open
Questions). Today's, for reference:

```sh
python -m swe_lab.rollout <instance_id> [--grade] [--model] [--timeout] [--no-pull]
python -m swe_lab.evaluation <instance_id> (--gold | --patch-file <p>) [--no-network]
python -m swe_lab.evaluation.verify --shard i/N [--aggregate]   # golden sweep
```

## Project Structure (PROPOSED — organization is an Open Question)

A sketch to react to, **not** decided (see Open Questions #4). The axes suggest
per-axis packages parallel to the existing `datasets/`:

```
core/
  sandbox/        the SandboxRun engine (phase sequencing, artifacts, persist, on-error)
  harnesses/      one per harness: claude_code/ (now), codex/, grok_build/ (next)
  datasets/       one per dataset: swebench_pro/ (now)   [exists]
  evaluation/     the eval axis; methods/ (unit_test/ now, model_judge/ later)
  <shared: docker, patch, paths, agent primitives folded into harnesses/sandbox>
```

Open placement questions: top-level vs under `core/`; how to split the currently
Claude-Code-specific `agent/*` into "general sandbox" vs "claude_code harness";
whether `rollout`/`evaluation` remain as thin CLI entrypoints.

## Code Style

Repo conventions unchanged: pyink (2-space, line 80), strict camelCase acronyms
(`SweBenchProInstance`, not `SWEbench…`), typed frozen dataclasses for records,
`Protocol`s for seams. Each axis plug is a small self-contained module; the engine
never imports a concrete harness/dataset/eval-method.

## Testing Strategy

- **Engine unit tests** — the phase sequencing, optional setup/extract,
  teardown/on-error callbacks, artifact collection — with a fake/mock sandbox
  (no Docker), so the lifecycle logic is tested with zero container spend.
- **Axis-contract tests** — each plug (harness/dataset/eval-method) tested
  against its contract in isolation.
- **Integration checkpoints that must not regress**: the flipt rollout still
  produces a graded patch; `python -m swe_lab.evaluation --gold` still resolves;
  the gold sweep stays 731/731.
- Existing quality bar (`uv run pytest` + `uv run pre-commit`) stays the gate.

## Boundaries

- **Always:** keep the engine harness-/dataset-/eval-method-agnostic; each plug
  self-contained; never break the working flipt rollout / gold sweep; log any
  dropped artifact (no silent loss).
- **Ask first:** the exact `SandboxRun` interface; the persistence mechanism;
  the on-error mechanism; the code organization/placement; adding execution
  model A; final naming.
- **Never:** leak a specific harness/dataset/eval-method into the engine;
  re-conflate run-context with grading spec; treat `proxy` as removable.

## Success Criteria

1. `rollout` and `eval` are two **configurations of one `SandboxRun` engine** —
   no duplicated staging / constants / artifact-naming.
2. **`EvalSpec` is split**: `rollout` consumes only a run context; the unit-test
   evaluator consumes run-context + its own grading spec.
3. The three axes exist as plug points; **claude_code × swebench_pro × unit_test**
   works end-to-end, and a **second harness** (or a stub) registers **without
   touching the engine**.
4. `proxy` is available as a harness capture strategy (even if unused by
   claude_code).
5. Regression-free: flipt rollout still yields a graded patch; gold sweep still
   731/731; `pytest` + `pre-commit` green.

## Out of scope

- Migrating **W1 annotation** onto the engine (designed-for, not done now).
- Execution **model A** (container-job).
- A **2nd dataset** or a **2nd eval-method** implementation (design the seams;
  don't build them yet).
- The deferred **mechanics** below.

## Open Questions

Deferred to the next design round — this spec fixes the concept, these fix the
mechanics:

1. **Exact `SandboxRun` interface** — phase signatures; how a harness / dataset /
   eval-method registers and supplies each phase; how `main` differs for
   rollout vs eval.
2. **Persistence** — how artifacts leave the ephemeral sandbox (Hugging Face,
   à la W1 traces? elsewhere?) and when.
3. **On-error mechanism** — how intermediate metrics are collected when a step
   fails mid-run.
4. **Code organization / placement** — top-level vs `core/`; splitting the
   Claude-Code-specific `agent/*` into general-sandbox vs `claude_code` harness;
   whether `rollout`/`evaluation` survive as thin CLIs; final package names.
5. **Naming** — the abstraction (`SandboxRun`? `Task`? `Run`?), the split names
   for `EvalSpec`, and the eval-method rename.
6. **Sampling / batching** — N-sample rollout and the matrix sweep sit *above* a
   single `SandboxRun`; confirm they're orchestration, not engine.
```
