# Horizontal — the shared foundation

The **horizontal** layer every [workstream](../workstreams/) builds on: shared,
**dataset-agnostic** infrastructure. Code lives in
[`src/swe_lab/core/`](../../src/swe_lab/core/) today; per the approved
[SandboxRun spec](spec.md), the `core/` package dissolves into top-level
axis packages (`sandbox/`, `harnesses/`, `datasets/`, `evaluation/`) during the
redesign. This doc is the design overview.
Operational details (commands, hazards) are in
[`../conventions.md`](../conventions.md).

## What's in it

| Package | Responsibility |
| --- | --- |
| [`core/datasets/`](../../src/swe_lab/core/datasets/) | Dataset-agnostic `load_dataset` + a name→record registry, plus per-dataset **adapter packages** (`swebench_pro/`: the typed record, the `EvalSpec` builder, the grader). Adding a dataset = adding a sibling adapter, never touching the general code. |
| [`core/repo/`](../../src/swe_lab/core/repo/) | `RepoProvider` protocol + `GitCheckoutProvider` (bare mirror + per-instance git worktree at `base_commit`), for the read-only annotation flow. |
| [`core/agent/`](../../src/swe_lab/core/agent/) | The headless Claude Code runner shared by annotation and rollout — pinned-binary provisioning, the stream-json trace record, the optional reverse-proxy capture. |
| [`core/docker/`](../../src/swe_lab/core/docker/) | General `DockerProvider` — pull an image, run a script in a bind-mounted container (`linux/amd64`). |
| [`core/patch.py`](../../src/swe_lab/core/patch.py) | Patch extract/clean helpers shared by rollout + evaluation. Contract: [ADR-0001](../decisions/ADR-0001-patch-extraction-and-grading.md). |
| [`core/benchmark.py`](../../src/swe_lab/core/benchmark.py) | The shared contract: `EvalSpec` + `BenchmarkAdapter` protocol. |
| [`core/paths.py`](../../src/swe_lab/core/paths.py) | Repo-root / datasets / cache path helpers. |

## Design principle

The general/dataset-specific split mirrors `datasets/`: **general code never
learns a dataset's specifics**; each dataset provides an adapter. The
general/per-dataset boundary in `benchmark.py` (`EvalSpec` still carries
SWE-Bench-Pro-shaped fields) is provisional until a second dataset forces it to
firm up.

## Cross-cutting work lands here

Shared-code changes that don't belong to a single vertical — e.g. extracting
common code out of a workstream into `core/`, or hardening a shared provider —
are **horizontal** work and are planned here (a `spec.md` / `plan.md` / `plans/`
alongside this README).

**Active now — the SandboxRun redesign:** re-architect the execution core into one
unified sandboxed-task engine + three plug-in axes (harness / dataset /
eval-method), so `rollout` and `eval` become configs of one engine. See
**[spec.md](spec.md)** (approved 2026-07-18), the **[plan](plan.md)** +
per-task designs in **[plans/](plans/)**, and
**[workspace-layout.md](workspace-layout.md)** (the concrete per-run file
inventory every composition is built against).
