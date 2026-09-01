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
  work over the eval data. All three are dormant today (see the snapshot below),
  so each is just a `README`; an active one would take the component layout
  described in [`doc-map.md`](doc-map.md).

## Status snapshot

**The centre of gravity is the [horizontal foundation](horizontal/)** — this
repo's job is maintaining the harness itself. The three workstreams are the
project's original framing and are all dormant: they record what was delivered,
not what is being worked on. Live, task-level status has exactly one home, the
[horizontal task index](horizontal/plans/README.md); update the table below only
when a workstream's state changes.

| # | Workstream | Status | Detail |
| --- | --- | --- | --- |
| **W1** | Related-files annotation | ✅ **Complete** — 731/731 annotated, QA'd, pushed; the folder is now an archive | [w1](workstreams/w1-related-files/) |
| **W2** | Solve + evaluate pipeline | 📦 **Folded into the horizontal foundation** — the solve + eval loop it planned is shipped; its two surviving to-dos moved to the horizontal index as tasks 31–32 | [w2](workstreams/w2-solve-eval/) |
| **W3** | Quality auditing / skew | 📋 **Never started** — an intent, not a plan. The nearest shipped work is benchmark integrity, which landed horizontally (`src/swe_lab/integrity/`, tasks 25–26), not under `pipelines/` as that README guesses | [w3](workstreams/w3-quality-audit/) |

**Latest (2026-08-31).** The horizontal foundation is the live surface: one
SandboxRun engine driven by `swe-lab run <workflow>`, three harnesses
(`claude_code`, `codex`, `grok_build` — tasks 06/28/29) and two datasets
(SWE-Bench Pro, plus DeepSWE 1.1 at task 30, landed 2026-08-26 with a live
rollout e2e run and a green 113-task gold sweep on Actions: 0 GOLDEN_FAIL / 0
BASE_UNEXPECTED_PASS). Rollout is proven end-to-end on all three harnesses, and
a downstream consumer has run it. One P0 is open: task 15, proving the sandbox
extensibility seam from the outside plus its author guide — ADR-0003 phase 2 is
waiting on it. Tasks 13 (R2 store) and 24 (the portable-bundle wiring) are
**deferred by choice**, and tasks 31–33 are newly queued → [horizontal task index](horizontal/plans/README.md). The CP1–CP5 human
checkpoints are **retired**: review happens at PR granularity instead. Patch
extraction is settled in
[ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md) (Accepted).

## Where everything lives

| Path | What's in it |
| --- | --- |
| [horizontal/](horizontal/) | The **horizontal** shared foundation — design of the shared execution core and any cross-cutting shared-code work. |
| [trace-synthesis/](trace-synthesis/) | **Oracle-guided trace synthesis** — an active component building SFT training traces on top of the foundation. Its [task index](trace-synthesis/plans/README.md) is its own live status home. |
| [workstreams/](workstreams/) | The **verticals** — one folder per workstream. All three are dormant: each is a design/history `README`. |
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
from a `spec.md`; the active component — today, the horizontal foundation —
owns its `plans/` per-task designs indexed by `plans/README.md`, which is also
its only status home); **experimenting** follows the
[experiment playbook](experiments/playbook.md).
Each fact has one canonical home — link to it, don't restate a fact that drifts.
