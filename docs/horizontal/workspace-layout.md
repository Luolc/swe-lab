# Workspace layout — files, paths, and lifecycle

The concrete realization of the spec's principle *"the shared, inspectable
state between observers is the workspace filesystem."* This is the reference
every composition (eval, rollout) is built against: what is staged before a
run, what the run produces, what post-processing writes, and where each file
lives — host-side and in-container.

## The paths

| Symbol | Value | Depends on | Meaning |
|---|---|---|---|
| workspace (host) | `.cache/eval_workspaces/<instance_id>/` (eval) · `.cache/rollout_workspaces/<instance_id>/` (rollout) | engine + composition | the per-run host dir; gitignored |
| `$SANDBOX_WORKSPACE` | `/workspace` (A-host bind-mount) · the local dir (A-ghjob) | **backend** | the workspace as seen in-container; every script references staged files only through it |
| `$WORKDIR` | `/app` for SWE-Bench Pro | **dataset** (`SandboxSpec.workdir`) | where the repo is checked out in the image; the `git diff` / test target |
| `$HOME` | `/tmp/claude-home` | **harness** (claude_code) | a writable HOME the agent binary needs; in-container `/tmp`, ephemeral, **not** a workspace file |

Two facts that shape everything below:

- **The workspace (`$SANDBOX_WORKSPACE`) and the repo (`$WORKDIR`) are
  different directories.** Files staged for a run live under the workspace;
  the repo under test lives at `$WORKDIR`. `git diff` runs in `$WORKDIR`, so
  nothing staged in the workspace can pollute an extracted patch.
- **Every script the sandbox runs is a persisted file, run by path** — never
  fed on stdin. Scripts known before the run are staged as mounts; scripts
  generated mid-run (extraction) are written to the workspace by the observer
  that generates them. Either way the exact script survives in the workspace
  for audit.

## Assets — infrastructure placed *outside* the workspace

The pinned agent binary is **not** a workspace file (it is ~100 MB, read-only
infrastructure, and must not be copied per run or persisted with run data).
It is a backend **asset mount**: a host file placed at a fixed container path,
read-only.

| Asset | Container path | Host source | Realized by |
|---|---|---|---|
| Claude Code binary | `/usr/local/bin/claude` (on `PATH`) | `.cache/bin/claude-code/<version>/linux-x64/claude` | A-host: `-v host:container:ro` · A-ghjob: `cp` into place at setup |

Scripts invoke it as `claude` (it is on `PATH`). Asset mounts are a backend
construction-time property, like `network`/`env`/`pass_env`.

---

## Eval composition (tasks 04 / 05)

Host root: `.cache/eval_workspaces/<instance_id>/` · in-container:
`$SANDBOX_WORKSPACE` (`/workspace`).

### Staged before the run (mounts, materialized into the workspace)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `entryscript.sh` | `$SANDBOX_WORKSPACE/entryscript.sh` | compile (mount) | the main body | the eval script: `cd $WORKDIR` → reset+checkout `base_commit` → `git apply` patch → restore golden tests → run_script → parser |
| `run_script.sh` | `$SANDBOX_WORKSPACE/run_script.sh` | compile (mount) | entryscript | SWE-Bench-Pro test-invocation script |
| `parser.py` | `$SANDBOX_WORKSPACE/parser.py` | compile (mount) | entryscript | SWE-Bench-Pro output → `output.json` parser |
| `required_tests.json` | `$SANDBOX_WORKSPACE/required_tests.json` | compile (mount) | the grader (host) | `sorted(fail_to_pass ∪ pass_to_pass)` — the expectation |
| `patch.diff` | `$SANDBOX_WORKSPACE/patch.diff` | compile (mount) | entryscript (`git apply`) | the candidate patch — **only** when grading a patch (`--gold` stages the gold patch) |

