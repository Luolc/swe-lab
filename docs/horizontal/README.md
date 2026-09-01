# Horizontal — the shared foundation

The **horizontal** layer every [workstream](../workstreams/) builds on: the
shared **SandboxRun engine** + its three plug-in axes. Per the approved
[SandboxRun spec](spec.md), the old `core/` package has **dissolved** into these
top-level packages (cutover complete, tasks 10a/10b). This doc is the design
overview. Operational details (commands, hazards) are in
[`../conventions.md`](../conventions.md).

## What's in it

| Package | Responsibility |
| --- | --- |
| [`sandbox/`](../../src/swe_lab/sandbox/) | The engine: `SandboxManager` + the five lifecycle hooks, the merged lifecycle-bearing `Sandbox` (+ narrow `SandboxFs` view for observers/graders), `SandboxSpec`, `Mounts`/`Resource` (receiver-decides transfer + `fetch`/collect seam, [ADR-0003](../decisions/ADR-0003-remote-sandbox-lifecycle.md)), `RunResult`; backends `DockerHostSandbox` (A-host) and `GitHubJobSandbox` (A-ghjob) via the open `build_sandbox` registry; the shared observers (`diff_extract`, `git_history_purge`, `result_verify`). |
| [`harnesses/`](../../src/swe_lab/harnesses/) | The **harness axis**: `base.py` (the `Harness` ABC) + `registry.py`, then one package per agent — [`claude_code/`](../../src/swe_lab/harnesses/claude_code/) (invocation, `convert`/`capture`/`recorder`, and the runner utilities `binary` provisioning, `proxy`, `errors`), [`codex/`](../../src/swe_lab/harnesses/codex/) and [`grok_build/`](../../src/swe_lab/harnesses/grok_build/). |
| [`datasets/`](../../src/swe_lab/datasets/) | The **dataset axis**: `load_dataset` + a name→record registry, plus one package per dataset (`swebench_pro/`, `deepswe/`: the typed record, run setup, and the `unit_test` compile + grader), and `verify.py`, the dataset-agnostic golden sweep. Adding a dataset = a sibling package. |
| [`evaluation/`](../../src/swe_lab/evaluation/) | The **evaluation axis**: the `verdict` contract (`Verdict`/`Grader`/`UnitTestSpec`) + one module per method (`unit_test` now). |
| [`workflow/`](../../src/swe_lab/workflow/) | The **task layer** ([ADR-0007](../decisions/ADR-0007-task-and-workflow-layer.md)): `Task` (one sandbox, three hooks, instance bound at `execute`), `run_task` (attempts, records, the terminal marker), `Workflow` (declared entries, edges resolved from the store), and the registry of statically-written definitions (`definitions.py`: `rollout`, `unit_test`, `rollout_and_unit_test`, `gold_unit_test`, `git_integrity_audit`). |
| [`cli/`](../../src/swe_lab/cli/) | One entry point, one module per subcommand: `run` (any registered workflow, against any instance) + `promote`. Any field of a workflow is adjusted for one invocation by naming its path (`overrides.py`). |
| [`rollout.py`](../../src/swe_lab/rollout.py) | The **rollout composition** (`CodingAgentTask`, [ADR-0007](../decisions/ADR-0007-task-and-workflow-layer.md)): a harness solves the bound instance under the shared observers, with optional proxy capture. |
| [`git/`](../../src/swe_lab/git/) | The task repo's **git state**: `patch.py` gets the agent's work *out* as a clean diff vs `base_commit` ([ADR-0001](../decisions/ADR-0001-patch-extraction-and-grading.md)); `history.py` keeps the answer *out* and proves it; `audit.py` is the agent-free sweep. Pure script builders — the observers that run them live in `sandbox/observers/`. |
| [`integrity/`](../../src/swe_lab/integrity/) | **Benchmark-integrity detection** ([ADR-0010](../decisions/ADR-0010-benchmark-integrity.md)): `rules.py` is the pure rule core, `replay.py` re-runs it over a stored run. Detection, never a gate. |
| [`conversation/`](../../src/swe_lab/conversation/) | The provider-neutral typed `Conversation` model + the shared conversation observer. |
| [`repo/`](../../src/swe_lab/repo/), [`paths.py`](../../src/swe_lab/paths.py) | `RepoProvider` + `GitCheckoutProvider` (W1's read-only checkout) + repo-root/cache path helpers. |
| [`pipelines/related_files/`](../../src/swe_lab/pipelines/related_files/) | W1's annotation pipeline. Keeps its own module entrypoint; not on the engine. |

## Design principle

Each axis is a self-contained plug: **the engine never imports a concrete
harness/dataset/eval-method**, and general code never learns a dataset's
specifics (each dataset compiles its record into the engine's general shapes —
`SandboxSpec` + `UnitTestSpec`, replacing the retired all-in-one `EvalSpec`).

## Cross-cutting work lands here

Shared-code changes that don't belong to a single vertical — e.g. extracting
common code out of a workstream into the engine, or hardening a shared backend —
are **horizontal** work and are planned here (a `spec.md` + `plans/` alongside
this README; see [`doc-map.md`](../doc-map.md) for the component layout).

**The SandboxRun redesign (largely landed):** the execution core is now one
unified sandboxed-task engine + three plug-in axes (harness / dataset /
eval-method), so `rollout` and `eval` are configs of one engine. See
**[spec.md](spec.md)** (approved 2026-07-18), the per-task designs indexed by
**[plans/README.md](plans/README.md)**, and
**[workspace-layout.md](workspace-layout.md)** (the concrete per-run file
inventory every composition is built against).
