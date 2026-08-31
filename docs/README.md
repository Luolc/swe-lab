# swe-lab — map

Orientation for the whole repo: what it is, where the work stands, and where
everything lives. This is the **"read this first"** index (agent rules are in
[`../AGENTS.md`](../AGENTS.md)).

`swe-lab` is tooling to **build, run, enrich, audit, and fix SWE (coding-agent)
evaluation data**. Its shape is **one horizontal shared foundation + several
independent verticals**:

- **Horizontal** — [`horizontal/`](horizontal/) → the shared **SandboxRun engine**
  + its three plug-in axes every vertical builds on (harness, dataset,
  eval-method). Code: [`src/swe_lab/sandbox/`](../src/swe_lab/sandbox/),
  [`harnesses/`](../src/swe_lab/harnesses/),
  [`datasets/`](../src/swe_lab/datasets/),
  [`evaluation/`](../src/swe_lab/evaluation/).
- **Verticals** — [`workstreams/`](workstreams/) → each an independent unit of
  work over the eval data. A workstream is a folder; the active one carries its
  own `spec` / `plan` / `todo`, a dormant one is just a `README`.

## Status snapshot

Update this when a workstream's state changes; keep the detail in each
workstream's folder, not here.

| # | Workstream | Status | Detail |
| --- | --- | --- | --- |
| **W1** | Related-files annotation | ✅ **Complete** — 731/731 annotated, QA'd, pushed | [w1](workstreams/w1-related-files/) |
| **W2** | Solve + evaluate pipeline | 🚧 **Active** — eval + rollout validated end-to-end (multi-harness, multi-dataset); matrix eval over the full SWE-Bench-Pro set is the focus | [w2](workstreams/w2-solve-eval/) |
| **W3** | Quality auditing / skew | 📋 **Planned** — first tool (gold self-test sweep) falls out of W2 | [w3](workstreams/w3-quality-audit/) |

**Latest (2026-08-31).** W1 annotation is done (7083 snippets; traces off-repo on
HF). Rollout is proven end-to-end: the `claude_code`, `codex`, and `grok_build`
harnesses are all done (horizontal tasks 06/28/29), and horizontal task 30
(DeepSWE 1.1 as a second dataset) landed 2026-08-26 with a live rollout e2e run
and a green 113-task gold sweep on Actions (0 GOLDEN_FAIL / 0
BASE_UNEXPECTED_PASS). W2's own mainline — matrix eval across the full
SWE-Bench-Pro 731 set ([w2 todo](workstreams/w2-solve-eval/todo.md) tasks 2–4) —
is still open. On the horizontal side, tasks 25 (git-history purge, P0) and 15
(extensibility seam proof, P0) remain open, task 13 (R2 store) is blocked on
CP4 (ask-first), and human checkpoints CP1–CP5 are all still unchecked →
[horizontal task index](horizontal/plans/README.md). Patch extraction is
settled in [ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md)
(Accepted).

## Where everything lives

| Path | What's in it |
| --- | --- |
| [horizontal/](horizontal/) | The **horizontal** shared foundation — design of the shared execution core and any cross-cutting shared-code work. |
| [workstreams/](workstreams/) | The **verticals** — one folder per workstream (design/history, plus `spec`/`plan`/`todo` when active). |
| [conventions.md](conventions.md) | Codebase map, build/test/lint commands, directory meanings, hazards, source-of-truth rule. |
| [doc-map.md](doc-map.md) | Which doc answers which question, where a new learning belongs, and the single-source-of-truth guards. |
| [decisions/](decisions/) | Architectural decisions (ADRs). ADR-0001 = patch extraction + grading (Accepted). |
| [releases/](releases/) | **What each published version means for a consumer** — breaking changes and the migration for them, one file per version. |
| [reviews/](reviews/) | Point-in-time engineering audits of the codebase (dated snapshots, not specs). |
| [experiments/playbook.md](experiments/playbook.md) | How we run experiments + investigations in this ML/eval repo. |
| [patch-extraction.md](patch-extraction.md) | Corner-case survey (background research, non-authoritative — decisions are in ADR-0001). |
| [traces.md](traces.md) | Off-repo trace storage (HF dataset repo + manifest). |
| [../AGENTS.md](../AGENTS.md) | Agent working rules: build vs experiment mode, git workflow, quality bar, boundaries. |

## How we work

See [`../AGENTS.md`](../AGENTS.md). In brief: **building** a feature runs the
**spec → plan → build → review → ship** lifecycle (a non-trivial effort starts
from a `spec.md`; the active component — a workstream or the horizontal
foundation — owns its `plan.md` strategy + `plans/` per-task designs indexed by
`plans/README.md`); **experimenting** follows the
[experiment playbook](experiments/playbook.md).
Each fact has one canonical home — link to it, don't restate a fact that drifts.
