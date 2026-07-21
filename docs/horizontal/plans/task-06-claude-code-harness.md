# Task 06 — `claude_code` harness (stream capture)

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) (§The three axes — harness), [task 02](task-02-engine-core.md)
> (observers/mounts/`Sandbox.exec`), [task 03](task-03-a-host-backend.md)
> (`DockerHostBackend`). Grounded in the current Claude-Code-specific code
> (`src/swe_lab/core/agent/{binary,trace,errors}.py`, `src/swe_lab/rollout/
> {entryscript,prompt,constants,runner}.py` at `fae1738`). Open items in §8.

---

## 1. Purpose & scope

Build the **harness axis's first plug**: `claude_code`. A harness supplies the
run's **main body** (how the agent is invoked in-container) plus the **mounts**
it needs (the pinned binary, the prompt) plus a **trace observer** (stream-json
→ exchange record). It does *not* own the backend, the dataset, or patch
extraction. This task delivers the harness pieces; task 07 assembles them into
`rollout`.

### In scope

- `harnesses/claude_code/`: `ClaudeCodeHarness` (mounts + main body), a
  `StreamTraceObserver`, and its constants.
- Reuse of the existing Claude-Code machinery **by import** (strangler):
  `ensure_claude_binary` (`binary.py`), `parse_stream_events` /
  `build_exchange_from_stream` / `last_stream_record` (`trace.py`).
- The in-container agent invocation ported from `entryscript.py:63-77`, rewired
  to `$SANDBOX_WORKSPACE` paths and run via `sb.exec`.
- Fast, Docker-free tests (fake backend + a scripted trajectory fixture).

### Out of scope

- **Physically moving** `core/agent/` into `harnesses/claude_code/` — deferred
  to cutover so W1 annotation (which imports `core/agent/`) keeps working; see
  §8 Q1. This task's harness *imports* the current modules.
- Patch extraction, the `rollout` composition, and the CLI (task 07).
- The **proxy** capture mode (task 08) — `trace.py`'s proxy branch is untouched;
  this harness uses `capture=stream` only.
- The agent **error taxonomy** (`errors.py`) — it serves W1's retry loop;
  rollout records failure via the exchange's `complete` flag, it does not raise
  and retry (see §5.4).

## 2. Module layout

```
harnesses/
  __init__.py
  claude_code/
    __init__.py
    harness.py     ClaudeCodeHarness (mounts() + build_body()) 
    trace.py       StreamTraceObserver (stateful: exchange + complete)
    constants.py   in-workspace binary/prompt/trajectory/stderr names, HOME, model
```

Tests: `tests/test_claude_code_harness.py` (mounts + body script + trace
observer, all against FakeBackend / fixtures).

## 3. Key types & signatures

```python
# ─── harnesses/claude_code/constants.py ─────────────────────────────────────
PROMPT_NAME = "prompt.txt"
TRAJECTORY_NAME = "trajectory.jsonl"
AGENT_SCRIPT_NAME = "agent.sh"       # the invocation script (a mount, run by path)
AGENT_STDERR_NAME = "agent.stderr"
AGENT_HOME = "/tmp/agent-home"     # the claude binary's writable HOME (in /tmp,
                                     # ephemeral, NOT a workspace file); harness-owned
BINARY_AT = "/opt/claude-code/claude"  # dedicated asset path, invoked by ABSOLUTE
                                     # path (no PATH reliance) — NOT in the workspace
DEFAULT_MODEL = "sonnet"
# The token is a rollout-composition concern (task 07 sets the backend's
# pass_env); the agent binary reads $CLAUDE_CODE_OAUTH_TOKEN from the env.

# ─── harnesses/claude_code/harness.py ───────────────────────────────────────
@dataclass(frozen=True)
class ClaudeCodeHarness:
  """The Claude Code agent as a sandbox-engine harness plug."""

  prompt: str
  model: str = DEFAULT_MODEL
  binary_path: Path | None = None      # default: ensure_claude_binary(...)
  repo_root: Path | None = None

  def mounts(self, workdir: str) -> Mounts:
    """Stage the prompt + the invocation script (agent.sh) into the workspace.

    The binary is NOT here — it is a backend *asset* at BINARY_AT (see §5.3);
    the rollout composition wires it into the backend.
    """
    return {
        PROMPT_NAME: Mount(content=self.prompt.encode()),
        AGENT_SCRIPT_NAME: Mount(
            content=self._invocation_script(workdir).encode(), executable=True
        ),
    }

  def binary_asset(self) -> tuple[str, Path]:
    """(container_path, host_path) for the backend's read-only asset mount."""
    binary = self.binary_path or ensure_claude_binary(repo_root=self.repo_root)
    return BINARY_AT, binary

  def build_body(self) -> Callable[[Sandbox], None]:
    """Return the main action: run the staged agent.sh by its workspace path."""
    def body(sb: Sandbox) -> None:
      _ = sb.run(AGENT_SCRIPT_NAME, timeout=...)  # run a workspace file by name
    return body

  def trace_observer(self) -> StreamTraceObserver: ...

# ─── harnesses/claude_code/trace.py ─────────────────────────────────────────
@dataclass
class StreamTraceObserver(SandboxObserver):
  """Stateful: reads trajectory.jsonl in before_destroy → exchange record."""

  exchange: dict[str, object] = field(default_factory=dict)
  complete: bool = False

  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    self.exchange = last_stream_record(sb.workspace / TRAJECTORY_NAME)
    self.complete = bool(self.exchange.get("complete", False))
    traj = sb.workspace / TRAJECTORY_NAME
    return Contribution(artifacts={"trajectory": traj} if traj.is_file() else {})
```

