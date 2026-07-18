# Workstream 1 — Related-files annotation

**Status: ✅ COMPLETE — full dataset, 731/731 instances annotated & QA'd.**

Ground-truth "what code must a solver read" per instance. 7083 snippets over 37
rounds; final commit `6fe7095`. Nothing left to annotate. Code lives under
[`src/swe_lab/pipelines/related_files/`](../../src/swe_lab/pipelines/related_files/).

---

## Objective

For each SWE-Bench Pro task instance, produce a **ground-truth** annotation of
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
- `description` — one or two sentences: why this snippet must be read, what role
  it plays in solving the task, and roughly what it contains.
- `category` — a coarse, filterable label (see below).

### Categories

A small, extensible enum:

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
  `DockerProvider` using `dockerhub_tag` / `before_repo_set_cmd`.
- **Agent capability/mode is pluggable.** Now: **read-only**. Future: editing,
  running tests, and running arbitrary commands.
- **Preserve unused fields.** Keep `dockerhub_tag`, `before_repo_set_cmd`, etc.
  on the task model so future modes can use them without a schema change.

## Milestones

### Milestone 1 — Data-loading foundation ✅ *(done, 2026-07-06)*

Delivered (under `src/swe_lab/`, since reorganized into `core/`):

- `core/datasets/swebench_pro/record.py` — `SweBenchProInstance`, the typed
  frozen record over the 16 columns, plus parsing (list columns are Python
  `repr`; three text columns are only sometimes JSON-string-wrapped).
- `core/datasets/loader.py` — dataset-agnostic `Dataset` container +
  `DatasetRecord` protocol + a name→record-type registry; `load_dataset("swebench_pro")`.
- `core/repo/provider.py` — `RepoProvider` protocol + `GitCheckoutProvider` (bare
  mirror per repo + per-instance worktree at `base_commit`, cached under
  gitignored `.cache/repos/`).
- `core/paths.py` — repo-root / datasets / cache path helpers.
- Tests under `tests/` cover parsing, loading, and provider idempotency.

### Milestone 2 — Annotation agent runner (single instance) ✅ *(2026-07-07)*

Delivered (under `src/swe_lab/pipelines/related_files/`):

- `schema.py` — `SnippetCategory` (5 categories), `Snippet` / `Annotation`,
  agent-output parsing, per-snippet validation.
- `workspace.py` — provision the checkout + materialize hint files into
  `.annotation_context/`; agent writes `.annotation_output.json`.
- `agent_validator.py` — a standalone, stdlib-only validator dropped into the
  workspace. The agent self-checks and fixes its output until every snippet is
  valid *before* finishing; the runner imports the same code for its post-hoc
  check (single source of truth). Line numbering matches the Read tool: a
  trailing newline yields one extra (empty) addressable line, so an `end_line` of
  "last line + 1" on a newline-terminated file is accepted (the flipt off-by-one
  — a convention mismatch, not an agent error).
- `annotation_prompt.py` — the annotation instruction (read-only requested in the
  prompt, not enforced by tool restrictions; agent writes result to a file, then
  self-validates). The aggregator's prompt lives in `aggregator.py`.
- `agent_run.py` — the shared runner (`run_agent`). `annotator.py` and
  `aggregator.py` are thin task-specific wrappers; `pipeline.py` orchestrates 3
  samples + aggregate; `__main__.py` is the CLI; `storage.py` writes artifacts.

Two decisions locked during the first run: headless Claude Code uses the
**subscription OAuth** (validated to pass through the proxy — no API key), and
the extracted proxy record is kept whole. *(Both since evolved: **stream**
capture is now the default (no proxy), and the trace record moved off-repo to
HF. The `proxy.py` per-call reverse proxy on `base_port + index` is still
available via `--capture proxy`.)*

### Milestone 3 — Annotation storage & format ✅ *(done + committed, 2026-07-07)*

- **Storage: one directory per instance, under `intermediate/`** —
  `outputs/related_files/swebench_pro/intermediate/<instance_id>/` holding
  `candidate_1..3.json` (raw samples) + `aggregate.json` (per-instance
  deliverable), each paired with a `.last_exchange.json` (final trace record, for
  auditing). The combined deliverable is a single
  `outputs/related_files/swebench_pro/annotations.parquet`, built from every
  instance's `aggregate.json` by the `combine` binary
  (`python -m swe_lab.pipelines.related_files.combine`) — **one row per instance**
  (`instance_id`, `relevant_snippets`: a JSON string of the ordered snippet
  dicts). A `metadata.json` sidecar records row/snippet counts, timestamp, and
  the parquet's SHA-256.
- Per-instance dirs are chosen over a single JSONL because annotation is done by
  **random sampling**, not in order: independent dirs avoid ordering issues, make
  re-annotating one instance idempotent (overwrite just that dir), and produce
  clean diffs. Paths use the stable `instance_id`, not the dataset index.
- Unlike the downloaded dataset data files (gitignored), the annotation output
  **is version-controlled** — it is the ground-truth deliverable.
- **Session-success check.** The extracted last trace record carries a `complete`
  flag (set only when the stream ended with a proper `stop_reason`); if not
  `complete`, the annotation is flagged as probably unreliable.

### What gets committed vs. stored off-repo *(updated 2026-07-10)*

