# Project Plan — swe-lab

Living document. It captures the current direction and will be refined as we go.

## Scope

`swe-lab` is an umbrella for tooling that **enriches, runs, and audits
SWE-Bench evaluation data**, organized as independent workstreams over shared
infrastructure in `src/swe_lab/core/` (dataset loading, per-instance
repo checkout, a headless-agent harness, a Docker execution layer, and the
dataset-agnostic benchmark contracts).

- **Workstream 1 — Related-files annotation** (`tasks/related_files/`) —
  ground-truth "what code must a solver read" per instance. **✅ Complete — the
  full dataset, 731/731 instances annotated & QA'd** (7083 snippets; 37 rounds;
  phase 1 via the reverse proxy, the rest in the default **stream** capture).
  Nothing left to annotate. See
  [`tasks/related_files/README.md`](src/swe_lab/tasks/related_files/README.md).
- **Workstream 2 — Solve + evaluate pipeline** (`rollout/` + `evaluation/` on
  `core/docker/`) — actually *solve* SWE-Bench Pro tasks in Docker and *grade*
  the patches. **Eval built & validated**; rollout (agent sampling) is next. See
  "Workstream 2" below.
- **Workstream 3 — Quality auditing / skew** *(planned; first tool falls out of
  W2)* — flag eval instances that no longer measure real capability, à la
  OpenAI's *Separating signal from noise in coding evaluations*. A **gold
  self-test sweep** (grade every instance's own gold patch; any that does *not*
  resolve is a broken/skewed instance) drops straight out of the eval pipeline.

## Status

**Read this first.** Snapshot of where the work stands so a fresh session can
pick up without guesswork. Update it whenever a milestone's state changes.

**Latest (2026-07-16).**

- **W1 (annotation) — ✅ COMPLETE, full dataset.** All **731/731** instances are
  annotated, QA'd, and pushed (final commit `6fe7095`): **7083 snippets** over
  **37 rounds**, every round 20/20 (last 10/10) valid and 3-candidate at
  **MAXJOBS=2**. The runner defaults to **stream capture**
  (`claude --output-format stream-json`, no reverse proxy; `--capture proxy`
  still available), producing a **source-agnostic unified exchange record** with
  operator PII redacted at write time (home/name/email → an "Alan Turing"
  placeholder). The large per-run trace records live **off-repo in a private HF
  dataset repo** (`luolc/swe-lab-traces`, **2924 files / 412.6 MB**)
  via `traces.py` push/fetch + a git-tracked `traces_manifest.json`; only the
  small annotation JSON + parquet stay in git. `GitCheckoutProvider` hardened to
  self-heal stale worktrees. **Nothing is left to annotate.**
  - **Concurrency ceiling.** Batch ran at **MAXJOBS=2** (≤6 headless agents) on
    the 16 GB box; MAXJOBS=4 (12 agents, ~1–2 GB each) swap-thrashes. An earlier
    13 h round-7 hang was root-caused to `capture_output=True` buffering big-repo
    streams in RAM + swap-thrash; fixed (`c4d12d5`: stream stdout to file +
    `killpg` on timeout). `perf_check.py` flags per-run stalls every round. The
    Claude subscription **credit wall** was hit a few times across the full run;
    each time we waited for the ~5 h reset and resumed cleanly (the runner skips
    instances whose `aggregate.json` already exists).
  - **Recall audit.** `recall_audit.py` classifies every missed gold file as
    acceptable (doc/i18n/manifest/generated/build/test-data) vs real source, so
    genuine recall gaps surface instead of hiding among routine exclusions. Across
    the whole 731-instance sweep, every flag was manually reviewed; the sole
    remaining real gap (vuls-cc63a0ec kernel-list files) was confirmed a recall
    **ceiling** and accepted, and all other flags verified **non-defects**
    (comment/typo-only edits, pure moves, deletions, submodule bumps, generated
    `.pb.go`, i18n, build/CI, examples, installers). Lesson recorded: a low
    coverage ratio can be gold-patch contamination, not annotation failure —
    confirm each "source miss" against the problem statement.
- **W2 (solve + eval)** — the **evaluation** subsystem is built and validated,
  and the **full gold self-test sweep is done** (731/731 golden patches resolve;
  3 dataset-side false negatives fixed in-loader — see "Full golden sweep" under
  Workstream 2). Gold self-tests **resolve** on **GitHub Actions** (native amd64,
  ~1–2.5 min/instance, free minutes, no secrets). **`rollout` (agent sampling) is
  the current focus** — a subscription `CLAUDE_CODE_OAUTH_TOKEN` is now available;
  the extractor's open design decisions are catalogued in "Patch extraction —
  open decisions" under Workstream 2. Architecture and next steps below.
- The repo was renamed `swe-lab` and reorganized into `core/` +
  `tasks/` (+ new `rollout/`/`evaluation/`); git history was scrubbed of a
  leaked OAuth token and operator PII (force-pushed).

The rest of this file is per-workstream detail. **W1 milestones/history**
follow; **W2** has its own section further down.

