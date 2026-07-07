# Project Plan — SWE-bench Related-Files Annotation

Living document. It captures the current direction and will be refined as we go.

## Status

**Read this first.** Snapshot of where the work stands so a fresh session can
pick up without guesswork. Update it whenever a milestone's state changes.

| Milestone | State | Notes |
| --- | --- | --- |
| 1 — Data-loading foundation | ✅ Done (2026-07-06) | See "Milestone 1" below for what shipped and where the code lives. |
| 2 — Annotation agent runner | ✅ Runner built + validated (2026-07-07) | Code under `annotate/`; validated end-to-end on one example (flipt). Prompt still being iterated for quality. |
| 3 — Annotation storage & format | 🚧 Storage implemented | `annotations/<id>.json` + extracted `.last_exchange.json` are written by the runner, but `annotations/` is **gitignored during prompt iteration** — not committed yet. |

**Right now:** the single-instance runner works end to end (subscription
OAuth through the per-call `cc-reverse-proxy`; agent writes its snippet list to
`.annotation_output.json`; runner parses, validates, and stores the two
artifacts). We are in the **prompt-iteration phase** (Development phasing step 1):
run a few examples, judge quality, refine `annotate/prompt.py`. Then annotate
10–20 examples, and only later un-gitignore `annotations/` to commit the
deliverable and scale up (batch inference, step 3).

Run one instance:
`python -m swebench_related_files_annotation.annotate <instance_id> [--model sonnet|opus]`.

## Objective

For each SWE-bench Pro task instance, produce a **ground-truth** annotation of
the code that a model would need to read in order to solve the task correctly.

This is about *building* ground truth, **not** about evaluating whether an
agent's file selection matches the gold patch. The gold `patch` / `test_patch`
are treated as *inputs / hints* the annotator may consult, not as a comparison
target.

## Core concepts

### Relevant code

Code that is genuinely needed to get the task right — e.g. a function the
solution must call or build on, a context file needed to understand how things
fit together, or an existing unit test that reveals the expected behavior.

### Code snippet (the annotation unit)

The atomic unit of annotation is a **code snippet**: one `file_path` plus one
**contiguous** line range (inclusive). A single file can contribute multiple
snippets when the relevant lines are non-contiguous (e.g. lines 1–100 and
200–300 are two separate snippets sharing the same `file_path`).

We use a **flat list of snippets** rather than a file-grouped structure: the
description is naturally per-range, and a flat list is simpler to serialize,
validate, and reason about. A "group by file" view is a trivial derivation when
needed.

Each snippet carries:

- `file_path` — path relative to the repo root.
- `start_line`, `end_line` — a contiguous, inclusive line range.
- `description` — one or two sentences in natural language: why this snippet
  must be read, what role it plays in solving the task, and roughly what it
  contains.
- `category` — a coarse, filterable label (see below).

### Categories

A small, extensible enum. Starting set (best-guess; easy to adjust later):

- `referenced-function` — a function/class/block the solution must call, use, or
  directly build on.
- `context-file` — surrounding code needed to understand how the pieces fit,
  even if not directly called.
- `useful-unit-test` — an existing test that reveals the expected behavior or
  contract.