### Produced during the run (in-container, by `entryscript.sh`)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `stdout.log` | `$SANDBOX_WORKSPACE/stdout.log` | run_script redirect | — (audit) | test-run stdout |
| `stderr.log` | `$SANDBOX_WORKSPACE/stderr.log` | run_script redirect | — (audit) | test-run stderr |
| `output.json` | `$SANDBOX_WORKSPACE/output.json` | `parser.py` | the grader (host) | structured test results (the PASSED set) |

### Produced after the run (host-side)

The grader (`before_destroy`) reads `output.json` + `required_tests.json` →
`SweBenchProVerdict` (held on the eval-parse observer). **No new file by
default** — whether the verdict is also written to the workspace is a task-12
(persistence) decision.

---

## Rollout composition (tasks 06 / 07)

Host root: `.cache/rollout_workspaces/<instance_id>/` · in-container:
`$SANDBOX_WORKSPACE` (`/workspace`). The binary is an **asset**
(`/usr/local/bin/claude`), not a workspace file.

### Staged before the run (mounts)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `agent.sh` | `$SANDBOX_WORKSPACE/agent.sh` | harness (mount) | the main body | the agent invocation: `export HOME=$HOME` · `mkdir -p $HOME` · `export IS_SANDBOX=1` · `cd $WORKDIR` · `claude -p "$(cat prompt.txt)" --model … --output-format stream-json --verbose --dangerously-skip-permissions > trajectory.jsonl 2> agent.stderr \|\| true` |
| `prompt.txt` | `$SANDBOX_WORKSPACE/prompt.txt` | harness (mount) | the agent | the solve prompt |

### Produced during the run (in-container, by `agent.sh`)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `trajectory.jsonl` | `$SANDBOX_WORKSPACE/trajectory.jsonl` | agent stdout redirect | trace observer (host) | stream-json event trace |
| `agent.stderr` | `$SANDBOX_WORKSPACE/agent.stderr` | agent stderr redirect | — (audit) | agent stderr |

### Produced after the run (diff-extract observer, `before_destroy`)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `extract.sh` | `$SANDBOX_WORKSPACE/extract.sh` | diff-extract observer | itself (run in-container) | the ADR-0001 extraction script: `git -C $WORKDIR add -N` + `git diff` vs `base_commit` (no `--binary`) → `patch.raw.diff` |
| `patch.raw.diff` | `$SANDBOX_WORKSPACE/patch.raw.diff` | `extract.sh` (in-container) | the observer (host) | raw `git diff` vs `base_commit` |
| `patch.diff` | `$SANDBOX_WORKSPACE/patch.diff` | the observer (host) | the grader, if `--grade` | cleaned patch (binary hunks stripped) |

For `--grade`, the CLI feeds `patch.diff` into a **separate eval run** (its own
`.cache/eval_workspaces/<id>/` above) — grading reuses the eval composition.

---

## What gets persisted (task 12)

The persist observer pushes only the artifacts a composition **registers**
(not the whole dir — the binary is an asset, never in the workspace, so it is
never a candidate):

- eval: (verdict — persistence shape TBD in task 12).
- rollout: `trajectory` (trace observer), `patch` + `patch_raw` (diff-extract
  observer).

The staged inputs (`entryscript.sh` / `agent.sh` / `run_script.sh` /
`parser.py` / `required_tests.json` / `prompt.txt`) remain in the workspace and
make it self-describing — a persisted workspace records *what ran*, *what was
expected*, and *what resulted*, re-gradable without the dataset record.

## Notes

- **Non-empty-workspace guard.** The manager refuses a non-empty workspace
  unless `reuse=True`; mounts materialize into a fresh dir.
- **Filenames are constants**, owned by their axis: the SWE-Bench-Pro names
  (`run_script.sh`, `parser.py`, `output.json`, `required_tests.json`,
  `entryscript.sh`, `stdout.log`, `stderr.log`) in the dataset adapter; the
  claude_code names (`agent.sh`, `prompt.txt`, `trajectory.jsonl`,
  `agent.stderr`, `$HOME`, the binary asset path) in the harness;
  `extract.sh` / `patch.raw.diff` / `patch.diff` in the shared diff-extract
  observer.
