# Project Plan — swebench-eval-lab

Living document. It captures the current direction and will be refined as we go.

## Scope

`swebench-eval-lab` is an umbrella for tooling that **enriches, runs, and audits
SWE-bench evaluation data**, organized as independent workstreams over shared
infrastructure in `src/swebench_eval_lab/core/` (dataset loading, per-instance
repo checkout, a headless-agent harness, a Docker execution layer, and the
dataset-agnostic benchmark contracts).

- **Workstream 1 — Related-files annotation** (`tasks/related_files/`) —
  ground-truth "what code must a solver read" per instance. **Shipped**: 121
  instances annotated & QA'd (100 in phase 1 via the reverse proxy, +20 in the
  new default **stream** capture). See
  [`tasks/related_files/README.md`](src/swebench_eval_lab/tasks/related_files/README.md).
- **Workstream 2 — Solve + evaluate pipeline** (`rollout/` + `evaluation/` on
  `core/docker/`) — actually *solve* SWE-bench Pro tasks in Docker and *grade*
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

**Latest (2026-07-10).**

- **W1 (annotation)** — the runner now defaults to **stream capture**
  (`claude --output-format stream-json`, no reverse proxy; `--capture proxy`
  still available), producing a **source-agnostic unified exchange record** with
  operator PII redacted at write time (home/name/email → an "Alan Turing"
  placeholder). The large per-run trace records live **off-repo in a private HF
  dataset repo** (`luolc/swebench-eval-lab-traces`) via `traces.py` push/fetch +
  a git-tracked `traces_manifest.json`; only the small annotation JSON + parquet
  stay in git. Round 6 (20 instances, stream mode) is done & QA'd — **20/20
  valid, stream == proxy quality**. Total shipped: **121 instances / 1086
  snippets**. `GitCheckoutProvider` hardened to self-heal stale worktrees.
- **W2 (solve + eval)** — the **evaluation** subsystem is built and validated:
  gold self-tests **resolve** for flipt (Go) and ansible (Python), both locally
  and on **GitHub Actions** (native amd64, ~1–2.5 min/instance, free private
  minutes, no secrets). Architecture, decisions, and next steps in "Workstream
  2" below. **rollout** (agent sampling) not started (needs a subscription
  token).
- The repo was renamed `swebench-eval-lab` and reorganized into `core/` +
  `tasks/` (+ new `rollout/`/`evaluation/`); git history was scrubbed of a
  leaked OAuth token and operator PII (force-pushed).

The rest of this file is per-workstream detail. **W1 milestones/history**
follow; **W2** has its own section further down.

| Milestone (W1 — annotation) | State | Notes |
| --- | --- | --- |
| 1 — Data-loading foundation | ✅ Done (2026-07-06) | See "Milestone 1" below for what shipped and where the code lives. |
| 2 — Annotation agent runner | ✅ Done (2026-07-06) | `tasks/related_files/` — single-instance runner **and** the 3-sample-then-aggregate pipeline; both prompts finalized (annotation v3, aggregator). |
| 3 — Annotation storage & format | ✅ Done (2026-07-07) | `outputs/related_files/<dataset>/<instance_id>/` with `candidate_1..3` + `aggregate` (each with `.last_exchange.json`). Committed & pushed (the deliverable). |

**Phase 1 complete — 100 sampled instances annotated & QA'd.** The pipeline works
end to end: `tasks/related_files/pipeline.py` runs N (=3) samples in parallel through the
`cc-reverse-proxy` (subscription OAuth), then the aggregator reconciles them;
every artifact is stored under
`outputs/related_files/swebench_pro/intermediate/<instance_id>/`, and the `combine` binary
rolls the aggregates up into `outputs/related_files/swebench_pro/annotations.parquet`. Both
prompts are finalized (see the experiment report). We ran **5 rounds of 20
(pairwise-disjoint random samples)** and hand-QA'd every result: **100/100 valid,
98 ✅ / 2 ⚠️ / 0 severe, 0 invalid across all 101 aggregates on disk**. This is a
deliberate **staged stop** — the method is validated; next phase is scaling to the
full dataset (see Future work). Full breakdown in
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
show what's done. Phase 1 (100 instances) is complete and pushed — nothing is
mid-flight. To extend: sample a new disjoint round excluding all `round*_ids.txt`,
then run the pipeline CLI (guard: skip if its `aggregate.json` already exists).

Run one instance (full pipeline):
`python -m swebench_eval_lab.tasks.related_files <instance_id> [--model sonnet|opus] [--samples 3]`.

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

**Delivered** (under `src/swebench_eval_lab/`):

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

