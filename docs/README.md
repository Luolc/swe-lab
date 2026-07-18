# swe-lab — map

Orientation for the whole repo: what it is, where the work stands, and where
everything lives. This is the **"read this first"** index (agent rules are in
[`../AGENTS.md`](../AGENTS.md)).

`swe-lab` is tooling to **build, run, enrich, audit, and fix SWE (coding-agent)
evaluation data**. Its shape is **one horizontal shared foundation + several
independent verticals**:

- **Horizontal** — [`core/`](core/) → the shared, dataset-agnostic infrastructure
  every vertical builds on (dataset loading, repo checkout, a headless-agent
  harness, a Docker execution layer, the benchmark contracts). Code:
  [`src/swe_lab/core/`](../src/swe_lab/core/).
- **Verticals** — [`workstreams/`](workstreams/) → each an independent unit of
  work over the eval data. A workstream is a folder; the active one carries its
  own `spec` / `plan` / `todo`, a dormant one is just a `README`.

## Status snapshot

Update this when a workstream's state changes; keep the detail in each
workstream's folder, not here.

| # | Workstream | Status | Detail |
| --- | --- | --- | --- |
| **W1** | Related-files annotation | ✅ **Complete** — 731/731 annotated, QA'd, pushed | [w1](workstreams/w1-related-files/) |
| **W2** | Solve + evaluate pipeline | 🚧 **Active** — eval built + validated (gold sweep 731/731); **`rollout` is the focus** | [w2](workstreams/w2-solve-eval/) |
| **W3** | Quality auditing / skew | 📋 **Planned** — first tool (gold self-test sweep) falls out of W2 | [w3](workstreams/w3-quality-audit/) |

**Latest (2026-07-17).** W1 annotation is done (7083 snippets; traces off-repo on
HF). W2's evaluation subsystem is validated and the full gold self-test sweep
passed (731/731; 3 dataset-side false negatives fixed in-loader via a `patches.py`
stopgap pending a published fixed parquet); **`rollout` (agent sampling) is the
current focus** → [w2](workstreams/w2-solve-eval/). Patch extraction is settled in
[ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md) (Accepted).

## Where everything lives

| Path | What's in it |
| --- | --- |
| [core/](core/) | The **horizontal** shared foundation — design of `src/swe_lab/core/` and any cross-cutting shared-code work. |
| [workstreams/](workstreams/) | The **verticals** — one folder per workstream (design/history, plus `spec`/`plan`/`todo` when active). |
| [conventions.md](conventions.md) | Codebase map, build/test/lint commands, directory meanings, hazards, source-of-truth rule. |
| [decisions/](decisions/) | Architectural decisions (ADRs). ADR-0001 = patch extraction + grading (Accepted). |
| [reviews/](reviews/) | Point-in-time engineering audits of the codebase (dated snapshots, not specs). |
| [experiments/playbook.md](experiments/playbook.md) | How we run experiments + investigations in this ML/eval repo. |
| [patch-extraction.md](patch-extraction.md) | Corner-case survey (background research, non-authoritative — decisions are in ADR-0001). |
| [traces.md](traces.md) | Off-repo trace storage (HF dataset repo + manifest). |
| [../AGENTS.md](../AGENTS.md) | Agent working rules: build vs experiment mode, git workflow, quality bar, boundaries. |

## How we work

See [`../AGENTS.md`](../AGENTS.md). In brief: **building** a feature runs
`/spec → /plan → /build → /review → /ship` (a non-trivial effort starts from a
`spec.md`; the active component — a workstream or the horizontal `core` — owns its
`plan.md` strategy + `plans/` per-task designs indexed by `plans/README.md`);
**experimenting** follows the [experiment playbook](experiments/playbook.md).
Each fact has one canonical home — link to it, don't restate a fact that drifts.
