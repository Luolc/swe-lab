# Horizontal — task index

Ordered task index + status for the horizontal component (per the repo's
planning convention: [`spec.md`](../spec.md) = target design,
[`plan.md`](../plan.md) = strategy, `plans/` = one deep design per task,
indexed here). Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8 (break down if larger).

**Single source of truth for status:** this table is the *only* live status for
the horizontal tasks. The `plans/task-*.md` docs are point-in-time **design
records** — a header may predate the landed code and never says "done"; don't
read status from them. A task that shipped with notable deltas gets a dated
`## Result` note at the foot of its plan, not a status flip.

| # | Task | Status |
|---|---|---|
| 01 | [Google-style readability lift](task-01-google-style-readability.md) | ✅ Complete (P3 leftovers deferred to the SandboxRun migration) |
| 02 | [Engine core (`sandbox/`, fake backend)](task-02-engine-core.md) | ✅ Done |
| 03 | [A-host backend (`DockerHostBackend`)](task-03-a-host-backend.md) | ✅ Done (+ amendments landed: `Resource`, `assets`, materialize seam — PR #46) |
| 04 | [`unit_test` eval method + SBP compile](task-04-unit-test-method.md) | ✅ Done |
| 05 | [Eval CLI on the engine + parity](task-05-eval-cli.md) | ✅ Done (CLI + parity workflow; parity run green — flipt + truncated-golden ansible match legacy) |
| — | **CP1 — eval parity** (human gate) | ⬜ |
| 06a | [`Conversation` protocol + output converters](task-06a-conversation-protocol.md) | ✅ Done (PR #37) |
| 06 | [`claude_code` harness (event-stream capture)](task-06-claude-code-harness.md) | ✅ Done (PR #47) |
| 07 | [Diff-extract observer + rollout CLI](task-07-diff-extract-rollout-cli.md) | ✅ Done (PR #48; live flipt rollout = CP2, manual) |
| — | **CP2 — rollout regression bar** (human gate) | ⬜ |
| 08 | [Proxy capture mode](task-08-proxy-capture.md) | ✅ Done (converter + harness/backend seam; live path = manual) |
| 09 | [A-ghjob backend](task-09-a-ghjob-backend.md) | ✅ Done (`GitHubJobBackend` + `--backend` seam; container-job workflow = manual) |
| 10a | [Moves: `datasets/`, `paths`, `repo/` → top level](task-10a-moves.md) | ✅ Done (pure rename; 190 green, W1 CLI runs) |
| 10b | [Cutover + deletion (old packages, `core/`, workflows)](task-10b-cutover.md) | ✅ Done (`core/` gone; verify on engine; stub-seam test; 184 green) |
| 11 | Docs sync | ✅ Done (maps + commands match the post-cutover tree; spec noted Implemented) |
| — | **CP3 — cutover: 731 sweep + stub seam test** (human gate) | ⬜ |
| 12 | [`Store` seam + post-run persist + manifest](task-12-persistence.md) | ✅ Done (FilesystemStore; `--persist`/`--sweep` on rollout+eval; `promote` subcommand. S3/R2 = task 13) |
| — | **CP4 — R2 provisioning** (ask-first) | ⬜ |
| 13 | R2 store + CI wiring | ⬜ |
| 14 | [**Merged lifecycle-bearing `Sandbox` + up-first + transfer seam**](task-14-sandbox-lifecycle-refactor.md) ([ADR-0003](../../decisions/ADR-0003-remote-sandbox-lifecycle.md)) | ✅ Done (merged `Sandbox`/`SandboxBackend`; up-first + collect; `Resource` = data; `Mount.read_only` drops `Assets`; open backend registry) |
| 15 | **Extensibility seam proof + author guide** (no shipped remote backend — [ADR-0003](../../decisions/ADR-0003-remote-sandbox-lifecycle.md)) | ⬜ **P0** |
| 16 | **Multi-rollout run-record layout** (`rollout_id` + `attempt`; split manifest read — [ADR-0004](../../decisions/ADR-0004-multi-rollout-run-record-layout.md)) | ✅ Done (key `<sweep>/<instance>/r<rollout>/a<attempt>`; `runs/` → store root; `run_ts` recorded only; `read_manifests` + targeted `read_manifest`). K-rollouts **sampling** in eval/rollout is the W2 follow-on |
| 17 | [**Flaky-eval retry + `flaky` verdict flag**](task-17-flaky-eval-retry.md) ([ADR-0005](../../decisions/ADR-0005-flaky-eval-retry.md)) | ✅ Done (ADR-0005; `retries` default 1; validated downstream — nearly all flakes recover in one retry, see the plan's Result note) |
| 18 | [Sandbox observability + interface reshape](task-18-observability-and-interface-reshape.md) ([ADR-0007](../../decisions/ADR-0007-task-and-workflow-layer.md) §2/§3/§8) | ✅ Done (PR #152; `sandbox.*` runtime metrics incl. live OOM coverage; `Harness.observers()` + `run(prompt=...)` breaking pair; live-rollout CP pending) |
| 19 | [**`Task` layer + both compositions rewritten on it**](task-19-task-layer.md) (ADR-0007 §§1–5) | ✅ Done (`workflow/` + `ArtifactSchema` seam; `CodingAgentTask` / `UnitTestEvalTask`; wrappers frozen, zero test edits; §2.6 instance-mounts migration deferred to Task 21 — see the plan's Result note) |
| — | **CP5 — Task falsification gate** (human gate: wrappers thin, live byte-equivalence) | ⬜ |
| 20 | [**Task-keyed persistence: records, validation, retry, resume**](task-20-task-persistence.md) (ADR-0007 §§6–7; amends ADR-0004 — the key gains a `<task>` segment). **Direction:** task-level retry is meant to *replace* the in-run eval retry — but the loop stays until the wrappers die (they receive one sandbox, not a factory); a new ADR supersedes ADR-0005 at removal | ✅ Done (`run_task` + `TaskAddress`/`TerminalMarker`; `Task.outputs_valid`/`should_retry` hooks; `Store.put_bytes`/`get_bytes`; CLIs stamp `task=`; no compat — old debug shards discarded) |
| 21 | [**Workflow: declared list, edges from the store**](task-21-workflow.md) (ADR-0007 §§5, 9–10) | ✅ Core done (PR #160: static edge resolution + caller `inputs` producer + per-entry timeout + `resume=False`; attempt errors persisted; two-container live e2e). CLI rewire folded into task 22 |
| 22 | [**Late-bound instances: static workflow definitions, registry, one CLI**](task-22-late-binding-workflows.md) — instance becomes a hook/execute argument; `WorkflowEntry.sandbox: SandboxConfig` replaces factories; `register_workflow`; **in-run eval retry retires** ([ADR-0008](../../decisions/ADR-0008-retry-moves-to-the-task.md) supersedes ADR-0005); wrappers deleted | ✅ Done (late-bound hooks + `InputsBuilder`; sandbox config split + per-attempt synthesis; `apply_patch`/`patch_name` contract; registry + built-in definitions; both CLIs re-plumbed, flags unchanged — see the plan's Result note. **`swe-lab run <workflow>` is *not* here**: the command surface is task 23) |
| 23 | [**The general CLI**](task-23-general-cli.md) — statically registered workflows invoked by name, adjusted per invocation through a generic dotted-path override grammar (`--<entry>.<field-path>=value`) over tasks, harnesses, and sandbox configs alike; `rollout` / `eval` retire into it | ✅ Done (`swe-lab run <workflow> <instance>`; override engine + harness registry; `--input` with the single-unbound shorthand; three exit codes; the proxy recorder became an observer. See the plan's Result note) |
| 24 | [**Portable Claude Code bundle**](task-24-claude-code-portable-bundle.md) — glibc-old-baseline runtime so the agent runs on musl/Alpine, ancient glibc and distroless alike; one complete tarball; **internal-use only — private channels, never publish** | 🔨 Build green (`packaging/claude-code-bundle/`; smoke matrix 21/21 on debian 10/12, ubuntu 20.04/22.04, alpine 3.19, distroless — live-agent checks still SKIP without a token). swe-lab wiring (§9) not started |

| 25 | [**Purge future git history before the agent runs**](task-25-git-history-purge.md) ([ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §3b + its 2026-08-06 amendment; [#191](https://github.com/Luolc/swe-lab/issues/191)) — a rollout-only observer purging in `after_create`: branches, remote refs, **date-filtered** tags (past kept), remotes, reflog, `gc --prune=now`; three assertions, any failure = a recorded failed attempt | ⬜ **P0** — design done and **empirically validated** on 5 real images / 4 languages / incl. Alpine; found 2 defects in the reference implementations (symref abort, GNU-only `date -d`) |
| 26 | [**Result verifier — detect what the environment cannot prevent**](task-26-result-verifier.md) ([ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §3c/§6 as amended) — a pure rule core (replayable) + an observer last in the rollout's `before_destroy`; audits the purge, flags planted auto-load hooks and retrieval traces. **Detection only, never a gate**; the model judge is a later, separate entry | ✅ Done (v1: rule core + replay + observer; 0/731 FP on the primary rule pinned as a test, 5/5 sensitivity, and 10/10 correct on a live Docker matrix of 5 instances × clean/cheat. **Layer 2 model judge deliberately deferred** — see the plan's Result note) |
| 27 | **Fair retry — only what is not the agent's own doing** ([ADR-0011](../../decisions/ADR-0011-fair-retry.md), implementing the [2026-07-29 outcome-states review](../../reviews/2026-07-29-rollout-outcome-states.md) §§5–6) — `AgentOutcome` + `Harness.outcome` replace the completion bit; `Task.retry_on_timeout` (default off), enforced by the runner's `retry_permitted` gate rather than a second hook; the rollout predicate reads `AgentOutcome` + `RunStatus` and never the patch or the grade; `record_extra` puts the outcome on the shard | ✅ Done (a timeout is no longer retried, an API/execution error now is; fairness invariants pinned by named tests) |
| 28 | [**Codex provisioning — there is no bundle to build**](task-28-codex-provisioning.md) — measured: the Codex Linux binary is **static musl** and runs bare on Alpine / debian:10 / distroless, so task-24's loader+glibc apparatus does not apply. Design only: fetch-verify-pin the bare `codex-<target>.tar.gz`, no `bwrap`, `packaging/lib/` as the shared half, and generalize the backend provisioning seam so a backend stops importing one harness by name | 📐 Design done, **not built** (scope + the claude-code wiring divergence deliberately left alone — see §9) |

**P0 — remote sandbox (ADR-0003).** swe-lab ships host + ghjob only; a remote /
internal sandbox is a consuming company's **own** `Sandbox` subclass (import-only).
Task 14 (the enabling refactor: up-first lifecycle, merged lifecycle-bearing
`Sandbox`, `Resource` → extensible data, a receiver-decides transfer seam)
**precedes Task 12** — building the persistence observer on the host-`Path`
assumption would weld it deeper.
Order: **14 → 15 → (12 rebased onto the transfer seam)**.

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

## Task 12: `Store` seam + post-run persist + manifest

Deep design: [`task-12-persistence.md`](task-12-persistence.md).

**Description:** `sandbox/store.py` — `Store` ABC + `FilesystemStore` +
`build_store` open registry; a **post-run `persist` step** (not an observer —
consumes the finished `RunResult` the collect seam produced) writing run-keyed
prefixes with injected timestamps and **one per-run manifest shard**; tier
stamped at launch via entry-point defaults + `--persist`; `promote` against
`FilesystemStore`. Manifest indexes T1 only.
- **Acceptance:** a formal run persists artifacts + a manifest shard; a debug run
  persists nothing; `promote` moves a debug workspace into T1 with a shard;
  `get` re-fetches.
- **Verification:** unit tests over `FilesystemStore` / `FakeStore` (no cloud);
  quality bar.
- **Dependencies:** 14 (the `fetch`/collect seam). **Scope:** M

### Checkpoint CP4 — R2 provisioning *(ask-first: user creates the R2 bucket*
*+ scoped API token before task 13 wires secrets into CI)*

## Task 13: R2 store + CI wiring

**Description:** `S3Store` over the S3 API (boto3 — a runtime dep behind the
ask-first boundary) registered as `build_store("s3", …)` and pointed at **R2**,
CI secrets, `promote` against R2; retention = keep-all per the spec.
- **Acceptance:** a CI rollout run lands its artifacts in R2 under
  `runs/<sweep>/<instance>/<ts>/…` with a manifest entry; laptop fetch works.
- **Verification:** a dispatched run + a local download; quality bar.
- **Dependencies:** 12, CP4. **Scope:** M