**Delivered** (under `src/swebench_eval_lab/tasks/related_files/`):

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
  swebench_eval_lab.tasks.related_files.combine`) — **one row per instance**
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

**Off-repo** (large, on the private HF dataset repo `luolc/swebench-eval-lab-traces`):
the per-run **`*.last_exchange.json`** trace records — one per candidate/aggregate,
each the run's unified exchange record (final request/response, `complete` flag,
model, etc.). They were originally committed but grew to ~60 MB, so they moved to
HF via `traces.py` (`push` / `fetch`, integrity-checked by sha256 in the
manifest); the raw files are gitignored. Operator PII is redacted from every
record at write time, and secrets never appear (auth header / org id scrubbed).

## Development phasing (respecting Claude Code usage limits)

Claude Code has usage limits, so we deliberately avoid running all 731 instances
early. **Steps 1–2 are complete, and step 2 was extended into a 100-instance
validation (5 rounds × 20); step 3 is the "Phase 2" item under Deferred / future
work above.**

1. **Prompt iteration** ✅ — iterated the annotation sub-agent's prompt on 1–2
   examples until output quality was good (see prompt-variance `REPORT.md`).
2. **First goal** ✅ — annotate 10–20 examples, then extended to **100** with a
   rolling concurrency window and per-instance QA.
3. **Batch inference (last)** — scale to the full dataset. Because Claude Code
   credits refresh periodically, this is expected to run on a recurring schedule
   (e.g. every few hours) rather than all at once. The current Claude Code
   version already supports this via scheduled remote agents / cron routines
   (the `/schedule` and `/loop` capabilities), so no upgrade is required. Design
   details (resume, deduplication of already-done instances, throttling) are
   deferred to this phase.

## Deferred / future work

**Phase 2 — scale to the full dataset (731 instances).** The method is validated
on 100 samples; the next step is to run the remaining instances. What this needs:

- **A batch driver** (currently the loop is hand-run per round via shell). Wants:
  resume (skip instances whose `aggregate.json` exists — the guard exists, just
  wire it into a loop over all ids), a bounded concurrency window (~4 pipelines on
  this box), and progress output. `rich` was removed from deps as unused; re-add
  it when this CLI is built. Could run on a recurring schedule for unattended
  batches.
- **Usage-limit handling at scale.** `UsageLimitError` already stops the run
  (don't retry a quota wall); a long batch should checkpoint and resume when the
  subscription window refreshes rather than aborting.
- **Cost/throughput budgeting.** ~$0.35–0.5 per instance (4 agent calls); 731
  instances ≈ a few hundred dollars and many hours — plan for multi-session runs.

**Quality follow-ups (optional).**

- A human spot-check tool for annotation quality (beyond the `qa_check.py`
  coverage heuristic).
- Revisit the 2 ⚠️ misses' pattern (a single existing file omitted) if it recurs
  at scale — currently 2/100, not worth prompt-overfitting for.

**Later phases.** Docker-based repo provisioning and an editing / test-running
agent mode (the current agent is read-only, which is all the annotation task
needs).

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
SWE-bench Pro tasks** (an agent generates a patch) and **evaluates** them (apply
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

- `core/benchmark.py` — the contract: `EvalSpec` (image ref, workdir,
  base_commit, before_repo_set_cmd, run_script/parser content, test lists,
  grading) + `BenchmarkAdapter` protocol.
- `core/datasets/swebench_pro/` — a **package**: `record.py` (the record) +
  `execution.py` (`SweBenchProAdapter`: jefzda image ref, pinned scaleapi harness
  fetch, `EvalSpec` builder). **All** SWE-bench-Pro run-knowledge lives here;
  adding a dataset = adding a sibling adapter package.
- `core/docker/provider.py` — general `DockerProvider` (pull; run a script in a
  bind-mounted container; `linux/amd64`).
- `evaluation/runner.py` + `__main__.py` — build the entryscript from an
  `EvalSpec`, run it, parse `output.json`, grade → `EvalResult`. CLI: `python -m
  swebench_eval_lab.evaluation <id> --gold` (grade the gold patch as a self-test)
  or `--patch-file`.
- `.github/workflows/eval.yml` — manual gold self-test on a GitHub-hosted runner.

## Decisions (2026-07-10)

- **Execution = GitHub Actions.** Debug on the private repo's free 2000
  min/month. First real container run on GH Actions (native amd64 — no local
  Apple-Silicon emulation; gold eval needs no secrets). Prefer **container jobs**
  (`jobs.<x>.container.image: <instance image>` via matrix) or "ubuntu runner +
  docker run" — both work; the eval CLI reuses either. Public-repo (free,
  unlimited minutes) decision deferred; if needed, a minimal public repo can
  wrap this one.
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

## Next steps

1. **Matrix eval** — one dispatch grading many instances in parallel (256
   matrix-cap → shard across workflows). The path to running all 731.
2. **Gold self-test sweep** — grade every instance's own gold patch; any that
   does *not* resolve flags a broken/skewed instance (this **is** the W3 skew
   tool). Log what's dropped; don't silently truncate.
3. **`rollout`** — the container agent loop (needs a subscription token). Extract
   the generic "run Claude Code headless + stream-json trace + exchange record"
   from `tasks/related_files/agent_run.py` into `core/agent/` so rollout reuses it.

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