| Milestone (W1 — annotation) | State | Notes |
| --- | --- | --- |
| 1 — Data-loading foundation | ✅ Done (2026-07-06) | See "Milestone 1" below for what shipped and where the code lives. |
| 2 — Annotation agent runner | ✅ Done (2026-07-06) | `tasks/related_files/` — single-instance runner **and** the 3-sample-then-aggregate pipeline; both prompts finalized (annotation v3, aggregator). |
| 3 — Annotation storage & format | ✅ Done (2026-07-07) | `outputs/related_files/<dataset>/<instance_id>/` with `candidate_1..3` + `aggregate` (each with `.last_exchange.json`). Committed & pushed (the deliverable). |

**Full dataset complete — 731/731 instances annotated & QA'd.** The pipeline works
end to end: `tasks/related_files/pipeline.py` runs N (=3) samples in parallel (stream
capture by default; phase 1 used the `cc-reverse-proxy` subscription OAuth), then
the aggregator reconciles them; every artifact is stored under
`outputs/related_files/swebench_pro/intermediate/<instance_id>/`, and the `combine` binary
rolls the aggregates up into `outputs/related_files/swebench_pro/annotations.parquet`. Both
prompts are finalized (see the experiment report). Scaled from a validated
**phase-1 staged stop of 100 instances** (5 rounds of 20; 100/100 valid,
98 ✅ / 2 ⚠️ / 0 severe) all the way to the **complete 731** over **37 rounds**,
hand-QA'd + recall-audited every round: **731/731 valid, all 3-candidate, 7083
snippets**. Full breakdown in
`experiments/related_files/batch_annotation/qa_log.md`.

**Done — prompt-variance experiment** (`experiments/related_files/prompt_variance/`, see
`REPORT.md`). One instance per language × 3, over three prompt versions + the
aggregator-prompt iteration. Outcome: file selection is stable; the **v3**
annotation prompt fixed the main variance drivers; residual variance is mostly
inherent (how much of a test to include), which the **finalized aggregator**
resolves by judgment. Runner hardened (failure classification, retry-on-transient,
stop-on-usage-limit, diagnostics); harness fully parallel (per-instance repeats
isolated by checkout variant + proxy port). Cost so far: ~$24 / 56 runs.

**Done — batch inference & QA, phase 1** (`experiments/related_files/batch_annotation/`).
Random-sampled instances annotated with the full pipeline, QA'd per instance.

