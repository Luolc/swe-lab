# Project Plan — swe-lab

**Roadmap + status index.** This file is deliberately thin: the vision, the
current status snapshot, and pointers into the detailed docs. Per-workstream
detail, decisions, conventions, and experiments each live in their own file
under [`docs/`](docs/) (see the [docs map](#docs-map)) so no single document has
to hold everything.

## Scope

`swe-lab` is an umbrella for tooling that **builds, runs, enriches, audits, and
fixes SWE-Bench (coding-agent) evaluation data**, organized as independent
**workstreams** over shared infrastructure in
[`src/swe_lab/core/`](src/swe_lab/core/) (dataset loading, per-instance repo
checkout, a headless-agent harness, a Docker execution layer, and the
dataset-agnostic benchmark contracts).

## Status snapshot

**Read this first.** Where the work stands so a fresh session can pick up without
guesswork. Update it whenever a milestone's state changes; keep the *detail* in
the linked workstream docs, not here.

| # | Workstream | Status | Detail |
| --- | --- | --- | --- |
| **W1** | Related-files annotation | ✅ **Complete — 731/731** annotated, QA'd, pushed (`6fe7095`) | [w1](docs/workstreams/w1-related-files.md) |
| **W2** | Solve + evaluate pipeline | 🚧 **Active** — eval built + validated; **full gold sweep done (731/731)**; **`rollout` is the current focus** | [w2](docs/workstreams/w2-solve-eval.md) |
| **W3** | Quality auditing / skew | 📋 **Planned** — first tool (gold self-test sweep) already falls out of W2 | [w3](docs/workstreams/w3-quality-audit.md) |

**Latest (2026-07-17).**

- **W1 — ✅ COMPLETE, full dataset.** All **731/731** instances annotated, QA'd,
  and pushed: **7083 snippets** over **37 rounds**. Default capture is now
  **stream** (`claude --output-format stream-json`, no reverse proxy); large trace
  records live off-repo in a private HF dataset repo (`luolc/swe-lab-traces`,
  2924 files / 412.6 MB) via `traces.py` + a git-tracked manifest. **Nothing left
  to annotate.** → [w1](docs/workstreams/w1-related-files.md)
- **W2 — active.** The **evaluation** subsystem is built and validated, and the
  **full gold self-test sweep is done** (731/731 gold patches resolve; 3
  dataset-side false negatives fixed in-loader via `patches.py` — a stopgap
  pending a published fixed parquet). Gold self-tests resolve on **GitHub
  Actions** (native amd64, ~1–2.5 min/instance, free, no secrets). **`rollout`
  (agent sampling) is the current focus** — a subscription
  `CLAUDE_CODE_OAUTH_TOKEN` is available (gitignored `.envrc.local`; rotate after
  use). → [w2](docs/workstreams/w2-solve-eval.md)
- **Patch-extraction decisions (D1–D8) are ⚠️ provisional / not source of
  truth.** They churned a lot; **the code is authoritative** (`core/patch.py`,
  `rollout/`, `grading.py`). Pending a joint re-review before they're trusted or
  split into ADRs. → [decisions](docs/decisions/patch-extraction-decisions.md)
- The repo was renamed `swe-lab` and reorganized into `core/` + `tasks/` (+
  `rollout/` / `evaluation/`); git history was scrubbed of a leaked OAuth token
  and operator PII (force-pushed).

## How we work

This repo follows a light-but-real lifecycle (adapted from the `agent-skills`
pack — brownfield path). Two modes:

- **Building** (a feature / change): `/spec → /plan → /build → /review → /ship`,
  with TDD and small atomic commits as the default. Slash commands are in
  [`.claude/commands/`](.claude/commands/); the skills they invoke are installed
  under [`.claude/skills/`](.claude/skills/).
- **Experimenting** (learning something — prompts, variance, failures, "is X
  worth building?"): follow the
  **[experiment playbook](docs/experiments/playbook.md)** — hypothesis → logged,
  timestamped run → empirical results → attributable conclusion → `REPORT.md`.
  This is the ML side the coding-lifecycle skills don't cover. An experiment's
  report *feeds* a `/spec` or a decision; experiment → decide → then build.

Agent behavior rules (voice input, language, naming) are in
[`AGENTS.md`](AGENTS.md); the codebase map, commands, and hazards are in
[`docs/conventions.md`](docs/conventions.md).

## Docs map

| Doc | What's in it |
| --- | --- |
| [docs/conventions.md](docs/conventions.md) | Codebase map, build/test/lint commands, directory meanings, hazards, source-of-truth rule. |
| [docs/workstreams/](docs/workstreams/) | Per-workstream detail — objective, design, milestones, history, next steps ([W1](docs/workstreams/w1-related-files.md) · [W2](docs/workstreams/w2-solve-eval.md) · [W3](docs/workstreams/w3-quality-audit.md)). |
| [docs/decisions/](docs/decisions/) | Architectural decisions (ADRs). Includes the ⚠️ provisional patch-extraction decision log. |
| [docs/experiments/playbook.md](docs/experiments/playbook.md) | How we run experiments + investigations in this ML/eval repo. |
| [docs/patch-extraction.md](docs/patch-extraction.md) | Grounded corner-case survey for patch extraction (⚠️ provisional). |
| [docs/traces.md](docs/traces.md) | Off-repo trace storage (HF dataset repo + manifest). |
| [AGENTS.md](AGENTS.md) | Agent working rules (imported by `CLAUDE.md`). |

## Objective (recap)

The through-line across workstreams: produce and maintain **trustworthy** eval
data and machinery — ground-truth annotations (W1), a Docker pipeline that
*actually* solves and *correctly* grades tasks (W2), and audits that flag
instances which no longer measure real capability (W3).