The timeout is threaded from the composition (task 07) into `build_body`; the
sketch elides it. `build_body` returns a closure so the composition stays
`with manager.sandbox() as sb: body(sb)`.

## 4. The in-container invocation

Ported from `entryscript.py:63-77`, rewired from the old fixed `MOUNT_AT`
constants to `$SANDBOX_WORKSPACE` (the backend's handshake, task 03 §5.5). All
interpolated values `shlex.quote`d (as `entryscript.py:55-61`):

```bash
set -u
export HOME=/tmp/agent-home            # claude's writable HOME (harness-owned)
mkdir -p "$HOME"
export IS_SANDBOX=1                     # so the agent accepts --dangerously-… as root
cd <workdir>                            # spec.workdir, e.g. /app (no git reset — rollout
                                        #   works from the image's checked-out state)
/opt/claude-code/claude \               # the binary asset, invoked by absolute path (§5.3)
  -p "$(cat "$SANDBOX_WORKSPACE"/prompt.txt)" \
  --model <model> --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  > "$SANDBOX_WORKSPACE"/trajectory.jsonl \
  2> "$SANDBOX_WORKSPACE"/agent.stderr || true
```

`HOME` is set **here**, by this `export` in the harness-generated `agent.sh`
(as `entryscript.py:63-69` does today) — not a Docker `ENV` or a backend env
var, which is exactly why it is a harness-local detail.

This text is staged as `agent.sh` (a mount) and **run by its workspace path**
(`sb.run("agent.sh")`), so the exact invocation persists for audit.

`|| true` preserves the current swallow (`entryscript.py:51-53,76`): a nonzero
agent exit must still leave the workspace edits for extraction. Model is passed
as an alias straight to `--model` (no alias→id mapping today —
`entryscript.py:73`, `runner.py:73`).

## 5. Design decisions

### 5.1 A harness is a bundle, not a subclass
The engine has no `Harness` base type — a harness is *whatever contributes* a
main body + mounts + a trace observer (spec §The three axes). `ClaudeCodeHarness`
exposes `mounts()`, `build_body()`, `trace_observer()`; the rollout composition
(task 07) wires them into a `SandboxManager`. This keeps the engine
harness-agnostic (it never imports `claude_code`) and lets a second harness
(Codex) register by providing the same three pieces — the CP3 stub proves it.

### 5.2 Reuse `core/agent/` by import; defer the physical move
`ensure_claude_binary`, `parse_stream_events`, `build_exchange_from_stream`,
`last_stream_record` are used as-is (`binary.py:85`, `trace.py:46,100,275`).
W1 annotation still imports `core/agent/` (`pipelines/related_files/
agent_run.py`), so **moving** those files now would break W1 — out of scope
(the spec keeps W1 unmigrated). The Claude-specific code relocates into
`harnesses/claude_code/` at cutover (10b), where W1's import is repointed or a
thin shim is left. This task adds the harness *around* the existing functions.
(§8 Q1 confirms the reuse-not-move call.)

### 5.3 The binary is an asset (fixed container path), not a workspace mount
*(Revised 2026-07-21 with the user — the earlier draft copied the binary into
the workspace; reversed.)* The ~100 MB binary is read-only **infrastructure**,
not run data: copying it into every per-run workspace wastes disk/time and
would pollute a persisted workspace (task 12 would push it to T1). So it is a
backend **asset** — a host file at a fixed container path, read-only — realized
by A-host as `-v <binary>:/opt/claude-code/claude:ro` (what the old rollout did,
`runner.py:135`) and by A-ghjob as `mkdir -p /opt/claude-code && cp`. The
harness declares the asset (`BINARY_AT` → `ensure_claude_binary()`); the rollout
composition (task 07) wires it into the backend's `assets`. Scripts invoke it by
its **absolute path** — *not* via `PATH`: not every image guarantees a given
`bin` dir on `PATH`, and a Docker bind mount auto-creates the target's parent
dirs, so a dedicated `/opt/claude-code/` we control is the robust choice. Only
run *data* lives in the workspace; see
[`workspace-layout.md`](../workspace-layout.md).

### 5.4 Rollout records failure; it does not classify-and-retry
W1's `errors.py` taxonomy (`classify_error_text`, `UsageLimitError`,
`RetryableError`) drives its retry loop. Rollout's model is different: run the
agent once, `|| true`, capture whatever resulted; a failed/partial run shows up
as `complete == False` (`trace._stream_complete` — `subtype=="success" and not
is_error`, `trace.py:88-97`) and/or an empty patch (task 07). No raising, no
retry here — so this task pulls in none of `errors.py`. (A resample tier, if
ever wanted, is a composition-level concern, not the harness's.)

### 5.5 Stream capture only; proxy is task 08
`StreamTraceObserver` uses `last_stream_record` (the stream branch,
`trace.py:100-105`). `trace.py`'s proxy branch (`last_proxy_record`,
`trace.py:108-125`) is the faithful-wire capture strategy wired in task 08; the
harness will gain a `capture` selector then. Stream needs no proxy process and
is what rollout uses today (`DEFAULT_CAPTURE`, `trace.py:40`).