- **Rounds (20 each, pairwise-disjoint):** ids in `round{1..5}_ids.txt` (seed
  `20260706`). **All 5 rounds ✅ done — 100/100 valid, 98 ✅ / 2 ⚠️ / 0 ❌, 0
  severe** (see `qa_log.md` final tally). The 2 ⚠️ are single existing-file
  omissions (round-1 #11 two UI templates; round-2 #6 `parse_xml.py`). Rounds 1–2
  were drawn as `Random(20260706).sample(dataset_ids, 40)`; round 3 as a separate
  draw from the remaining pool; rounds 4–5 as `Random(20260706).sample(remaining,
  40)` where `remaining` excludes rounds 1–3. **Each round excludes every
  already-run id** — the pipeline does NOT skip; re-running an id re-does all 4
  agent calls and overwrites (wasted tokens). Launch commands carry a
  `[ -f …/aggregate.json ] && SKIP` guard.
- **Mechanism:** one `python -m …annotate <id>` per instance (3 samples +
  aggregate). Rolling window of ~4 concurrent pipelines (8-core / 16 GB box; 20
  at once would OOM). QA each result on completion → `qa_log.md`; per-run stdout
  in gitignored `.cache/batch-logs/`.
- **QA rule:** brief ✅ if valid + covers the *existing* gold-patch code files
  (new files / docs / dep-manifests / generated code correctly excluded). minor →
  log & keep going; severe → log + explain in chat.
- **Robustness proven in the field, two fixes landed.** (1) A flaky sample that
  ends without writing its output no longer kills the pipeline: no-output is
  retried and the aggregate tolerates a failed sample (`df22a2f`). (2) A CLI that
  exits nonzero writes its error (incl. API status) to **stdout**, not stderr — we
  now parse stdout to classify it, and treat a mid-session API 401 as retryable
  (`f78594c`, `3044d87`). Both were triggered by real batch failures and recovered.
- After round1 a **coverage line** was added to both prompts (see the report's
  "Batch-QA Coverage Refinement"); spot-check confirmed it.
- **`outputs/related_files/` is committed** (the deliverable); pushed after each round.

**Resume after a session break:** `qa_log.md` rows + `outputs/related_files/swebench_pro/<id>/`
show what's done. **The full dataset (731/731) is complete and pushed — nothing is
mid-flight and there is nothing left to annotate.** (For the record, the re-run
mechanism still works: the pipeline CLI skips any instance whose `aggregate.json`
already exists, so re-running an id is idempotent.)

Run one instance (full pipeline):
`python -m swe_lab.tasks.related_files <instance_id> [--model sonnet|opus] [--samples 3]`.

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

**Delivered** (under `src/swe_lab/`):

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

**Delivered** (under `src/swe_lab/tasks/related_files/`):

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
- `annotation_prompt.py` — the annotation instruction (read-only requested in the
  prompt, not enforced by tool restrictions; agent writes its result to a file,
  then self-validates). The aggregator's prompt lives in `aggregator.py`.
- `agent_run.py` — the shared runner (`run_agent`): provision workspace, invoke
  headless Claude Code through a per-call proxy with retries + failure
  classification, read/validate/store. `annotator.py` and `aggregator.py` are thin
  task-specific wrappers over it; `pipeline.py` orchestrates 3 samples + aggregate;
  `__main__.py` is the CLI. `storage.py` writes the artifacts.

Two decisions locked during the first run: headless Claude Code uses the
**subscription OAuth** (validated to pass through the proxy — no API key), and
the extracted proxy record is **kept whole** (~144 KB/instance). *(Both since
evolved — see the "Latest" block: **stream** capture is now the default (no
proxy), and the trace record moved off-repo to HF.)*

### Milestone 3 — Annotation storage & format ✅ *(done + committed, 2026-07-07)*

- Storage: **one directory per instance, under `intermediate/`** —
  `outputs/related_files/<dataset>/intermediate/<instance_id>/` holding
  `candidate_1..3.json` (the raw samples) + `aggregate.json` (the per-instance
  deliverable), each paired with a `.last_exchange.json` (final proxy record, for
  auditing). The combined deliverable is a single
  `outputs/related_files/<dataset>/annotations.parquet` beside `intermediate/`, built from
  every instance's `aggregate.json` by the `combine` binary (`python -m
  swe_lab.tasks.related_files.combine`) — **one row per instance**
  (`instance_id`, `relevant_snippets`: a JSON string of the ordered snippet dicts
  `file_path`, `start_line`, `end_line`, `category`, `description`). A
  `metadata.json` sidecar records row/snippet counts, timestamp, and the
  parquet's SHA-256. It is small (~100 KB / 100 instances) and committed
  directly; no Git LFS needed. See `storage.py` and `combine.py`.
  Unlike the downloaded dataset data files (which are gitignored), the annotation
  output **is version-controlled**: it is the ground-truth deliverable, committed
  and pushed. Per-instance directories are chosen over a single JSONL because
  annotation is done by random sampling rather than in order: independent
  directories avoid ordering issues, make re-annotating a single instance
  idempotent (overwrite just that directory), and produce clean diffs. Paths use
  the stable `instance_id`, not the dataset index (which can drift). The
  `<dataset>` segment keeps room for datasets beyond `swebench_pro`.
- Validation:
  - Each snippet's `file_path` exists in the checked-out repo, and
    `start_line <= end_line <=` the file's line count.
  - **Session-success check.** The extracted last proxy record carries a
    `complete` flag (set only when the stream ended with a proper `stop_reason`).
    If it is not `complete`, the annotation session likely did not finish, so the
    resulting annotation is probably unreliable and should be flagged rather than
    trusted. (Detail to refine during Milestone 2 iteration.)

### What gets committed vs. stored off-repo *(updated 2026-07-10)*

**Committed to git** (the small deliverable): each instance's annotation JSON
(`candidate_1..3.json` + `aggregate.json`) and the combined
`annotations.parquet` (+ `metadata.json`), plus a `traces_manifest.json`.

**Off-repo** (large, on the private HF dataset repo `luolc/swe-lab-traces`):
the per-run **`*.last_exchange.json`** trace records — one per candidate/aggregate,
each the run's unified exchange record (final request/response, `complete` flag,
model, etc.). They were originally committed but grew to ~60 MB, so they moved to
HF via `traces.py` (`push` / `fetch`, integrity-checked by sha256 in the
manifest); the raw files are gitignored. Operator PII is redacted from every
record at write time, and secrets never appear (auth header / org id scrubbed).

## Development phasing (respecting Claude Code usage limits)

Claude Code has usage limits, so we deliberately paced the run rather than firing
all 731 instances at once. **All three steps are now complete — the full dataset
is annotated (731/731).**

1. **Prompt iteration** ✅ — iterated the annotation sub-agent's prompt on 1–2
   examples until output quality was good (see prompt-variance `REPORT.md`).
2. **First goal** ✅ — annotate 10–20 examples, then extended to **100** with a
   rolling concurrency window and per-instance QA.
3. **Batch inference to the full dataset** ✅ — scaled to **all 731** over 37
   rounds. Rather than a scheduler, this ran as hand-driven rounds with a
   MAXJOBS=2 rolling window; when the subscription credit wall was hit we waited
   for the ~5 h reset and resumed (the runner skips instances whose
   `aggregate.json` already exists, so resume is idempotent — no scheduler was
   needed in the end).

## Deferred / future work

**Phase 2 — scale to the full dataset (731 instances) — ✅ DONE (2026-07-16).**
The full dataset is annotated; the notes below are retained as a record of how it
was run and the one loose end.

- **Batch driver.** Never built as a standalone CLI — the run stayed a hand-driven
  per-round shell loop (`run_round.sh`) with a MAXJOBS=2 rolling window and the
  `aggregate.json`-exists skip guard, which proved sufficient. If a proper batch
  CLI is ever wanted (e.g. for a second dataset), re-add `rich` for progress.
- **Usage-limit handling.** Worked as designed: `UsageLimitError` stops the run
  on a quota wall; we waited for the ~5 h subscription reset and resumed (skip
  guard makes resume idempotent). Hit the wall a few times over the full run;
  recovered cleanly each time.
- **Cost/throughput (actual).** ~$0.35–0.5 per instance (4 agent calls); the full
  731 ran across many multi-hour sessions as expected.

**Loose end (annotation).** Nothing blocks W2, but for completeness:

- **Publish the fixed parquet.** Independent of annotation but adjacent: the
  loader still patches 3 dataset-side truncated `fail_to_pass` names in memory
  (`patches.py`); publish a corrected parquet to HF and retire the stopgap (also
  tracked under Workstream 2).

**Quality follow-ups (optional).**

- A human spot-check tool for annotation quality (beyond the `qa_check.py`
  coverage heuristic).
- The single-existing-file omission pattern (the phase-1 2 ⚠️) did **not** grow
  into a systemic problem at full scale — recall audits across all 731 rounds
  found only one accepted recall ceiling (vuls-cc63a0ec) and no systemic miss.

**Later phases.** Docker-based repo provisioning and an editing / test-running
agent mode (the current agent is read-only, which is all the annotation task
needs).

**Outputs directory restructure (deferred — do as one focused change).** We are
moving toward a per-dataset top-level layout, `outputs/<dataset>/<task>/`. The
golden **patch validation** task already writes there
(`outputs/swebench_pro/patch_validation/`). The existing annotation deliverable
still lives under the old shape, `outputs/related_files/swebench_pro/`, and
should be renamed to `outputs/swebench_pro/related_files/`. This is intentionally
**not** done yet because it is large: besides moving the files it touches the
`combine` binary, the HF dataset-repo upload/manifest paths, and every doc/path
reference — worth doing in one deliberate pass, not piecemeal.

### Shipped: sample-and-aggregate (self-consistency)

This started as an *option* — an alternative to converging on one "perfect"
prompt — and is now the **production pipeline** (`tasks/related_files/pipeline.py`). Each
instance is annotated **3 times in parallel**, then an **aggregator agent** reads
the candidates and synthesizes one final annotation (self-consistency over
independently-sampled references). The engineering prerequisite it once lacked —
per-run isolation so repeats of one instance run concurrently — was built: each
run gets its own checkout `variant`, proxy `port`, and log path (provisioning
guarded by a lock). See the prompt-variance `REPORT.md` for why this beat
single-run, and `aggregator.py` for the finalized reconciler prompt.

---

# Workstream 2 — Solve + evaluate pipeline

Started 2026-07-09. Build a **robust, Docker-based pipeline that actually solves
SWE-Bench Pro tasks** (an agent generates a patch) and **evaluates** them (apply
the patch, run the tests, grade). Reuse the best existing references rather than
reinvent; no existing harness fully fits, so we build our own around them.

## Objective & split

Two decoupled flows over a shared Docker layer:

- **`rollout`** *(agent sampling — planned)* — run a headless coding agent
  **inside** an instance's prebuilt container and capture its full trajectory +
  the resulting patch (`git diff`). Deliberately **not** called "solver": trace
  generation is general (solving is one use; later we'll also, e.g., feed a
  trajectory back in for behavioral analysis). The agent harness is **pluggable**
  (Claude Code first; Codex / OpenCode / … later) — the harness-agnostic contract
  is *patch = `git diff` of the workdir*; the trace format is per-harness.
- **`evaluation`** *(built + validated)* — apply a candidate patch, run the
  instance's tests, parse, and grade `resolved ⇔ (fail_to_pass ∪ pass_to_pass) ⊆
  passed`.

## Reference

Official harness cloned at `/Users/luoliangchen/dev/3p/scaleapi/SWE-bench_Pro-os`
(MIT). We **reuse** its prebuilt per-instance Docker Hub images
(`jefzda/sweap-images:<dockerhub_tag>`), its per-instance `run_script.sh` +
`parser.py`, and its grading rule; we **port** its `create_entryscript` logic. We
do **not** vendor its ~1000 harness files or take it as a submodule — instead we
**fetch** each instance's `run_script`/`parser` from a **pinned commit**
(`ca10a60…`, tip of `origin/main` at 2026-07-10) into a gitignored cache. Solver
references: **mini-swe-agent** (MIT; Scale's leaderboard scaffold) and the
**Claude Agent SDK** (MIT).

## Architecture — general flow + per-dataset adapter

Mirrors the `datasets/` split (general loader + per-dataset record). **General,
dataset-agnostic** code never learns a dataset's specifics; each dataset provides
an **adapter**:

- `core/benchmark.py` — the shared contract: `EvalSpec` (image ref, workdir,
  base_commit, before_repo_set_cmd, run_script/parser content, test lists,
  grading) + `BenchmarkAdapter` protocol. NB: `EvalSpec` still carries
  SWE-Bench-Pro-shaped fields (`run_script`/`parser`); the general/per-dataset
  boundary here is provisional until a second dataset forces it to firm up.
- `core/datasets/swebench_pro/` — a **package** holding **all** SWE-Bench-Pro
  run-knowledge: `record.py` (the record) + `execution.py` (`SweBenchProAdapter`:
  jefzda image ref, pinned scaleapi harness fetch, `EvalSpec` builder) +
  `grading.py` (the SBP grader — ports Scale's `create_entryscript`, stages the
  workspace, runs it, parses `output.json`, grades → `EvalResult`; `build_eval_script`
  also has `apply_patch` / `checkout_golden_tests` flags for dataset self-checks).
  The grader is dataset-specific — plain SWE-Bench has no `run_script`/`parser`.
  Adding a dataset = adding a sibling adapter package.
- `core/docker/provider.py` — general `DockerProvider` (pull; run a script in a
  bind-mounted container; `linux/amd64`).
- `evaluation/` — the general eval **CLI** only (`__main__`): pick a dataset,
  build its `EvalSpec`, hand it to that dataset's grader. CLI: `python -m
  swe_lab.evaluation <id> --gold` (grade the gold patch as a self-test)
  or `--patch-file`. Only SWE-Bench Pro is wired up today.
- `.github/workflows/eval.yml` — manual gold self-test on a GitHub-hosted runner.

## Decisions (2026-07-10)

- **Execution = GitHub Actions.** Debug on the private repo's free 2000
  min/month. First real container run on GH Actions (native amd64 — no local
  Apple-Silicon emulation; gold eval needs no secrets). Public-repo (free,
  unlimited minutes) decision deferred; if needed, a minimal public repo can
  wrap this one.
- **Job model is per-flow (updated 2026-07-13).** Two options: **(A) container
  job** (`jobs.<x>.container.image: <instance image>` — the whole job *is* the
  image) vs **(B) ubuntu runner + `docker run`** (job on ubuntu host, image run
  as a throwaway container). **These are NOT freely interchangeable with one CLI**
  as an earlier note implied: the current `core/docker/provider.py` is B-only (it
  shells `docker run`); using it under a container job would be docker-in-docker.
  Supporting A needs a separate "run the entryscript directly in the job, no
  docker" path.
  - **Runtime efficiency is equivalent.** A container is namespaced processes on
    the host kernel, not a VM — commands run at native speed either way; overlayfs
    and `docker exec` overhead are negligible; the image is pulled once either way.
    The only real perf axis is amd64 *emulation*, which is a local-Apple-Silicon
    issue, absent on GH amd64 runners, and orthogonal to A-vs-B. So choose by
    ergonomics/constraints, not speed.
  - **eval → B (shipped).** Harness (grading) stays on the host; one job can
    `docker run` many instances sequentially (economical for the 731 gold sweep);
    same code path as local; full control of `--platform` / `--network none` /
    `--rm`. `eval.yml` = `runs-on: ubuntu-latest` + `python -m ...evaluation`.
  - **rollout → A (planned).** It is naturally one-instance-per-job (the agent
    runs minutes → one patch), so B's multi-instance edge doesn't apply. The
    Claude Code binary runs in the job shell (which *is* the sandbox), edits the
    repo, runs tests, then `git diff` — no container-lifecycle / `docker exec`
    juggling. Caveat: the image must be a valid GH job container (GH injects node;
    minimal images can miss libs). NB: "mount the pinned Claude Code linux-x64
    binary" is **orthogonal** to A-vs-B — it's how the agent enters the container
    in *either* model, not a reason to pick A.
- **Auth.** P0 = **Claude subscription** via `CLAUDE_CODE_OAUTH_TOKEN`
  (`claude setup-token`); P1 = **OpenRouter** (Anthropic-compatible endpoint —
  `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, `ANTHROPIC_AUTH_TOKEN=<orkey>`,
  `ANTHROPIC_API_KEY=""`, model `~anthropic/claude-sonnet-latest`), Claude models
  only, chosen for its more flexible limits.