- **Committed to git** (the small deliverable): each instance's annotation JSON
  (`candidate_1..3.json` + `aggregate.json`) and the combined
  `annotations.parquet` (+ `metadata.json`), plus a `traces_manifest.json`.
- **Off-repo** (large, on the private HF dataset repo `luolc/swe-lab-traces`):
  the per-run `*.last_exchange.json` trace records. They were originally
  committed but grew to ~60 MB, so they moved to HF via `traces.py` (`push` /
  `fetch`, integrity-checked by sha256 in the manifest); the raw files are
  gitignored. Operator PII is redacted from every record at write time, and
  secrets never appear (auth header / org id scrubbed). See
  [`docs/traces.md`](../traces.md).

## How the full run went

The pipeline works end to end: `pipeline.py` runs N (=3) samples in parallel
(stream capture by default; phase 1 used the `cc-reverse-proxy` subscription
OAuth), the aggregator reconciles them, and `combine` rolls the aggregates into
`annotations.parquet`. Scaled from a validated **phase-1 staged stop of 100
instances** (5 rounds of 20; 100/100 valid, 98 ✅ / 2 ⚠️ / 0 severe) to the
**complete 731** over **37 rounds**, hand-QA'd + recall-audited every round:
**731/731 valid, all 3-candidate, 7083 snippets**. Full breakdown in
[`experiments/related_files/batch_annotation/qa_log.md`](../../experiments/related_files/batch_annotation/qa_log.md).

- **Concurrency ceiling.** Batch ran at **MAXJOBS=2** (≤6 headless agents) on the
  16 GB box; MAXJOBS=4 (12 agents, ~1–2 GB each) swap-thrashes. An earlier 13 h
  round-7 hang was root-caused to `capture_output=True` buffering big-repo
  streams in RAM + swap-thrash; fixed (`c4d12d5`: stream stdout to file +
  `killpg` on timeout). `perf_check.py` flags per-run stalls every round.
- **Usage limits.** The Claude subscription **credit wall** was hit a few times;
  each time we waited for the ~5 h reset and resumed cleanly (the runner skips
  instances whose `aggregate.json` already exists, so resume is idempotent — no
  scheduler was needed).
- **Recall audit.** `recall_audit.py` classifies every missed gold file as
  acceptable (doc/i18n/manifest/generated/build/test-data) vs real source. Across
  the whole 731-instance sweep, the sole remaining real gap (vuls-cc63a0ec
  kernel-list files) was confirmed a recall **ceiling** and accepted; all other
  flags verified **non-defects**. Lesson recorded: a low coverage ratio can be
  gold-patch contamination, not annotation failure — confirm each "source miss"
  against the problem statement.
- **Cost/throughput (actual).** ~$0.35–0.5 per instance (4 agent calls); the full
  731 ran across many multi-hour sessions.

## Shipped: sample-and-aggregate (self-consistency)

Started as an *option* and became the **production pipeline**
(`pipeline.py`). Each instance is annotated **3 times in parallel**, then an
**aggregator agent** reads the candidates and synthesizes one final annotation
(self-consistency over independently-sampled references). Per-run isolation
(own checkout `variant`, proxy `port`, log path; provisioning guarded by a lock)
lets repeats of one instance run concurrently. See the prompt-variance
[`REPORT.md`](../../experiments/related_files/prompt_variance/REPORT.md) for why
this beat single-run, and `aggregator.py` for the finalized reconciler prompt.

## Related experiments

- **Prompt variance** —
  [`experiments/related_files/prompt_variance/`](../../experiments/related_files/prompt_variance/)
  ([REPORT.md](../../experiments/related_files/prompt_variance/REPORT.md)). One
  instance per language × 3, over three prompt versions + aggregator iteration.
  Outcome: file selection is stable; the **v3** prompt fixed the main variance
  drivers; residual variance is inherent, resolved by the finalized aggregator.
  Cost ~$24 / 56 runs.
- **Batch annotation & QA** —
  [`experiments/related_files/batch_annotation/`](../../experiments/related_files/batch_annotation/)
  (`qa_log.md`, per-round id lists, `qa_check.py` / `perf_check.py` /
  `recall_audit.py`).

## Loose ends

- **Publish the fixed parquet.** The loader still patches 3 dataset-side
  truncated `fail_to_pass` names in memory (`patches.py`) — see
  [W2](w2-solve-eval.md) and
  [the eval-issue write-up](../../experiments/eval_issues/truncated_golden_test_names/README.md).
  Publish a corrected parquet to HF and retire the stopgap.

## Run one instance

```bash
python -m swe_lab.pipelines.related_files <instance_id> [--model sonnet|opus] [--samples 3]
```

## Deferred / future

- A human spot-check tool for annotation quality (beyond the `qa_check.py`
  coverage heuristic).
- Docker-based repo provisioning + an editing / test-running agent mode (the
  current agent is read-only, which is all annotation needs).
- **Outputs directory restructure (deferred — do as one focused change).** Move
  toward a per-dataset top-level layout, `outputs/<dataset>/<task>/`. The golden
  patch-validation task already writes there
  (`outputs/swebench_pro/patch_validation/`); the annotation deliverable still
  lives under the old `outputs/related_files/swebench_pro/` and should become
  `outputs/swebench_pro/related_files/`. Not done yet because it is large: it
  touches the `combine` binary, the HF upload/manifest paths, and every
  doc/path reference — worth one deliberate pass.
