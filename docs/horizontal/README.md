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
| [`sandbox/`](../../src/swe_lab/sandbox/) | The engine: `SandboxManager` + the five lifecycle hooks, the merged lifecycle-bearing `Sandbox` (+ narrow `SandboxFs` view for observers/graders), `SandboxSpec`, `Mounts`/`Resource` (receiver-decides transfer + `fetch`/collect seam, [ADR-0003](../decisions/ADR-0003-remote-sandbox-lifecycle.md)), `RunResult`; backends `DockerHostSandbox` (A-host) and `GitHubJobSandbox` (A-ghjob) via the open `build_sandbox` registry; shared observers (diff-extract) + `patch.py` (extraction contract, [ADR-0001](../decisions/ADR-0001-patch-extraction-and-grading.md)). |
| [`harnesses/`](../../src/swe_lab/harnesses/) | The **harness axis**: `base.py` (the `Harness` ABC) + `claude_code/` — invocation, `convert`/`capture` (stream \| proxy), and the Claude Code runner utilities (`binary` provisioning, `proxy`, `trace`, `errors`). |
| [`datasets/`](../../src/swe_lab/datasets/) | The **dataset axis**: `load_dataset` + a name→record registry, plus per-dataset packages (`swebench_pro/`: the typed record, run setup, and the `unit_test` compile + grader). Adding a dataset = a sibling package. |
| [`evaluation/`](../../src/swe_lab/evaluation/) | The **eval-method axis**: the `verdict` contract (`Verdict`/`Grader`/`UnitTestSpec`) + `methods/` (`unit_test` now). |
| [`conversation/`](../../src/swe_lab/conversation/) | The provider-neutral typed `Conversation` model + the shared conversation observer. |
| [`repo/`](../../src/swe_lab/repo/), [`paths.py`](../../src/swe_lab/paths.py) | `RepoProvider` + `GitCheckoutProvider` (W1's read-only checkout) + repo-root/cache path helpers. |

## Design principle

Each axis is a self-contained plug: **the engine never imports a concrete
harness/dataset/eval-method**, and general code never learns a dataset's
specifics (each dataset compiles its record into the engine's general shapes —
`SandboxSpec` + `UnitTestSpec`, replacing the retired all-in-one `EvalSpec`).

## Cross-cutting work lands here

Shared-code changes that don't belong to a single vertical — e.g. extracting
common code out of a workstream into the engine, or hardening a shared backend —
are **horizontal** work and are planned here (a `spec.md` / `plan.md` / `plans/`
alongside this README).

**The SandboxRun redesign (largely landed):** the execution core is now one
unified sandboxed-task engine + three plug-in axes (harness / dataset /
eval-method), so `rollout` and `eval` are configs of one engine. See
**[spec.md](spec.md)** (approved 2026-07-18), the **[plan](plan.md)** +
per-task designs in **[plans/](plans/)**, and
**[workspace-layout.md](workspace-layout.md)** (the concrete per-run file
inventory every composition is built against).