- **Claude Code in the container** = mount a **pinned native linux-x64 binary**
  (downloaded at runtime to a gitignored cache — **never** committed), not an
  npm-in-a-wrapper-image (which would mean building/pushing ~731 images and
  rebuilding on every version bump).
- **Trace storage** reuses the W1 pattern (HF dataset repo + manifest).

## Progress — evaluation validated ✅

Gold self-tests **resolve** end to end:

| instance | lang / runner | where | result |
| --- | --- | --- | --- |
| flipt-io/flipt | Go / `go test` | local (emulated) | resolved ✅ |
| flipt-io/flipt | Go / `go test` | GitHub Actions | resolved ✅ (~2.5 min) |
| ansible/ansible | Python / `ansible-test` | GitHub Actions | resolved ✅ (~57 s) |

Neither needed ENV-scraping nor `test_patch` (the tests exist at `base_commit`).
The GH runner is native amd64 → the whole thing (checkout + `uv sync` + dataset
download + image pull + test + grade) is ~1–2.5 min/instance and free.

### Full golden sweep done — 3 dataset-side false `GOLDEN_FAIL`s fixed *(2026-07-16)*

The **gold self-test sweep** (step 4 below) was run across the whole dataset (GH
Actions run `29463094538`): **728/731** golden patches resolve. The **3** that
did not — NodeBB, ansible, vuls — were all diagnosed as **false negatives in the
upstream dataset, not harness bugs**: their `fail_to_pass` lists carry a handful
of test names **truncated by exactly one trailing character** (a closing `"` in
seven cases, a trailing space in one), so grading's exact string-set membership
scores those (actually-passing) tests as missing. Confirmed via local Docker
repro (base fails / golden passes the exact required count) and cross-checked
against Scale's own `swe_bench_pro_eval.py`, which fails identically on the same
data — full write-up in
[`experiments/eval_issues/truncated_golden_test_names/`](experiments/eval_issues/truncated_golden_test_names/README.md).

