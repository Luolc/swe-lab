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
| `$HOME` | the image's own, else the passwd entry, else `/tmp/agent-home` | **harness** (shared) | resolved in three tiers by `run_claude_code.sh`, never hijacked (#240/#245): task images pre-warm toolchain caches under the user's home, and an unconditional `HOME` hands an offline run a cold cache |
| `$CLAUDE_CONFIG_DIR` | `/agent-home/.claude` | **harness** (claude_code) | the agent's **config** root — pinned per run so the image cannot inject instructions through `~/.claude.json` (the ADR-0010 door); ephemeral, **not** a workspace file |

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

## Read-only infrastructure placed *outside* the workspace

Read-only infrastructure the run must never mutate — that immutability (not
its size) is what makes it one — lives at a fixed container path **outside**
the read/write workspace. There is **no `Assets` type**: per
[ADR-0003](../decisions/ADR-0003-remote-sandbox-lifecycle.md) (task 14) such a
thing is just a **read-only `Mount`**
(`Mount(resource, executable=…, read_only=True)`) at an absolute target,
contributed through the same `mounts` seam and staged by the sandbox after
`up()`. The pinned agent binary is the one we have. ("Asset" survives below
as the informal word for it — it names no type.)

| Read-only mount | Container path | Host source | Realized by |
|---|---|---|---|
| Claude Code binary | `/opt/claude-code/claude` | `.cache/bin/claude-code/<version>/linux-x64/claude` | A-host: `docker cp` in, then `chmod 0555` · A-ghjob: `cp` into place at mode `0555` (executable + read-only) |

