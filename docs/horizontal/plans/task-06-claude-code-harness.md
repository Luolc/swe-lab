# Task 06 — `claude_code` harness (event-stream capture)

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) (§The three axes — harness, §Agent output → one typed
> `Conversation`, §Assets vs. mounts), [task 02](task-02-engine-core.md)
> (observers/mounts/`Sandbox.run`), [task 03](task-03-a-host-backend.md)
> (`DockerHostBackend`, the **assets** field + the **materialize seam** this
> harness needs), [task 06a](task-06a-conversation-protocol.md) (the shared
> `Conversation` model + `ConversationConverter` ABC + `ConversationObserver`
> this harness plugs into). Grounded in the current Claude-Code-specific code
> (`src/swe_lab/core/agent/{binary,trace,errors}.py`, `src/swe_lab/rollout/
> {entryscript,prompt,constants,runner}.py` at `fae1738`). Open items in §8.

---

## 1. Purpose & scope

Build the **harness axis's first plug**: `claude_code`. A harness supplies the
run's **main body** (how the agent is invoked in-container), the **mounts** it
needs (the prompt + the invocation script), the **assets** it needs (read-only
fixed-path files — the pinned binary now, agent config later), and a
**converter** that turns its native output into a `Conversation` (task 06a). It
does *not* own the backend, the dataset, patch extraction, or the (shared)
conversation observer. This task delivers the harness pieces; task 07 assembles
them into `rollout`.

### In scope

- `harnesses/base.py`: the `Harness` **ABC** (the behavior contract, ADR-0002).
- `harnesses/claude_code/`: `ClaudeCodeHarness(Harness)` (mounts + assets + main
  body + converter), and its constants.
- `harnesses/claude_code/convert.py`: `EventStreamConverter(ConversationConverter)`
  — Claude Code `event_stream` → `Conversation` (wraps the existing
  `parse_stream_events` / `build_exchange_from_stream`).
- The **shared** `ConversationObserver` (task 06a) is *wired* here (configured
  with the claude converter), not redefined.
- The in-container agent invocation ported from `entryscript.py:63-77`, rewired
  to `$SANDBOX_WORKSPACE` paths (run data) + the binary's absolute asset path.
- Fast, Docker-free tests (fake backend + a scripted event-stream fixture).

### Out of scope

- **Physically moving** `core/agent/` into `harnesses/claude_code/` — deferred
  to cutover so W1 annotation (which imports `core/agent/`) keeps working; see
  §8 Q1. This task's harness *imports* the current modules.
- Patch extraction, the `rollout` composition, and the CLI (task 07).
- The **proxy** capture mode (task 08) — `trace.py`'s proxy branch is untouched;
  this harness uses `capture=stream` only.
- The agent **error taxonomy** (`errors.py`) — it serves W1's retry loop;
  rollout records failure via the conversation observer's `complete` flag, it
  does not raise and retry (see §5.4).
- **Fine-tuning the `claude` CLI flags** — the invocation uses today's working
  defaults (§4). Verifying the flag set against the pinned binary (`--bare`,
  explicit `--allowedTools`, `--setting-sources`, model handling) is a **later**
  pass, deferred by the owner (§8 Q4); do not diverge from the current flags in
  this task.

## 2. Module layout

```
harnesses/
  __init__.py
  base.py        Harness (ABC): mounts() + assets() + build_body() + conversation_observer()
  claude_code/
    __init__.py
    harness.py     ClaudeCodeHarness(Harness)
    convert.py     EventStreamConverter(ConversationConverter)  (task-06a ABC)
    constants.py   BINARY_AT (asset path), prompt/event-stream/stderr names, HOME, model
```

The conversation observer is the **shared** `ConversationObserver` (task 06a,
`swe_lab/conversation/`); this task does not define a harness-specific observer.

Tests: `tests/test_claude_code_harness.py` (mounts + assets + body script +
converter, all against FakeBackend / fixtures).

## 3. Key types & signatures