**Temporary fix (in place):** rather than re-host the parquet yet, the loader
still downloads the *original* upstream parquet and corrects only these 8 entries
**in memory at load time** — see
`core/datasets/swebench_pro/patches.py` (`patch_fail_to_pass`, applied in
`record.from_raw`; a no-op on every other row and self-limiting once the data is
fixed). With it, all three gold-eval `resolved = true` locally via the real
`python -m swe_lab.evaluation <id> --gold` path → the sweep is
effectively **731/731**. **End state (TODO):** publish one fully-fixed parquet to
our own Hugging Face dataset repo and point the loader at it; then delete
`patches.py`.

## Next steps

**Priority (set 2026-07-14): `rollout` first**, because it takes wall-clock time
to run; while it runs we build matrix-eval + the gold sweep. A subscription
`CLAUDE_CODE_OAUTH_TOKEN` is now available (stored in gitignored `.envrc.local`;
rotate after use).

1. **`rollout` — the container agent loop.** Run headless Claude Code inside each
   instance's prebuilt image, capture the trajectory + the patch. Sub-tasks:
   - **Patch extraction** is the hard, error-prone part and has its own grounded
     spec: **[`docs/patch-extraction.md`](docs/patch-extraction.md)** (surveys
     SWE-Bench Pro / classic, SWE-agent, mini-swe-agent, OpenHands, Agentless,
     Moatless, R2E-Gym; ~40 corner cases). Implement §7 of that doc verbatim:
     isolated-config `git add -A` (with `:(exclude)` build noise + nested-`.git`
     removal) → `git diff --cached --binary --no-textconv --no-ext-diff
     --no-color --default-prefix -c core.quotepath=false -c core.autocrlf=false
     <base_commit>` → **write raw bytes** → empty-patch guard.
   - Extract the generic "run Claude Code headless + stream-json trace + exchange
     record" from `tasks/related_files/agent_run.py` into `core/agent/` so rollout
     reuses it.
   - Mount a pinned native linux-x64 Claude Code binary (gitignored cache, never
     committed); GH Actions **container job** model (one instance per job).
