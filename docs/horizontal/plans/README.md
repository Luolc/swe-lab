# Horizontal — task index

Ordered task index + status for the horizontal component (per the repo's
planning convention: [`spec.md`](../spec.md) = target design,
[`plan.md`](../plan.md) = strategy, `plans/` = one deep design per task,
indexed here). Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8 (break down if larger).

| # | Task | Status |
|---|---|---|
| 01 | [Google-style readability lift](task-01-google-style-readability.md) | ✅ Complete (P3 leftovers deferred to the SandboxRun migration) |
| 02 | [Engine core (`sandbox/`, fake backend)](task-02-engine-core.md) | ✅ Done |
| 03 | [A-host backend (`DockerHostBackend`)](task-03-a-host-backend.md) | ✅ Done |
| 04 | [`unit_test` eval method + SBP compile](task-04-unit-test-method.md) | ✅ Done |
| 05 | [Eval CLI on the engine + parity](task-05-eval-cli.md) | ✅ Done (CLI + parity workflow; parity run green — flipt + truncated-golden ansible match legacy) |
| — | **CP1 — eval parity** (human gate) | ⬜ |
| 06a | [`Conversation` protocol + output converters](task-06a-conversation-protocol.md) | ✅ Done (PR #37) |
| 06 | [`claude_code` harness (event-stream capture)](task-06-claude-code-harness.md) | 📝 Designed |
| 07 | [Diff-extract observer + rollout CLI](task-07-diff-extract-rollout-cli.md) | 📝 Designed |
| — | **CP2 — rollout regression bar** (human gate) | ⬜ |
| 08 | Proxy capture mode | ⬜ |
| 09 | A-ghjob backend | ⬜ |
| 10a | Moves: `datasets/`, `paths`, `repo/` → top level | ⬜ |
| 10b | Cutover + deletion (old packages, `core/`, workflows) | ⬜ |
| 11 | Docs sync | ⬜ |
| — | **CP3 — cutover: 731 sweep + stub seam test** (human gate) | ⬜ |
| 12 | `Store` seam + tier mechanics | ⬜ |
| — | **CP4 — R2 provisioning** (ask-first) | ⬜ |
| 13 | R2 store + CI wiring | ⬜ |

Write a `task-NN-*.md` deep design before starting any task marked non-trivial
(02, 03, 04, 06 at minimum — engine interface details, docker lifecycle, the
`Grader` compile path, and the harness invocation deserve source-grounded
designs).

---

## Task 02: Engine core — `sandbox/` package, fake backend

**Description:** The dataset-/harness-/eval-method-agnostic engine per the
spec: `Sandbox` (pure handle), `SandboxSpec`, `Mount`/`Mounts` + merge-and-
materialize (duplicate target = error), `RunResult`/`RunStatus`,
`SandboxObserver` (five hooks) + `CompositeObserver`, `SandboxManager`
(yield-the-sandbox, always-post-process, `on_error` routing), `SandboxBackend`
protocol + a fake backend for tests.
- **Acceptance:** manager lifecycle unit-tested against the fake backend,
  including the failure matrix (create fails / body raises / `before_destroy`
  raises) — destroy runs on every path; hook contributions aggregate into
  `RunResult`; mounts materialize with `executable` honored and duplicate
  targets rejected loudly.
- **Verification:** new `tests/test_sandbox_*.py` green with zero Docker use;
  full quality bar.
- **Dependencies:** none. **Scope:** M

## Task 03: A-host backend

**Description:** `SandboxBackend` implemented over `docker create/start/exec/rm`
(persistent container, workspace bind-mounted, `linux/amd64`, network toggle,
env pass-through); image pull reused from the existing provider code.
- **Acceptance:** a sandbox comes up on a small public image, `exec` runs
  scripts with timeout + streamed output, teardown always removes the
  container (asserted in a failure-injection test).
- **Verification:** integration smoke test (skippable where Docker is absent);
  no dangling containers after the suite.
- **Dependencies:** 02. **Scope:** M

## Task 04: `unit_test` eval method + SWE-Bench-Pro compile

**Description:** The evaluation axis per the spec: `Verdict` protocol
(`resolved` only), `Grader[V: Verdict]`, `UnitTestSpec[V]`
(`eval_script`/`mounts`/`grader`); the `unit_test` method's main body +
eval-parse observer. The SBP adapter compiles its record into
`SandboxSpec` + `UnitTestSpec` (ports `build_eval_script`; mounts
`run_script.sh`/`parser.py`) and defines `SweBenchProVerdict` with
`output_state: ok | absent | unparseable`.
- **Acceptance:** corrupt-but-present `output.json` yields
  `output_state=unparseable` (distinct from "no tests passed") — the audit
  P0-2 false-GOLDEN_FAIL class is unrepresentable; `build_eval_script` port is
  covered by pure unit tests (flag combinations, last-line
  `before_repo_set_cmd`, `shlex.quote`d test names) — closing audit P0-3.
- **Verification:** `tests/test_unit_test_method.py` + adapter tests, no
  Docker required; full quality bar.
- **Dependencies:** 02 (03 for end-to-end). **Scope:** M

## Task 05: Eval CLI on the engine + parity

**Description:** `swe_lab/__main__.py` (Typer `app()`) + `cli/eval.py` (a typed
`@app.command()`) running eval as an engine composition; a new
`eval-parity.yml` CI job. Old `evaluation/` package stays untouched until 10b.
- **Acceptance:** `python -m swe_lab eval <id> --gold` resolves flipt + ansible
  in CI; old-vs-new verdict parity on 2–3 instances including one
  truncated-golden-names instance.
- **Verification:** CI run links + a parity table in the PR body.
- **Dependencies:** 03, 04. **Scope:** M

### Checkpoint CP1 — eval parity *(human review before the rollout slice)*

## Task 06a: `Conversation` protocol + output converters

**Description:** `swe_lab/conversation/` — one provider-neutral, well-typed
**Pydantic `Conversation`** model (role-tagged messages of `type`-discriminated
content blocks), ported from the sibling `locode-core`'s `locode-protocol` + the
Anthropic SDK `types`, plus the shared, harness-agnostic `ConversationObserver`
(conversion is a `Harness.to_conversation` method, not a separate ABC;
claude_code's `event_stream` → `Conversation` lands with task 06). Named
`conversation`, not `trace` (perf-tracing clash) or `trajectory`
(Claude-specific). Retires the misnamed `last_exchange` dict for new code. Adds
Pydantic (owner-approved runtime dep).
- **Acceptance:** `Conversation` round-trips through `model_dump_json` /
  `model_validate_json`; the `event_stream` fixture converts to the right
  roles/blocks with `tool_use`↔`tool_result` pairing; empty/absent →
  `Conversation(messages=[])`.
- **Verification:** `tests/test_conversation.py` + converter tests, no Docker;
  quality bar. **Backlog (not here):** rename+re-host W1's published
  `.last_exchange.json` on HF (ask-first).
- **Dependencies:** none (consumed by 06/07/08). **Scope:** M

## Task 06: `claude_code` harness (event-stream capture)

**Description:** `harnesses/base.py` (the `Harness` **ABC**, ADR-0002) +
`harnesses/claude_code/` — pinned-binary provisioning (reused from
`core/agent/binary.py` by import), the agent-run main body (in-container
invocation through `sb.run`; the prompt + `agent.sh` as workspace mounts, **the
binary as a read-only asset at `/opt/claude-code/claude`**), `event_stream`
capture as a conversation observer producing a task-06a `Conversation`.
- **Acceptance:** `ClaudeCodeHarness(Harness)` registers as a composition (main
  + observers + mounts + binary asset) without the engine importing it; prompt +
  `agent.sh` land in the workspace, the binary at its `/opt` asset path; the
  `event_stream` converts to a typed `Conversation`; a nonzero agent exit still
  leaves the edits (`|| true`).
- **Verification:** unit tests with a scripted fake agent binary + an
  `event_stream` fixture; quality bar. CLI-flag tuning deferred (uses today's
  defaults).
- **Dependencies:** 02, 03 (asset + materialize seam), 06a. **Scope:** M

## Task 07: Diff-extract observer + rollout CLI

**Description:** Shared diff-extract observer (ADR-0001 contract, ports
`core/patch.py` usage), explicit outcome recording
(`resolved`/`unresolved_tests_failed`/`empty_patch` — grading reuses task 04),
`cli/rollout.py`, `rollout.yml` switched to `python -m swe_lab rollout`.
- **Acceptance:** one instance runs agent → `patch.diff` → graded outcome as a
  single engine composition; `empty_patch` never grades as a pass.
- **Verification:** CI flipt rollout run link with conversation + patch + verdict.
- **Dependencies:** 04, 06. **Scope:** M

### Checkpoint CP2 — rollout regression bar *(human review before the moves)*

## Task 08: Proxy capture mode

**Description:** `ReverseProxy` (from `core/agent/proxy.py`) wired as the
harness's alternative capture strategy per the spec ("proxy is not legacy");
stream stays the default. Does **not** change rollout auth (token-via-proxy
is a separate ADR — see plan Out-of-scope).
- **Acceptance:** capture=proxy produces an exchange record equivalent to the
  stream path on the same run (existing trace tests extended).
- **Verification:** unit tests against the recorded fixtures; quality bar.
- **Dependencies:** 06. **Scope:** S

## Task 09: A-ghjob backend

**Description:** The job-is-the-container backend: `exec` runs in the job
shell, workspace is a local dir; proven by a workflow variant running one
instance's eval (or rollout) as a GH container job.
- **Acceptance:** the same engine composition runs unchanged on both backends
  (backend chosen by config, spec Success #4).
- **Verification:** a green `workflow_dispatch` run using the container-job
  model.
- **Dependencies:** 02 (07 for the rollout variant). **Scope:** M

## Task 10a: Moves — `datasets/`, `paths`, `repo/` to top level

**Description:** Mechanical relocation per the spec's migration mapping:
`core/datasets/` → `datasets/`, `core/paths.py` → `paths.py`,
`core/repo/` → `repo/` (W1 keeps using it as-is); update every import
(`pipelines/`, tests, remaining `core/` users). No behavior change.
- **Acceptance:** zero behavior diff — full suite green, W1 CLI still runs
  (`python -m swe_lab.pipelines.related_files --help`).
- **Verification:** full quality bar; grep shows no `swe_lab.core.datasets` /
  `core.paths` imports left.
- **Dependencies:** CP1 + CP2 passed. **Scope:** M (mechanical)

## Task 10b: Cutover + deletion

**Description:** Port `evaluation/verify.py` to `cli/verify.py` (shard /
aggregate over the engine eval); delete old `rollout/` + `evaluation/`
packages, `core/benchmark.py` (`EvalSpec` retired), and the emptied `core/`;
final dispatcher; `verify-golden.yml` switched to `python -m swe_lab verify`.
Includes the **harness-stub seam test**: a fake harness registers without any
engine change (spec Success #3).
- **Acceptance:** `src/swe_lab/core/` no longer exists; the workflows call the
  new CLI; stub-harness test green.
- **Verification:** full quality bar; `rollout.yml` + `verify-golden.yml` (small
  shard) + `eval-parity.yml` each dispatched green.
- **Dependencies:** 05, 07, 09, 10a. **Scope:** M

## Task 11: Docs sync

**Description:** `docs/conventions.md` directory map, `docs/horizontal/README.md`
package table, W2 README/plan command references; spec status noted
Implemented.
- **Acceptance:** no doc references a deleted path; map matches the tree.
- **Verification:** grep for `core/`, `swe_lab.rollout`, `swe_lab.evaluation`
  across docs.
- **Dependencies:** 10b. **Scope:** S

### Checkpoint CP3 — cutover *(user triggers the full 731 sweep,*
*`max-parallel` ≤15, ~2.2 h; reviews 731/731 + a flipt rollout re-run)*

## Task 12: `Store` seam + tier mechanics

**Description:** `sandbox/store.py` — `Store` protocol + `FilesystemStore`;
`PersistObserver` (T1 manifest append, run-keyed prefixes with injected
timestamps); tier stamped at launch via entry-point defaults + `--persist`;
`promote` subcommand against `FilesystemStore`. Manifest indexes T1 only.
- **Acceptance:** a formal run persists artifacts + manifest entry; a debug run
  persists nothing; `promote` moves a debug workspace into T1 with a manifest
  entry.
- **Verification:** unit tests over `FilesystemStore`; quality bar.
- **Dependencies:** 02 (05/07 wire the flags). **Scope:** M

### Checkpoint CP4 — R2 provisioning *(ask-first: user creates the R2 bucket*
*+ scoped API token before task 13 wires secrets into CI)*

## Task 13: R2 store + CI wiring

**Description:** `R2Store` over the S3 API (boto3 or a thin client — runtime
dep needs the ask-first boundary), CI secrets, `promote` against R2; retention
= keep-all per the spec.
- **Acceptance:** a CI rollout run lands its artifacts in R2 under
  `runs/<sweep>/<instance>/<ts>/…` with a manifest entry; laptop fetch works.
- **Verification:** a dispatched run + a local download; quality bar.
- **Dependencies:** 12, CP4. **Scope:** M