```python
# ─── harnesses/base.py ──────────────────────────────────────────────────────
type Assets = dict[str, Path]        # container_path → host_path, read-only

class Harness(ABC):
  """A harness plug: it contributes the pieces a solving run needs.

  A behavior interface (ABC, per ADR-0002): claude_code now, codex/grok_build
  next, all sharing this contract. The engine (SandboxManager) never imports a
  concrete harness — the *composition* (run_rollout, task 07) calls these
  methods and wires the results into a manager + backend. So the ABC lives in
  the harness layer, not the engine core; "engine stays harness-agnostic" and
  "a harness is an ABC" are independent and both hold. Nothing harness-specific
  (event-stream parsing, the observer class) lives on this base — a harness
  contributes data (mounts, assets) + a converter; the observer is shared.
  """

  @abstractmethod
  def mounts(self, workdir: str) -> Mounts: ...
  @abstractmethod
  def assets(self) -> Assets: ...                 # read-only fixed-path files
  @abstractmethod
  def build_body(self, timeout: float) -> Callable[[Sandbox], None]: ...
  @abstractmethod
  def conversation_observer(self) -> ConversationObserver:  # the SHARED observer,
    ...                                            # configured with this harness's converter

# ─── harnesses/claude_code/harness.py ───────────────────────────────────────
@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  """The Claude Code agent as a sandbox-engine harness plug."""

  prompt: str
  model: str = DEFAULT_MODEL
  binary_path: Path | None = None      # default: ensure_claude_binary(...)
  repo_root: Path | None = None

  @override
  def mounts(self, workdir: str) -> Mounts:
    """Stage the prompt + the invocation script into the workspace (run data)."""
    return {
        PROMPT_NAME: InlineMount(content=self.prompt.encode()),
        AGENT_SCRIPT_NAME: InlineMount(
            content=self._invocation_script(workdir).encode(), executable=True
        ),
    }

  @override
  def assets(self) -> Assets:
    """Read-only files placed at fixed container paths (outside the workspace).

    The pinned binary today; a harness may add read-only agent config (e.g. a
    Claude settings JSON) here later — assets are a set, not a single file.
    """
    binary = self.binary_path or ensure_claude_binary(repo_root=self.repo_root)
    return {BINARY_AT: binary}          # BINARY_AT = /opt/claude-code/claude

  @override
  def build_body(self, timeout: float) -> Callable[[Sandbox], None]:
    """Return the main action: run the staged agent.sh by its workspace path."""
    def body(sb: Sandbox) -> None:
      _ = sb.run(AGENT_SCRIPT_NAME, timeout=timeout)  # run a workspace file by name
    return body

  @override
  def conversation_observer(self) -> ConversationObserver:
    """The shared observer, configured with this harness's converter + output."""
    return ConversationObserver(
        converter=EventStreamConverter(), raw_name=EVENT_STREAM_NAME
    )
```

The **shared** `ConversationObserver` (task 06a) reads the harness's native
output (`raw_name`) in `before_destroy`, runs the injected converter, writes
`conversation.json`, and registers `conversation` + the raw output as artifacts.
`build_body` returns a closure so the composition stays
`with manager.sandbox() as sb: body(sb)`.

## 4. The in-container invocation