2. **Close eval gap** (cheap, do alongside rollout — see
   `docs/patch-extraction.md` §8): an **empty-patch guard**. (Binary hunks are
   *not* stripped at grading — decided 2026-07-15 to keep binary out of the
   patch upstream at extraction instead; `grading.py` applies it verbatim.)
   Confirm the **open item**: does Pro's per-instance harness reset agent-touched
   *test files*, or must we? (`docs/patch-extraction.md` §5.1.)
3. **Matrix eval** — one dispatch grading many instances in parallel (256
   matrix-cap → shard across workflows). The path to running all 731. Build while
   rollout runs.
4. **Gold self-test sweep** ✅ *(done 2026-07-16)* — graded every instance's own
   gold patch; 728/731 resolved, and the 3 that didn't were dataset-side
   truncated-name false negatives, now fixed in-loader (see "Full golden sweep"
   above) → 731/731. Remaining follow-up: **publish the fully-fixed parquet to
   Hugging Face** and retire the in-memory `patches.py` stopgap.

## Patch extraction — decisions (settled 2026-07-17)

These were open items reviewed against `docs/patch-extraction.md` (the grounded
corner-case survey) and the implementation in
[`core/patch.py`](src/swe_lab/core/patch.py). **They are now decided**
(user, 2026-07-17); the code, its docstrings, and the doc have been reconciled to
match. Each item below keeps its rationale and records the **✅ decision**; the
per-item history (doc-said / code-said / tension) is retained as the audit trail.

**Decisions at a glance**

| # | Topic | Decision | P1/later backlog |
| --- | --- | --- | --- |
| D1 | diff base | diff vs the instance's **`base_commit`** (guarantees the grading round-trip) | post-setup-commit base → **P1** |
| D2 | build-noise denylist | **none** by default (`exclude_globs` empty); no material impact today | per-ecosystem `:(exclude)` denylist → **P1** |
| D3 | binary | **happy path: text only** — `git add -N` + diff **without `--binary`**, then `strip_binary_hunks` on the host removes the residual bytes-free marker | faithful binary extract+apply → **P1** |
| D4 | diff prefixes | keep the **`-c diff.noprefix=false -c diff.mnemonicPrefix=false`** pins (empirically equivalent to `--default-prefix`, and needed for git < 2.41) | — |
| D5 | agent test-file edits | grading already restores the **golden** test files by path (see finding) | fuller all-test-file reset → **P2 / monitor** |
| D6 | eval apply tolerance | **no change** — keep the single strict `git apply -v` (matches Scale) | opt-in `--3way`/`--reject` ladder → later, behind a flag |
| D8 | empty patch | **rollout-side explicit outcome** (`empty_patch` vs `unresolved_tests_failed`), never graded as a pass | eval-side defensive guard → **P2** |

D7 (deferred silent-failure guards: gitignored-new-source, submodules, LFS) stays
deferred; add detection + logging when first needed (**P1** when it bites).

The per-item detail below is the rationale record.

### D1. Base to diff against: post-setup commit vs raw `base_commit`