Scripts invoke it by its **absolute path** (`/opt/claude-code/claude`), *not* via
`PATH` — no image guarantees a given `bin` dir on `PATH`, and a Docker bind mount
auto-creates the target's parent dirs, so a dedicated path we control is robust.
Keeping the binary out of the busy workspace is deliberate: the workspace stays
pure run data (a persisted workspace, task 12, isn't polluted) and nothing can
scribble on the binary.

**Mounts** (the workspace files below) wrap the same **`Resource`**
(the shared content-source) and are transferred by the sandbox's **`mount` /
`_mount_one`** step that dispatches on the Resource kind (`Inline` → write,
`LocalFile` → copy today; `Url` / object-store fetched natively later), never a
hardcoded copy — the receiver decides the transfer
([ADR-0003](../decisions/ADR-0003-remote-sandbox-lifecycle.md)).

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
| *(per-instance fix files)* | `$SANDBOX_WORKSPACE/<name>` | `fixes/` (mount) | entryscript | **only** for the few instances whose *environment* is broken upstream — e.g. `matrix-wysiwyg-1.4.1.tgz` for `element-web-aec454dd…`. See [`fixes/`](../../src/swe_lab/datasets/swebench_pro/fixes/) |

An instance's fix also splices bash into `entryscript.sh` at one seam — **after**
the golden test checkout, **before** the run script, still under `set -e`.
Earlier is undone by `git reset --hard` or overwritten by the checkout; a fix
that fails aborts the run rather than grading a half-patched tree.

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
| `run_claude_code.sh` | `$SANDBOX_WORKSPACE/run_claude_code.sh` | harness (mount) | the main body | the agent invocation: resolve `$HOME` in three tiers (image → passwd → `/tmp/agent-home`) · `export CLAUDE_CONFIG_DIR=/agent-home/.claude` · `export IS_SANDBOX=1` · `. agent_env.sh` (caller-injected env) · `cd $WORKDIR` · `/opt/claude-code/claude -p --model … --output-format stream-json --verbose --dangerously-skip-permissions --replay-user-messages --input-format stream-json < prompt.stream.json > claude.event_stream.jsonl 2> claude.stderr.log` (the prompt is piped in on **stdin**, not inlined; the agent's own exit status is propagated rather than swallowed) |
| `prompt.txt` | `$SANDBOX_WORKSPACE/prompt.txt` | **harness** (written in `run`) | a later reader, not the agent | the task prompt — content is **dataset-derived** (`SweBenchProInstance.prompt`), handed to `Harness.run(prompt=...)` as text; the *filename* is this harness's own choice (ADR-0007 §8). Kept on every path as the readable record of what was asked |
| `prompt.stream.json` | `$SANDBOX_WORKSPACE/prompt.stream.json` | **harness** (written in `run`) | the agent (via run_claude_code.sh) | the same prompt as the one `stream-json` user event the run opens with. Under `--input-format stream-json` the prompt cannot be a plain file — every message on that stdin is a JSON line — and `--replay-user-messages` is only accepted alongside it ([ADR-0017](../decisions/ADR-0017-what-a-capture-is-evidence-of.md)) |

### Produced during the run (in-container, by `run_claude_code.sh`)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `claude.event_stream.jsonl` | `$SANDBOX_WORKSPACE/claude.event_stream.jsonl` | agent stdout redirect | conversation observer (host) | Claude Code's native `stream-json` output (the primary; kept verbatim as the `claude_code.event_stream.jsonl` artifact) |
| `claude.stderr.log` | `$SANDBOX_WORKSPACE/claude.stderr.log` | agent stderr redirect | harness-outcome observer (host) | the run's stderr log — registered as the `claude_code.stderr.log` artifact (a native byproduct, kept for debugging failed runs) |

The conversation observer (`before_destroy`, host-side) converts the native
`claude.event_stream.jsonl` into the canonical typed `Conversation` (task 06a).
It already holds those bytes, so it contributes them **inline** — the manager
writes `conversation.json` straight to the host output dir, with no round trip
back through the sandbox. The raw trace itself is registered by the
*harness-outcome* observer, which also collects `claude.stderr.log` and the
agent's completion signal.

Artifact names carry their format, and a harness's own byproducts are namespaced
by harness: `claude_code.event_stream.jsonl` / `claude_code.stderr.log` are
Claude-Code-specific, while the canonical `conversation.json` is shared across
harnesses and stays unprefixed.

**The artifact name is also the host filename**: the collect step lands each
artifact at `<output dir>/<artifact name>`, so the in-container
`claude.stderr.log` lands as `claude_code.stderr.log`, and the same name keys
the manifest and the store. The in-container filename is only where the fetch
reads *from* — it is an in-sandbox path (possibly absolute) and two observers
may share one (`stderr.log` from a harness and from an eval), so it cannot name
the output. The name has neither problem: it is unique by construction (the
merge refuses a duplicate) and vetted as a plain filename.

### Produced after the run (diff-extract observer, `before_destroy`)

| File | In-container path | Written by | Read by | Content |
|---|---|---|---|---|
| `extract.sh` | `$SANDBOX_WORKSPACE/extract.sh` | diff-extract observer | itself (run in-container) | the ADR-0001 extraction script: `git -C $WORKDIR add -N` + `git diff` vs `base_commit` (no `--binary`) → `patch.raw.diff` |
| `patch.raw.diff` | `$SANDBOX_WORKSPACE/patch.raw.diff` | `extract.sh` (in-container) | the observer (host) | raw `git diff` vs `base_commit` |
| `patch.diff` | *(host output dir only)* | the observer (host) | the grader, if `--grade` | cleaned patch (binary hunks stripped) — contributed **inline**, so it is never written back into the sandbox |

For `--grade`, the CLI feeds `patch.diff` into a **separate eval run** (its own
`.cache/eval_workspaces/<id>/` above) — grading reuses the eval composition.

---

## What gets persisted (task 12)

The persist observer pushes only the artifacts a composition **registers**
(not the whole dir — the binary is an asset, never in the workspace, so it is
never a candidate):

- eval: (verdict — persistence shape TBD in task 12).
- rollout: `conversation` + `event_stream` + `stderr` (conversation
  observer — every native byproduct), `patch` +
  `patch.raw.diff` (diff-extract observer).

The staged inputs (`entryscript.sh` / `run_claude_code.sh` / `run_script.sh` /
`parser.py` / `required_tests.json` / `prompt.txt`) remain in the workspace and
make it self-describing — a persisted workspace records *what ran*, *what was
expected*, and *what resulted*, re-gradable without the dataset record.

## Notes

- **Non-empty-workspace guard.** The sandbox's `up()` refuses a non-empty
  workspace unless `reuse=True`; mounts are transferred into a fresh dir.
- **Filenames are constants**, owned by their axis: the SWE-Bench-Pro names
  (`run_script.sh`, `parser.py`, `output.json`, `required_tests.json`,
  `entryscript.sh`, `stdout.log`, `stderr.log`, **and the solve `prompt.txt`**)
  in the dataset adapter; the claude_code names (`run_claude_code.sh`,
  `claude.event_stream.jsonl`, `claude.stderr.log`, `$HOME`, the `/opt/claude-code/claude`
  binary asset path) in the harness; `conversation.json` in the shared
  conversation observer; `extract.sh` / `patch.raw.diff` / `patch.diff` in the
  shared diff-extract observer. `PROMPT_NAME` (`prompt.txt`) is the one
  cross-axis convention — the dataset writes it, the harness reads it.