Ported from `entryscript.py:63-77`, rewired from the old fixed `MOUNT_AT`
constants to `$SANDBOX_WORKSPACE` (the backend's handshake, task 03 §5.5) for run
data; the binary is invoked by its **absolute asset path**. All interpolated
values `shlex.quote`d (as `entryscript.py:55-61`). **Flags are today's working
defaults** — flag tuning is deferred (§8 Q4, §In scope note):

```bash
set -u
export HOME=/tmp/agent-home            # claude's writable HOME (harness-owned)
mkdir -p "$HOME"
export IS_SANDBOX=1                     # so the agent accepts --dangerously-… as root
cd <workdir>                            # spec.workdir, e.g. /app (no git reset — rollout
                                        #   works from the image's checked-out state)
/opt/claude-code/claude \              # the binary ASSET, invoked by absolute path (§5.3)
  -p "$(cat "$SANDBOX_WORKSPACE"/prompt.txt)" \
  --model <model> --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  > "$SANDBOX_WORKSPACE"/event_stream.jsonl \
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

### 5.1 A harness is an `ABC`; the engine still never imports a concrete one
*(Revised 2026-07-22 with the owner — the earlier draft had no base type;
reversed.)* A harness is a **behavior contract with multiple implementations**
(claude_code now, codex/grok_build next) — exactly the ADR-0002 case for an
`abc.ABC` + `@abstractmethod`. The prior "structural bundle, no base class"
framing conflated two independent things: "the engine must not import a concrete
harness" does **not** require the *absence* of a `Harness` type. So `Harness` is
an ABC in the **harness layer** (`harnesses/base.py`); the engine
(`SandboxManager`) still only ever sees `observers` / `mounts` / a `body`
callable and never imports it. The *composition* (`run_rollout`, task 07) is the
only thing that knows `Harness` — it calls `mounts()`/`assets()`/`build_body()`/
`conversation_observer()` and wires them into a manager + backend. Nothing
harness-specific (the event-stream shape, an observer subclass) lives on the base
— a harness contributes data + a converter; the observer is shared (§5.5).

### 5.2 Reuse `core/agent/` by import; defer the physical move
`ensure_claude_binary`, `parse_stream_events`, `build_exchange_from_stream`,
`last_stream_record` are used as-is (`binary.py:85`, `trace.py:46,100,275`) —
wrapped by the claude converter (task 06a) into a typed `Conversation`. W1
annotation still imports `core/agent/` (`pipelines/related_files/agent_run.py`),
so **moving** those files now would break W1 — out of scope (the spec keeps W1
unmigrated). The Claude-specific code relocates into `harnesses/claude_code/` at
cutover (10b), where W1's import is repointed or a thin shim is left. This task
adds the harness *around* the existing functions. (§8 Q1 confirms the
reuse-not-move call.)

### 5.3 The binary is a read-only **asset** at `/opt`, via `assets()`
The pinned binary is **read-only infrastructure the run must never mutate** —
that is what makes it an asset (its ~100 MB size is beside the point). It lives
at a fixed container path **outside** the read/write workspace
(`BINARY_AT = /opt/claude-code/claude`) and is invoked by **absolute path** —
not via `PATH` (no image guarantees a given `bin` dir on `PATH`; a Docker bind
mount auto-creates the target's parent, so a dedicated `/opt/claude-code/` we
control is robust). `assets()` returns a **dict** (`container_path → host_path`),
not a single file: assets are a *category* of read-only things — the binary now,
a Claude settings JSON or other read-only config later — so the interface does
not hard-code "just the binary". The backend realizes each as a construction-time
property (like `network`/`env`): A-host `-v host:container:ro`, A-ghjob a `cp`
kept read-only (task 03 assets field). The composition (task 07) wires
`harness.assets()` into the backend. See
[`workspace-layout.md`](../workspace-layout.md).

### 5.4 Rollout records failure; it does not classify-and-retry
W1's `errors.py` taxonomy (`classify_error_text`, `UsageLimitError`,
`RetryableError`) drives its retry loop. Rollout's model is different: run the
agent once, `|| true`, capture whatever resulted; a failed/partial run shows up
as `complete == False` (`trace._stream_complete` — `subtype=="success" and not
is_error`, `trace.py:88-97`) and/or an empty patch (task 07). No raising, no
retry here — so this task pulls in none of `errors.py`. (A resample tier, if
ever wanted, is a composition-level concern, not the harness's.)

### 5.5 Event-stream capture via a shared observer + a claude converter
The conversation observer is **shared and harness-agnostic** (`ConversationObserver`,
task 06a): given a converter + the native output filename, it produces
`conversation.json`. Only the *converter* is Claude-specific —
`EventStreamConverter` walks `event_stream` (`--output-format stream-json`,
`last_stream_record` / `parse_stream_events`, `trace.py:100-105`) into the typed
model. `trace.py`'s proxy branch (`last_proxy_record`, `trace.py:108-125`) is the
faithful-wire strategy wired in task 08 (a second converter behind the same ABC);
the harness will gain a `capture` selector then. Stream needs no proxy process
and is what rollout uses today (`DEFAULT_CAPTURE`, `trace.py:40`).

## 6. Tests (all Docker-free)

- **Mounts + assets:** `mounts()` returns the prompt (`content` = prompt bytes)
  and `agent.sh` at the right target names; `assets()` returns
  `{BINARY_AT: <host binary>}`, the host path defaulting through a monkeypatched
  `ensure_claude_binary`.
- **Invocation script:** the built body script sets `HOME`/`IS_SANDBOX`, `cd`s
  the workdir, invokes `/opt/claude-code/claude` (absolute) with `--model`,
  `--output-format stream-json`, `--verbose`, `--dangerously-skip-permissions`,
  redirects to `event_stream.jsonl`/`agent.stderr`, ends with `|| true`; values
  are `shlex.quote`d (inject a workdir with a space/quote).
- **Converter:** against a checked-in `event_stream.jsonl` fixture (a few
  stream-json lines incl. a terminal `result` with `subtype:"success"`),
  `EventStreamConverter.to_conversation` yields a typed `Conversation` (role-tagged
  messages, tool-use blocks paired to tool-results); an empty/absent file →
  `Conversation(messages=[])`. (The shared observer's file-plumbing + `complete`
  flag are tested with the observer in task 06a / via FakeBackend.)
- **Body runs via run:** with FakeBackend, `build_body(timeout)(sb)` calls
  `sb.run(AGENT_SCRIPT_NAME, …)` once (assert recorded).

## 7. Dependencies

Tasks 02, 03 (the **assets** field + the **materialize seam**), **06a** (the
`Conversation` model + `ConversationConverter` ABC + shared `ConversationObserver`),
and, at compose time, 04 via task 07. Reuses `core/agent/` functions — no new
runtime deps beyond 06a's Pydantic. New code Google-docstring'd.

## 8. Open questions (need user confirmation)

1. **Reuse-not-move (§5.2)** — OK to have `harnesses/claude_code/` *import*
   `core/agent/{binary,trace}` now and defer the physical relocation +
   W1-import repoint to 10b? (Alternative: move now and repoint W1 in this
   task — bigger blast radius, breaks the strangler's "old path intact".)
2. ~~Binary copy vs asset~~ — **resolved 2026-07-22**: the binary is a read-only
   **asset** at `/opt/claude-code/claude`, returned by `assets()` (a dict, so it
   generalizes to agent config later) and realized by the backend's assets field
   (task 03). It is **not** a workspace file.
3. ~~HOME path~~ — **resolved 2026-07-21**: `AGENT_HOME = /tmp/agent-home`,
   owned by this harness, in-container ephemeral, not a workspace file.
4. **CLI flags** — deferred by the owner (2026-07-22): use today's working
   defaults (`--dangerously-skip-permissions`, `stream-json`, `--verbose`,
   `--model <alias>`). A later pass verifies the flag set against the pinned
   binary's `claude --help` (candidates the owner flagged: `--bare` to strip
   auto-discovered skills/agents/plugins/MCP/hooks for a reproducible headless
   run, explicit `--allowedTools`, `--setting-sources` to ignore host config).
   Do not change flags in this task.