- **Doc (§7, §2):** step 2 diffs `--cached ... "$BASE_COMMIT"` — i.e. against the
  instance's original `base_commit`.
- **Code:** `build_extraction_script(base_ref=...)` diffs against a caller-supplied
  `base_ref`, and its docstring specifies `base_ref` should be *"the post-setup
  commit the rollout entryscript makes right before the agent runs"* — a fresh
  commit of the container's post-provision state, **not** `base_commit`.
- **Tension:** these are different strategies. Diffing against a **post-setup
  commit** (the "commit-the-base trick", §4A.4) means any tree mutations from
  image setup / `before_repo_set_cmd` are already *in the base* and cannot leak
  into the patch — which is exactly why the code can drop the `:(exclude)`
  denylist (see D2). Diffing against raw `base_commit` (the doc) is simpler and
  matches what the grader applies against, but re-admits all of §4A.4/§4A.5's
  contamination.
- **✅ Decision:** diff against the instance's **`base_commit`** (not a post-setup
  commit). The code passes `base_ref=base_commit` and the rollout entryscript uses
  it. Rationale: it *guarantees* the grading round-trip — the grader does
  `git reset --hard base_commit` then `git apply`, and the dataset's own gold
  patches are diffs vs `base_commit` and apply, so the convention is proven. The
  post-setup-commit base (which would auto-absorb setup noise) is a nicer but
  fiddlier option — deferred as **P1**.

### D2. `:(exclude)` build-noise denylist: keep, drop, or make tunable

- **Doc (§7 step 1):** hardcodes the mini-swe-agent #528 denylist
  (`pyproject.toml`, `setup.cfg`, `setup.py`, `tox.ini`, `*.cfg`, `*.toml`).
- **Code:** `exclude_globs` defaults to **empty** (no denylist); the docstring
  argues it is unnecessary *given* the post-setup base of D1, but keeps the
  parameter for "the rare instance that still needs it."
- **Tension:** the post-setup base removes *setup-time* noise, but the **agent's
  own run** can still trigger a build tool that rewrites `pyproject.toml` /
  lockfiles *after* the base snapshot — that noise would still enter the patch.
  So the denylist may still be wanted as a secondary defense even with D1.
- **✅ Decision:** **no denylist** by default — `exclude_globs` stays empty. There
  is no material impact today (an agent solving a task rarely rewrites build
  config). A per-ecosystem `:(exclude)` denylist (with logging of dropped paths,
  no silent truncation) is deferred as **P1**; the `exclude_globs` param is
  already there to wire it when needed.

### D3. Binary handling (was self-contradictory across §7 / §8 / code)

- **Doc §7 (grading):** "keep single `git apply -v` **and add `strip_binary_hunks`**
  before writing `patch.diff`."
- **Doc §8 (later, 2026-07-15):** grading "deliberately does **not** call
  `strip_binary_hunks`" because "the extractor's `git diff` omits binary by
  default so the patch reaching the grader is already binary-free."
- **Code:** the extractor's `_DIFF_FLAGS` **includes `--binary`**, which *emits*
  an applyable binary patch — the opposite of "omits binary by default." So the
  §8 premise is false as written, and §7 and §8 give opposite instructions.
- **Tension:** with `--binary` at extraction + no strip at grading, a
  binary-containing agent patch is applied verbatim by our `git apply -v` — but
  Scale **strips** binary before applying (§1), so our grade would **diverge from
  Scale's** on exactly those patches. Meanwhile §4B.1 wants faithful binary
  capture *for the trace*.
- **✅ Decision — happy path, text only (no binary):** extraction stages new
  files with `git add -N` and diffs **without `--binary`**, so binary *bytes* are
  never serialized; the rollout runner then calls `strip_binary_hunks` to remove
  the residual bytes-free `Binary files ... differ` marker (which would otherwise
  break `git apply`). Grading applies verbatim — the patch it receives is already
  binary-free (gold patches are binary-free; rollout patches are stripped
  upstream), so it does not call `strip_binary_hunks`. **Empirically verified**
  (2026-07-17): `git add -N` + no-`--binary` and `git add -A --cached` +
  no-`--binary` produce **byte-identical** output — it is *omitting `--binary`*,
  not `-N`, that drops binary; `-N` is just the lighter intent-to-add idiom. The
  stripped text patch applies cleanly against `base_commit`. Faithful binary
  extract+apply (a two-artifact scheme) is deferred as **P1**.

### D4. `--default-prefix` vs the `-c` prefix pins (git-version compat)

- **Doc §7:** uses `git diff ... --default-prefix`.
- **Code:** deliberately **avoids** `--default-prefix` (git ≥ 2.41 only) and pins
  `-c diff.noprefix=false -c diff.mnemonicPrefix=false` instead, for the same
  `a/ b/` prefixes on any git version.
- **✅ Decision:** keep the **`-c diff.noprefix=false -c diff.mnemonicPrefix=false`**
  pins; do **not** use `--default-prefix`. Verified (2026-07-17): the local git
  (2.39.5) does not even support `--default-prefix` (it needs git ≥ 2.41), and
  under our config isolation the `-c` pins are equivalent — both force `a/ b/`
  prefixes. The doc §7 is updated to match. No effect difference; comments in
  `core/patch.py` explain the why.