## 6. Tests (all Docker-free)

- **Mounts:** `mounts()` returns the binary (`source` set, `executable=True`)
  and the prompt (`content` = prompt bytes) at the right target names; the
  binary path defaults through a monkeypatched `ensure_claude_binary`.
- **Invocation script:** the built body script sets `HOME`/`IS_SANDBOX`,
  `cd`s the workdir, invokes `$SANDBOX_WORKSPACE/claude` with `--model`,
  `--output-format stream-json`, `--verbose`, `--dangerously-skip-permissions`,
  redirects to `trajectory.jsonl`/`agent.stderr`, ends with `|| true`; values
  are `shlex.quote`d (inject a workdir with a space/quote).
- **Trace observer:** against a checked-in `trajectory.jsonl` fixture (a few
  stream-json lines incl. a terminal `result` with `subtype:"success"`),
  `before_destroy` yields the exchange record, `complete == True`, and
  registers the `trajectory` artifact; a fixture ending in `is_error:true` →
  `complete == False`; an absent file → `{}`, `complete == False`, no artifact.
- **Body runs via exec:** with FakeBackend, `build_body(workdir)(sb)` calls
  `sb.exec` once with the invocation script (assert recorded).

## 7. Dependencies

Tasks 02, 03 (and, at compose time, 04 via task 07). Reuses `core/agent/`
functions — no new runtime deps. New code Google-docstring'd.

## 8. Open questions (need user confirmation)

1. **Reuse-not-move (§5.2)** — OK to have `harnesses/claude_code/` *import*
   `core/agent/{binary,trace}` now and defer the physical relocation +
   W1-import repoint to 10b? (Alternative: move now and repoint W1 in this
   task — bigger blast radius, breaks the strangler's "old path intact".)
2. ~~Binary copy vs bind-mount~~ — **resolved 2026-07-21**: the binary is a
   backend **asset** at `/opt/claude-code/claude` (read-only, invoked by
   absolute path — no PATH reliance), not a workspace copy (§5.3). Requires the
   task-03 asset-mount amendment.
3. ~~HOME path~~ — **resolved 2026-07-21**: `AGENT_HOME = /tmp/agent-home`,
   owned by this harness (a claude-specific detail, not an engine/shared
   constant), in-container ephemeral, not a workspace file. A different harness
   sets its own or none.
