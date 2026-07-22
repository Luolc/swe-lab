# Plan — SandboxRun implementation

The **strategy** for implementing the approved [SandboxRun spec](spec.md):
phase order, dependency graph, risks, standing quality bar, and human
checkpoints. The ordered task list + status lives in
[`plans/README.md`](plans/README.md); this file deliberately does **not**
enumerate tasks.

## Strategy — strangler, eval-first, moves last

Build the new `sandbox/` engine **alongside** the existing code and migrate one
vertical slice at a time; the old `rollout/`/`evaluation/` packages stay
working until their engine replacements have proven parity. Order of slices:

1. **Engine first, Docker-free.** The manager/observer lifecycle is pure
   orchestration — build it against a fake backend and unit-test the failure
   matrix with zero container spend.
2. **Eval slice before rollout slice.** Eval is agent-free (~2.5 min/instance,
   no tokens), so it is the cheapest end-to-end proof of the engine + A-host
   backend + dataset/eval-method axes. It also carries the highest-value
   correctness fix (the `output_state` verdict, killing the false-GOLDEN_FAIL
   class from the 2026-07-18 audit).
3. **Rollout slice next** (claude_code harness + diff-extract), reusing the
   already-proven engine + eval method for grading.
4. **Mechanical moves last** (`datasets/`→top level, old packages deleted,
   `core/` dissolved, CI workflows cut over) — only after both slices pass
   their checkpoints, so a failed experiment never strands a half-moved tree.
5. **Persistence after eval slice** — the `Store` seam and tiers are
   independent of the harness work; the R2 vendor wiring waits on a
   user-provisioned bucket (ask-first checkpoint).

## Dependency graph

```
02 engine core (fake backend, no Docker)
    │
    ├── 03 A-host backend (docker create/start/exec/rm)
    │       │
    │       ├── 04 unit_test method + SBP compile ──┐
    │       │       │                               │
    │       │       └── 05 eval CLI + parity ── CP1 │
    │       │                                       │
    │       ├── 06 claude_code harness (stream)     │
    │       │       │                               │
    │       │       ├── 07 diff-extract + rollout CLI (grades via 04) ── CP2
    │       │       └── 08 proxy capture mode
    │       │
    │       └── 09 A-ghjob backend
    │
    ├── 12 Store seam + tiers (persist observer)
    │       └── 13 R2 store  [ask-first: bucket]
    │
    └── (after CP1+CP2) 10a moves → 10b cutover+deletion → 11 docs sync ── CP3
```

## Phases

| Phase | Tasks | Proves |
|---|---|---|
| A — Engine | 02 | lifecycle, hooks, Mounts, RunResult; failure matrix under a fake backend |
| B — Backend | 03 (09 later) | a real container behind the `Sandbox` handle |
| C — Eval slice | 04, 05 → **CP1** | dataset × eval-method axes compose; parity with the old grader; `output_state` fix live |
| D — Rollout slice | 06, 07 → **CP2**, 08 | harness axis composes; flipt rollout regression bar holds; proxy is a capture strategy |
| E — Moves & cutover | 09, 10a, 10b, 11 → **CP3** | one engine, no duplicated machinery, `core/` gone, CI on the new CLI, 731/731 still green, harness stub registers w/o engine changes |
| F — Persistence | 12, 13 → **CP4** | T0/T1 mechanics + R2 behind the `Store` seam |

## Definition of Done (every task clears this standing bar)

- `uv run pre-commit run --all-files` clean · `uv run pytest` green — including
  the full Google-style gate set (D/D401/D417, N, C90, W505, pydoclint) now
  live from [task 01](plans/task-01-google-style-readability.md); migrated code
  lands Google-docstring'd (the task-01 P4 guardrail).
- New behavior has a test; changed behavior updates its test.
- **No silent caps or truncation** — anything dropped is logged.
- Spec boundaries hold: the engine never imports a concrete
  harness/dataset/eval-method; run-context never re-conflates with grading
  spec; batching stays outside the engine.

## Checkpoints (human review gates)

- **CP1 — eval parity.** Gold self-test resolves on flipt + ansible through the
  engine path; old-vs-new verdict parity on 2–3 instances (including one from
  the truncated-golden-names class). *User reviews before the rollout slice.*
- **CP2 — rollout regression bar.** Flipt rollout end-to-end on the engine in
  CI: conversation + clean `patch.diff` + graded outcome. *User reviews before
  the moves.*
- **CP3 — cutover.** Full golden sweep 731/731 via `workflow_dispatch`
  (`max-parallel` ≤15, ~2.2 h) + a flipt rollout re-run + the harness-stub
  seam test. *User triggers the sweep and reviews results.*
- **CP4 — R2 provisioning.** User creates the R2 bucket + API token (ask-first
  boundary) before task 13 wires it into CI.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Grading parity regression during the port | High | CP1 parity check incl. a golden-fail-class instance; old grader kept until 10b |
| Persistent-container lifecycle leaks (create/exec without `--rm`) | Med | backend `finally`-rm; engine tests assert destroy runs on every failure path |
| Import-move breakage in W1 (`pipelines/`) | Med | moves are one mechanical task (10a) gated by the full suite; W1 has its own tests |
| CI workflows drift from the new CLI | Med | each workflow updated in the same task as its CLI; CP3 exercises all three |
| Full-sweep cost/concurrency | Low | manual dispatch only; `max-parallel` 15 keeps merge-CI headroom |
| Scope creep into deferred items (on-error observer, W1 migration, token-via-proxy ADR) | Med | listed out-of-scope below; each needs its own decision to enter |

## Out of scope (unchanged from the spec, restated for the build)

- W1 annotation migration; a real 2nd dataset / eval-method / harness
  implementation (a **stub** proves the harness seam at CP3).
- The on-error diagnostics observer (P1).
- Routing rollout's OAuth token through a host-side proxy (audit P0-1) — a
  security-design change that needs its own ADR; the proxy *capture* work in
  task 08 does not change rollout auth.
- Publishing the fixed parquet / retiring `patches.py` (W2 task 4, orthogonal).