### D5. Agent-touched **test files** (§5.1) — the potential-gaming risk

- **The risk:** a solver can game the held-out tests by editing them. SWE-Bench
  classic defends by resetting modified test files to base + removing agent-created
  test files, *then* applying the gold `test_patch` (§5.1/§5.2), with a
  **modified-vs-new** distinction that is load-bearing (issue #518).
- **Unknown:** whether SWE-Bench **Pro**'s per-instance `run_script.sh` +
  our ported `build_eval_script` already reset agent-touched test files, or
  whether nothing does. If nothing does, our grade is gameable.
- **Finding (2026-07-17):** our `build_eval_script` (in
  `core/datasets/swebench_pro/grading.py`) already restores the **golden test
  files by path** *after* applying the model patch — the last line of
  `before_repo_set_cmd` is a `git checkout <sha> -- <test files>` that runs post-
  apply, so an agent's edits to the graded test files are overwritten and cannot
  game the held-out tests. This covers the load-bearing case.
- **Residual (P2 / monitor):** only the *golden* test files are restored, not
  arbitrary agent-created/edited test files elsewhere; those don't affect the
  `fail_to_pass`/`pass_to_pass` set unless they *are* the graded tests, so risk is
  low. Revisit with a fuller all-test-file reset only if a real instance shows a
  gap.

### D6. Eval-side apply hardening ladder (opt-in deviation from Scale)

- **Doc §7 (grading):** offers an *optional* fallback ladder
  `git apply -v → git apply --3way → git apply --reject` (we hold `base_commit`,
  so `--3way` is viable), explicitly opt-in + logged, never silent.
- **State:** not built; grading is the single strict `git apply -v` (matches
  Scale).
- **✅ Decision:** grade **strictly like Scale** — single `git apply -v`, no
  ladder, for now. The opt-in `--3way`/`--reject` hardening is a later,
  clearly-labeled experiment (user still weighing whether the extra tolerance is
  worth diverging from Scale's grade). No change to the grading path today.

### D7. Deferred guards — defer, but **log when they would fire**

Three §4 corner cases are deferred "until a real instance needs them"; the risk is
that they fail **silently**. Decision: keep them deferred **but add detection +
logging** so the gold/rollout sweep surfaces any instance that hits them, instead
of silently mis-extracting:

- **Gitignored new source files** (§4A.2) — `git add -A` silently skips them; a
  real new source file that happens to match `.gitignore` vanishes. Detect via a
  `git status --ignored` / `git check-ignore` pass and log; force-add only if it's
  genuinely source.
- **Submodules / gitlinks** (§4C.6) — `--ignore-submodules=all` is the fix; until
  then, log any `Subproject commit` line reaching the patch.
- **Git LFS pointer-vs-content** (§4B.2) — detect `filter=lfs` via `.gitattributes`
  / `git check-attr` and log; decide pull-objects vs exclude per instance.

### D8. Empty-patch guard — wire it, and on which side(s)

- **Code:** `is_effectively_empty` exists and is tested, but is **not wired** into
  extraction or grading (§8 confirms "no empty-patch guard").
- **✅ Decision — rollout-side, with an explicit outcome:** the rollout runner
  computes `is_empty` via `is_effectively_empty(patch)` (after binary stripping),
  and the CLI records an explicit `outcome`: `empty_patch` (agent produced no
  edits — the eval is **skipped**, never graded as a pass) vs
  `unresolved_tests_failed` (a real patch that graded false) vs `resolved`. So an
  unresolved run's *reason* is read from the log, never guessed. An eval-side
  defensive guard (for a directly-supplied empty `--patch-file`) is deferred as
  **P2**.

### Where each decision landed in code (2026-07-17)

- D1/D2/D3/D4 → `core/patch.py` (`build_extraction_script`, `_DIFF_FLAGS`,
  `strip_binary_hunks`) + `rollout/entryscript.py` (diffs vs `base_commit`) +
  `rollout/runner.py` (strips binary → clean `patch.diff`, keeps raw for audit).
- D3/D8 → `rollout/runner.py` (`is_empty`, `binary_stripped`) +
  `rollout/__main__.py` (explicit `outcome`). D6 → `grading.py` unchanged (strict
  `git apply -v`). D5 → `grading.py` already restores golden test files.
- `docs/patch-extraction.md` §7–§8 reconciled to match. **P1 backlog:** post-setup
  base (D1), `:(exclude)` denylist (D2), faithful binary extract+apply (D3),
  deferred silent-failure guards (D7); **P2:** eval-side empty guard (D8), fuller
  test-file reset (D5).

## Open items / contingencies

- **ENV / `test_patch`.** Not needed for flipt/ansible, but Scale's entryscript
  scrapes `ENV` from the dockerfiles and some instances may need `test_patch`
  applied. Add to the adapter (fetch dockerfiles / apply `test_patch`) **only if
  a gold self-test fails** — the sweep will surface these.
- **Scale-harness brittleness to harden as we port** (from the research): `eval()`
  on dataset fields → `ast.literal_eval`; ENV scraped textually; only the last
  line of `before_repo_set_cmd` used; regex parsers are format-sensitive; image
  tag special-casing (element-web / `-vnan`). We already fetch scripts from a
  pinned commit to avoid drift.