- `interface-contract` — the required interface/signature/API the fix must
  conform to (relates to the dataset's `interface` field).
- `similar-pattern` — analogous code elsewhere in the repo to mirror when
  writing the fix.

The first three are the originally agreed categories; the last two are proposed
additions. Trim or extend as the annotation work reveals what is actually useful.

### Line-range precision

Precise to `file_path` + line range, using best judgment for the most
appropriate range. It need not cover a lot, nor be extremely tight.

## Annotation schema

One annotation record per instance:

```json
{
  "instance_id": "django__django-12345",
  "snippets": [
    {
      "file_path": "src/foo/bar.py",
      "start_line": 1,
      "end_line": 100,
      "category": "referenced-function",
      "description": "Defines Bar.resolve(), which sits on the call path of the bug; needed to see how None input is currently handled."
    }
  ],
  "metadata": { "model": "...", "run_id": "...", "timestamp": "..." }
}
```

## Information available to the annotation agent

For each instance the agent may consult **all information available for that
instance**: the full repo checkout, the `problem_statement`, `requirements`,
`interface`, the gold `patch` and `test_patch`, and even the `git log`. The gold
patch here is an input hint for producing accurate ground truth, not a target to
match.

## Design principles — keep future flexibility

We build only what the current read-only annotation flow needs, but structure
the code so these future directions require extension, not rewrite:

- **Repo access is pluggable.** A `RepoProvider` abstraction. Now:
  `GitCheckoutProvider` (clone + checkout `base_commit`). Future:
  `DockerProvider` using `dockerhub_tag` / `before_repo_set_cmd` to bring up the
  full per-instance environment.
- **Agent capability/mode is pluggable.** Now: **read-only** (the agent only
  reads files to identify relevant code). Future: editing, running tests, and
  running arbitrary commands.
- **Preserve unused fields.** Keep `dockerhub_tag`, `before_repo_set_cmd`, etc.
  on the task model so future modes can use them without a schema change.

## Milestones

### Milestone 1 — Data-loading foundation ✅ *(done, 2026-07-06)*

1. Add `polars` as the parquet reader.
2. Typed task-instance model over the 16 dataset columns.
3. Dataset loader: enumerate / filter / look up by `instance_id`; kept
   dataset-agnostic so more datasets can be added later (mirrors the existing
   `datasets/` layout).
4. `GitCheckoutProvider`: clone `repo` and checkout `base_commit` into a
   gitignored local cache; idempotent and reusable across runs.

**Delivered** (under `src/swebench_related_files_annotation/`):

- `datasets/swebench_pro.py` — `SweBenchProInstance`, the typed frozen record
  over the 16 columns, plus its column set and parsing (list columns are Python
  `repr`; three text columns are only sometimes JSON-string-wrapped).
- `datasets/loader.py` — dataset-agnostic `Dataset` container + `DatasetRecord`
  protocol + a name→record-type registry; `load_dataset("swebench_pro")`.
- `repo/provider.py` — `RepoProvider` protocol + `GitCheckoutProvider` (bare
  mirror per repo + per-instance worktree at `base_commit`, cached under
  gitignored `.cache/repos/`).
- `paths.py` — repo-root / datasets / cache path helpers.
- Tests under `tests/` cover parsing, loading, and provider idempotency.

### Milestone 2 — Annotation agent runner (single instance) ✅ *(built + validated, 2026-07-07)*

- Build a per-instance working directory from the provisioned repo.
- Prompt template: given the problem statement (+ `requirements` / `interface`)
  and access to the gold `patch` / `test_patch` / `git log`, ask the agent to
  return a structured list of code snippets (with line ranges, categories, and
  descriptions).
- **Per-call reverse proxy on a unique port.** Every Claude Code invocation
  starts its own `cc-reverse-proxy` instance on a distinct port derived from the
  instance's dataset index — `base_port + index` (e.g. `20000 + index`) to stay
  clear of privileged / commonly-used ports. This keeps ports unique across
  concurrent or interleaved runs. The proxy's log output is written to a
  per-instance path (named by `instance_id`) so logs never overwrite each other.
- Invoke Claude Code headless with `ANTHROPIC_BASE_URL` pointed at that
  instance's proxy so every request/response is logged.
- Parse the structured output into the annotation schema.

**Delivered** (under `src/swebench_related_files_annotation/annotate/`):

- `schema.py` — `SnippetCategory` (5 categories), `Snippet` / `Annotation`,
  agent-output parsing, per-snippet validation.
- `proxy.py` — build `cc-reverse-proxy` from the submodule; run one per call on
  `base_port + index` (`DEFAULT_BASE_PORT = 20000`) with a per-instance log.
- `workspace.py` — provision the checkout + materialize hint files into
  `.annotation_context/`; agent writes `.annotation_output.json`.
- `agent_validator.py` — a standalone, stdlib-only validator dropped into the
  workspace. The agent runs it (`python3 .annotation_context/validate_annotation.py`)
  to self-check and fix its output until every snippet is valid *before*
  finishing; the runner imports the same code for its post-hoc check (single
  source of truth). Line numbering matches the Read tool: a trailing newline
  yields one extra (empty) addressable line, so an `end_line` of "last line + 1"
  on a newline-terminated file is accepted (this was the flipt off-by-one — a
  convention mismatch, not an agent error).
- `prompt.py` — the instruction (read-only requested in the prompt, not enforced
  by tool restrictions; agent writes its result to a file, then self-validates).
- `runner.py` + `__main__.py` — orchestrate everything and store artifacts.

Two decisions locked during the first run: headless Claude Code uses the
**subscription OAuth** (validated to pass through the proxy — no API key), and
the extracted proxy record is **kept whole** (~144 KB/instance).

### Milestone 3 — Annotation storage & format 🚧 *(implemented; not committed yet)*

- Storage: **one file per instance** — `annotations/<instance_id>.json`. Unlike
  the downloaded dataset data files (which are gitignored), the annotation output
  **is version-controlled**: these files are the ground-truth deliverable and are
  committed and pushed to the repo. One-file-per-instance is chosen over a single
  JSONL because annotation is done by random sampling rather than in order:
  independent files avoid ordering issues, make re-annotating a single instance
  idempotent (overwrite just that file), and produce clean diffs. The filename
  uses the stable `instance_id` rather than the dataset index (which can drift
  if the dataset changes).
- Validation:
  - Each snippet's `file_path` exists in the checked-out repo, and
    `start_line <= end_line <=` the file's line count.
  - **Session-success check.** The extracted last proxy record carries a
    `complete` flag (set only when the stream ended with a proper `stop_reason`).
    If it is not `complete`, the annotation session likely did not finish, so the
    resulting annotation is probably unreliable and should be flagged rather than
    trusted. (Detail to refine during Milestone 2 iteration.)

### What gets committed vs. kept local

Two artifacts per instance are committed and pushed to the repo:

1. **The structured annotation** — `annotations/<instance_id>.json` (the code
   snippets described above).
2. **A single extracted proxy record** — the **last** record from that instance's
   `cc-reverse-proxy` log, capturing the final exchange: the request is the
   conversation from the first message up to the second-to-last message, and the
   response is the model's final answer. Just this one record is small enough to
   version-control, and it preserves how the annotation was produced.

The **full** `cc-reverse-proxy` output (every request/response for the whole run)
is **not** tracked — it is large, so it stays local / gitignored. Only the single
extracted last record is committed.

## Development phasing (respecting Claude Code usage limits)

Claude Code has usage limits, so we deliberately avoid running all 731 instances
early.

1. **Prompt iteration** — iterate the annotation sub-agent's prompt on **1–2
   examples** until the output quality is good.
2. **First goal** — annotate **10–20 examples**. No concurrency needed at this
   stage.
3. **Batch inference (last)** — scale to the full dataset. Because Claude Code
   credits refresh periodically, this is expected to run on a recurring schedule
   (e.g. every few hours) rather than all at once. The current Claude Code
   version already supports this via scheduled remote agents / cron routines
   (the `/schedule` and `/loop` capabilities), so no upgrade is required. Design
   details (resume, deduplication of already-done instances, throttling) are
   deferred to this phase.

## Deferred / future work

- Batch orchestration & CLI (resume, dedup, `rich` progress), driven on a
  recurring schedule.
- Docker-based repo provisioning and an editing / test-running agent mode.
- (Optional) a human spot-check tool for annotation quality.
