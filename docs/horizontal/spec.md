# Spec: SandboxRun — unified sandboxed-task engine + pluggable axes

> **Status: Approved (2026-07-18).** The design was confirmed across the
> 2026-07-18 interview + interface sessions, and the five remaining open items
> (naming, code placement, persistence, on-error, sampling) were **resolved in
> the same day's follow-up interview** — see
> [Resolved questions](#resolved-questions-2026-07-18). Next step: the plan.
>
> **Date:** 2026-07-18 · **Scope:** the horizontal shared foundation, consumed
> by every workstream. Names below (`SandboxManager`, `Sandbox`, `SandboxSpec`,
> `Grader`, …) are **final**.

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

### The `Sandbox` is a pure handle; the outcome is a `RunResult`

The `Sandbox` is **only a handle** to the live container — nothing accumulates on
it, so its state is always clear. The **shared, inspectable state between
observers is the workspace filesystem** (`sb.workspace`), where artifacts already
live (`trajectory.jsonl`, `patch.diff`, `output.json`). The run's outcome is an
explicit **`RunResult`** the manager builds from observer *return values* + what
it catches (status / error / metrics). Nobody accumulates hidden state on a
shared object.

```python
@dataclass(frozen=True)
class Sandbox:                       # pure handle — nothing mutable accumulates on it
    label: str
    workspace: Path                  # host-visible dir shared into the container = the shared state (files)
                                     #   A-host → `docker create -v` mount · A-ghjob → local dir
    backend: SandboxBackend          # A-host | A-ghjob
    def exec(self, script, *, timeout, env=..., stream_to=None) -> ExecResult: ...  # run in the container

@dataclass
class RunResult:                     # the manager's aggregated return
    label: str
    status: RunStatus                # ok / setup_failed / errored / …
    artifacts: dict[str, Path]       # refs into the workspace: patch.diff, trajectory.jsonl, …
    metrics: dict[str, float] = {}
    error: Exception | None = None
```

### Declarative mounts — staging as data, not code

Everything a run needs staged into the workspace is declared as a **`Mounts`**
value instead of imperative per-flow staging code (today rollout and eval each
hand-write their own). Each **axis contributes its own mounts** — dataset: setup
files; harness: the pinned agent binary + entryscript; eval-method:
`run_script.sh` + `parser.py` — and the manager **merges and materializes** the
union into `sb.workspace` before the container comes up (duplicate target paths
are an error, not a silent overwrite).

```python
@dataclass(frozen=True)
class Mount:
    content: bytes | None = None     # small, runtime-generated (scripts)
    source: Path | None = None       # large, host-cached (the ~100MB agent binary)
    executable: bool = False         # chmod +x after materializing
                                     # exactly one of content/source is set

type Mounts = dict[str, Mount]       # key = workspace-relative target path
```

This kills the duplicated-staging smell directly and keeps the axes decoupled:
no axis needs to know what another axis mounts.

### The observer — the original five hooks only

No per-phase hooks: `after_create`→`before_main` (and `after_main`→
`before_destroy`) had nothing between them, so they were redundant. The five
lifecycle hooks are enough; most observers touch just `after_create` and/or
`before_destroy`. Hooks **return contributions** (artifact refs / metrics) that
the manager aggregates — they do **not** mutate a shared bag.

```python
class SandboxObserver:                # all no-op by default; override what you need
    def before_create(self, sb: Sandbox) -> None: ...
    def after_create(self, sb: Sandbox) -> None: ...       # sandbox up → SETUP runs here (exec + write files)
    def before_destroy(self, sb: Sandbox) -> Contribution | None: ...  # EXTRACT / PERSIST / eval-parse (always)
    def after_destroy(self, sb: Sandbox) -> None: ...
    def on_error(self, sb: Sandbox, error: Exception) -> Contribution | None: ...  # may exec into the live sb

class CompositeObserver(SandboxObserver): # fan out to a list, in registration order
    observers: list[SandboxObserver]
```

### The manager yields the sandbox; `main` is the body

The manager yields the (handle-only) `sandbox`; the body is `main`. Artifacts are
**files written into `sb.workspace`**; the manager accumulates a `RunResult` from
observer contributions and exposes it after the block.

```python
with manager.sandbox() as sb:          # before_create → backend.up → after_create (setup ran); sb = live handle
    run_agent(sb)                       # the ONE action: rollout writes trajectory.jsonl into sb.workspace
                                        #                 (or run_eval_script(sb) for eval)
result = manager.result                 # RunResult — before_destroy observers already ran
```

**Post-processing always runs** (the `finally` semantics) — we deliberately keep
it simple and have **no** main→teardown signal for now; add a conditional-skip
later only if a real case needs it. On error, `before_destroy` still runs, so its
observers may guard on the caught error where relevant. **Observer→observer data
flows through the workspace** (extract writes `patch.diff` and returns its ref;
persist pushes the workspace) — never through mutable state on the sandbox.

### Phase → hook mapping (nothing beyond the five hooks)

| Concern | Where | Provided by |
|---|---|---|
| setup script (per instance; `exit 0` or abort) | `after_create` observer | dataset |
| **main** — agent run (rollout) / eval script (eval) | the **body** | harness / eval-method |
| diff-extract | `before_destroy` observer | shared |
| trace capture | around the body / `before_destroy` | harness |
| eval parse → verdict (`grader.grade(workspace)`) | `before_destroy` observer | dataset, via the eval-method seam |
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

- **Split `EvalSpec`** into a small shared **`SandboxSpec`** (the run context:
  `image_ref` / `workdir` / `base_commit` / `instance_id` — what it takes to
  bring an instance's sandbox up) and an **eval-method-specific spec**. rollout
  takes only the `SandboxSpec`; the unit-test evaluator takes it + its own spec;
  a future model-judge takes it + a rubric.
- **The unit-test method's general contract** is a triple — everything
  SWE-Bench-Pro-shaped (`run_script`, `parser`, `before_repo_set_cmd`, test
  lists) stays inside the SBP adapter, which *compiles* its record into it:

  ```python
  class Verdict(Protocol):            # the minimal cross-dataset surface —
      @property                       #   sweeps/aggregation depend on nothing else
      def resolved(self) -> bool: ...

  class Grader[V: Verdict](Protocol): # dataset-owned judgment, generic in its verdict
      def grade(self, workspace: Path) -> V: ...   # pure: reads files, no container

  @dataclass(frozen=True)
  class UnitTestSpec[V: Verdict]:
      eval_script: str                # what main runs in the container
      mounts: Mounts                  # files it needs staged (SBP: run_script.sh + parser.py)
      grader: Grader[V]               # workspace → verdict, in before_destroy
  ```

  Concrete verdict types are **dataset-owned** (e.g. `SweBenchProVerdict`:
  `resolved` + `passed`/`missing` sets + an `output_state: ok | absent |
  unparseable` — the last distinguishes "parser output corrupt" from "no tests
  passed", eliminating the false-GOLDEN_FAIL class found in the 2026-07-18 code
  audit). The `Verdict` bound stays minimal on purpose: widen it later if a real
  aggregation needs more (adding a member is mild evolution; guessing now is
  over-design). `grade()` is pure over the workspace → unit-testable without
  Docker.
- **Free the general words**: `evaluation` stays the general axis; the current
  implementation becomes the named method `unit_test`. Fix the `Eval*` /
  `Annotation*` / `run_script` / `patch` overloads during migration (mechanical;
  plan-stage).
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

**One entry point with subcommands** (decided 2026-07-18): the standalone
`rollout`/`evaluation` `__main__` packages dissolve into

```sh
python -m swe_lab rollout <instance_id> [--grade] [--model] [--timeout] [--no-pull]
python -m swe_lab eval    <instance_id> (--gold | --patch-file <p>) [--no-network]
python -m swe_lab verify  --shard i/N [--aggregate]   # golden sweep
```

**Growth guard:** the dispatcher (`swe_lab/__main__.py`) is a table only; every
subcommand lives in its own module (`swe_lab/cli/rollout.py`, `cli/eval.py`, …).
Future subcommands (`annotate`, `audit`, `promote`) are new modules, never
additions to a growing single file.

## Project Structure (DECIDED 2026-07-18)

**Axis packages sit flat at the top of `src/swe_lab/`, and the `core/` package
dissolves entirely** — this refactor migrates all of it (整锅端), no dual-track
period. Rationale: keeping everything under `core/` would reduce the repo to a
`core` + `workstreams` two-bucket shape, and the extra nesting buys nothing.

```
src/swe_lab/
  __main__.py     dispatcher table only → cli/<subcommand>.py modules
  cli/            one module per subcommand (rollout, eval, verify, promote, …)
  sandbox/        SandboxManager, Sandbox, SandboxSpec, Mounts, SandboxObserver,
                  CompositeObserver, backends (A-host, A-ghjob), shared observers
                  (setup, diff-extract, persist, metrics, logging)
  harnesses/      one per harness: claude_code/ (now), codex/, grok_build/ (next)
  datasets/       one per dataset: swebench_pro/ (now)   [moves up from core/]
  evaluation/     the eval axis; methods/ (unit_test/ now, model_judge/ later)
  paths.py        [moves up from core/]
```

Migration mapping for the rest of today's `core/`: `docker/` + the general parts
of `agent/` (binary provisioning pattern, stream/proxy capture, error taxonomy)
→ `sandbox/` and `harnesses/claude_code/` respectively; `patch.py` → the shared
diff-extract observer's home in `sandbox/`; `benchmark.py` dissolves with the
`EvalSpec` split; `repo/` stays as-is wherever W1 needs it (untouched until W1
migrates). The docs side mirrors this: `docs/core/` is renamed
**`docs/horizontal/`** (this folder) — pairs with `workstreams/` = the verticals.

## Persistence — artifact tiers & store (DECIDED 2026-07-18)

Artifacts fall into **three tiers with different lifecycles**; the old
single-HF-repo approach conflated them (it was designed for one task, W1).

| Tier | What | Lifecycle | Home |
|---|---|---|---|
| **T0 debug residue** | ad-hoc runs, format-unstable intermediates | disposable, auto-expiring | **no infra built**: local runs → the workspace dir under `.cache/`; CI runs → GitHub Actions artifacts (built-in TTL, ≤90 days, free on the public repo) |
| **T1 formal intermediates** | trajectories, patches, per-run results, diagnostics — **including failed runs** | **keep everything** (failures are research material for W3 / behavioral analysis); private | **S3-compatible object store** (below), keyed `runs/<sweep-id>/<instance>/<run-ts>/…` |
| **T2 formal publishes** | the final parquet, curated traces | versioned, public | **Hugging Face** (what it is actually for) |

Mechanics:

- **`Store` seam.** The `PersistObserver` talks to a tiny `Store` interface
  (put + manifest append). The vendor is **configuration, not architecture** —
  swapping stores later is a config change. **The manifest indexes T1 only**; it
  is the ledger of formal intermediates and is never polluted by debug residue.
- **Tier = explicit entry-point default + one flag, no inference.** Formal sweep
  workflows default `formal`; `workflow_dispatch` one-offs and local CLI default
  `debug` (opt in via `--persist`). The tier is stamped into the run record at
  launch. Exact per-entry defaults are tuned at plan/usage time.
- **Misclassification safety valve = `promote`**: one command pushes a debug
  run's workspace into T1 + appends the manifest entry. T0's TTL gives a
  recovery window, so classification never needs to be perfect — that dissolves
  the "how do we split T0/T1" problem.
- This design **subsumes the deferred `outputs/` restructure** (AGENTS.md
  boundary): committed intermediates in `outputs/` belong to T1 and migrate
  there.

**Store selection** (prices/quotas verified against official pages 2026-07-18;
scale assumption: 5–20 GB per full sweep → 100–300 GB over a year or two;
CI-token writes, occasional laptop reads; budget ≤$10/mo):

| Option | Free | @300 GB | Egress | S3 API | Dealbreaker? |
|---|---|---|---|---|---|
| **Cloudflare R2** ← **chosen** | 10 GB | ~$4.4/mo | $0 always | ✅ | — |
| Backblaze B2 (runner-up) | 10 GB | ~$2.1/mo | free ≤3× stored | ✅ | — (cheapest; egress cap is fine) |
| Scaleway (zero-cost alt) | **75 GB** | ~€3.3/mo | 75 GB/mo free | ✅ | EU account; covers ~year one entirely free |
| HF private (free / PRO $9) | 100 GB / 1 TB | $9 flat | free | ❌ hf CLI | repo-hygiene limits (~100k files/repo, 50–100 files/commit) fight the many-small-files, append-per-run pattern — validates "HF is for formal publishes" |
| AWS S3 | 5 GB | ~$6.9/mo | $0.09/GB past 100 GB/mo | ✅ | priciest, no upside here |
| Wasabi | none | $7.99/mo **floor** (1 TB min + 90-day min duration) | fair-use | ✅ | minimums kill it for churning artifacts |
| GH Actions artifacts | free (public) | — | free | ❌ | **≤90-day retention** → T0 only (a feature there) |
| GH Releases | free | — | free | ❌ | 2 GiB/file, immutable blob semantics |

**Decision: R2 for T1** — unconditional zero egress, first-class S3 tooling
(rclone / boto3 / aws-cli), free tier covers the first sweeps, ~$4/mo at
steady-state. B2 and Scaleway are recorded as drop-in fallbacks behind the same
seam (Scaleway if a zero-cost year matters more than mainstream tooling).

## Deferred: on-error diagnostics (P1 — not in v1)

The `on_error` hook stays in the engine interface, but **no diagnostics observer
ships in v1**. Real debugging during bring-up will reveal what observability is
actually needed; add it then. Candidate ideas recorded for that moment:

- exec into the still-live container: `git status --porcelain` /
  `git diff --stat` of the workdir (how far did the agent get?), agent-log tail,
  disk usage;
- diagnostics are **files in `workspace/diagnostics/`** — same rank as any
  artifact, persisted by the normal `before_destroy` → T1 path (failed runs are
  kept anyway);
- the diagnostics observer must **never raise** (a broken probe must not mask
  the original error);
- structured run metrics (duration, tokens) are **not** on-error's job — that is
  the normal-path `MetricsObserver`.

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
  log any dropped artifact (no silent loss); **keep batching outside the engine**
  — N-sample rollout and matrix sweeps are orchestration (CI matrix + thin
  drivers); the engine's worldview is one sandbox, one run, one `RunResult`.
- **Ask first:** widening the `Verdict` bound; swapping the T1 store vendor;
  adding a conditional-teardown signal back; bringing on-error diagnostics into
  scope.
- **Never:** leak a specific harness/dataset/eval-method into the engine;
  re-conflate run-context with grading spec; treat `proxy` as removable; let the
  manifest index anything but T1.

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
  don't build them yet — the one shipped composition is
  `claude_code × swebench_pro × unit_test`; a second harness may get a **stub**
  purely to prove the seam, per Success Criteria #3).
- A conditional-teardown **signal** (dropped for simplicity; revisit if needed).
- The **on-error diagnostics observer** (P1 — see its section; the hook itself
  ships, the observer doesn't).
- Tuning the exact **per-entry-point tier defaults** (plan/usage-stage).

## Resolved questions (2026-07-18)

All five remaining open questions were settled in the 2026-07-18 follow-up
interview; each resolution lives in its section above. The map:

1. **Naming** → engine trio kept as-is; run-context = `SandboxSpec`; unit-test
   contract = `UnitTestSpec[V]` + `Grader[V: Verdict]` (minimal `Verdict` bound:
   `resolved` only; concrete verdicts dataset-owned); axis stays `evaluation`,
   method is `unit_test`. New: engine-level `Mounts`. See *Declarative mounts*
   and *Decoupling that follows*.
2. **Code organization** → top-level flat axis packages; `core/` dissolves
   entirely; `docs/core/` → `docs/horizontal/`; one CLI entry with
   per-subcommand modules. See *Project Structure* and *Commands*.
3. **Persistence** → three tiers (T0 no-infra / T1 keep-all object store /
   T2 HF public); `Store` seam; R2 chosen (B2 / Scaleway fallbacks); tier =
   entry default + flag + `promote`; manifest indexes T1 only. See
   *Persistence*.
4. **On-error** → deferred wholesale to P1; the hook ships, no observer in v1.
   See *Deferred: on-error diagnostics*.
5. **Sampling / batching** → confirmed orchestration, not engine (one sandbox,
   one run, one `RunResult`). See *Boundaries → Always*.
```
