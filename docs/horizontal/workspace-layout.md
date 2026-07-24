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
| `$HOME` | `/tmp/agent-home` | **harness** (claude_code) | a writable HOME the agent binary needs; set by `export HOME=…` inside `agent.sh` (not a Docker/backend env); in-container `/tmp`, ephemeral, **not** a workspace file |

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

## Assets — read-only infrastructure placed *outside* the workspace

An **asset** is read-only infrastructure the run must never mutate — that
immutability (not its size) is what makes it an asset. It lives at a fixed
container path **outside** the read/write workspace, and is realized by the
backend as a construction-time property (like `network`/`env`). The pinned agent
binary is an asset.

| Asset | Container path | Host source | Realized by |
|---|---|---|---|
| Claude Code binary | `/opt/claude-code/claude` | `.cache/bin/claude-code/<version>/linux-x64/claude` | A-host: `-v host:container:ro` · A-ghjob: `cp` into place (read-only) |

Scripts invoke it by its **absolute path** (`/opt/claude-code/claude`), *not* via
`PATH` — no image guarantees a given `bin` dir on `PATH`, and a Docker bind mount
auto-creates the target's parent dirs, so a dedicated path we control is robust.
Keeping the binary out of the busy workspace is deliberate: the workspace stays
pure run data (a persisted workspace, task 12, isn't polluted) and nothing can
scribble on the binary.

**Mounts** (the workspace files below) wrap the same **`Resource`** as assets
(the shared content-source) and are materialized by a **per-backend `materialize`
seam** that dispatches on the Resource kind (`Inline` → write, `LocalFile` → copy
today; `Url` / object-store fetched natively later), never a hardcoded copy — see
spec §Materialization is a per-backend seam.

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
`$SANDBOX_WORKSPACE` (`/workspace`). The binary is a read-only **asset** at
`/opt/claude-code/claude` (above), *not* a workspace file.

### Staged before the run (mounts)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `agent.sh` | `$SANDBOX_WORKSPACE/agent.sh` | harness (mount) | the main body | the agent invocation: `export HOME=/tmp/agent-home` · `mkdir -p $HOME` · `export IS_SANDBOX=1` · `cd $WORKDIR` · `/opt/claude-code/claude -p "$(cat prompt.txt)" --model … --output-format stream-json --verbose --dangerously-skip-permissions > event_stream.jsonl 2> agent.stderr \|\| true` |
| `prompt.txt` | `$SANDBOX_WORKSPACE/prompt.txt` | **dataset/composition** (mount) | the agent (via agent.sh) | the solve prompt — **dataset-derived** (`build_solve_prompt`), *not* a harness mount |

### Produced during the run (in-container, by `agent.sh`)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `event_stream.jsonl` | `$SANDBOX_WORKSPACE/event_stream.jsonl` | agent stdout redirect | conversation observer (host) | Claude Code's native `stream-json` output (the primary; kept verbatim as the `event_stream` artifact) |
| `agent.stderr` | `$SANDBOX_WORKSPACE/agent.stderr` | agent stderr redirect | conversation observer (host) | the run's stderr log — registered as the `agent_stderr` artifact (a native byproduct, kept for debugging failed runs) |

The conversation observer (`before_destroy`, host-side) converts the native
`event_stream.jsonl` into the canonical typed `Conversation` (task 06a) and
writes `conversation.json` alongside it; both are registered artifacts.
(`event_stream` is Claude-Code-specific; the canonical `conversation` is shared.)

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
- rollout: `conversation` + `event_stream` + `agent_stderr` (conversation
  observer — every native byproduct), `patch` +
  `patch_raw` (diff-extract observer).

The staged inputs (`entryscript.sh` / `agent.sh` / `run_script.sh` /
`parser.py` / `required_tests.json` / `prompt.txt`) remain in the workspace and
make it self-describing — a persisted workspace records *what ran*, *what was
expected*, and *what resulted*, re-gradable without the dataset record.

## Notes

- **Non-empty-workspace guard.** The manager refuses a non-empty workspace
  unless `reuse=True`; mounts materialize into a fresh dir.
- **Filenames are constants**, owned by their axis: the SWE-Bench-Pro names
  (`run_script.sh`, `parser.py`, `output.json`, `required_tests.json`,
  `entryscript.sh`, `stdout.log`, `stderr.log`, **and the solve `prompt.txt`**)
  in the dataset adapter; the claude_code names (`agent.sh`,
  `event_stream.jsonl`, `agent.stderr`, `$HOME`, the `/opt/claude-code/claude`
  binary asset path) in the harness; `conversation.json` in the shared
  conversation observer; `extract.sh` / `patch.raw.diff` / `patch.diff` in the
  shared diff-extract observer. `PROMPT_NAME` (`prompt.txt`) is the one
  cross-axis convention — the dataset writes it, the harness reads it.
