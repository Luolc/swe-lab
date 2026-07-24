# Task 06 — `claude_code` harness (event-stream capture)

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) (§The three axes — harness, §Agent output → one typed
> `Conversation`, §Assets vs. mounts), [task 02](task-02-engine-core.md)
> (observers/mounts/`Sandbox.run`), [task 03](task-03-a-host-backend.md)
> (`DockerHostBackend`, the **assets** field + the **materialize seam** this
> harness needs), [task 06a](task-06a-conversation-protocol.md) (the shared
> `Conversation` model + the shared `ConversationObserver`; conversion is a
> `Harness.to_conversation` method, not a separate ABC) this harness plugs into.
> Grounded in the current Claude-Code-specific code
> (`src/swe_lab/core/agent/{binary,trace,errors}.py`, `src/swe_lab/rollout/
> {entryscript,prompt,constants,runner}.py` at `fae1738`). Open items in §8.

---

## 1. Purpose & scope

Build the **harness axis's first plug**: `claude_code`. A harness supplies the
run's **main body** (how the agent is invoked in-container), the **mounts** it
needs (its invocation script — **not** the prompt, which is dataset-derived; §5.6),
the **assets** it needs (read-only fixed-path files — the pinned binary now,
agent config later), and a **`to_conversation`** that turns its native output
into a `Conversation` (task 06a). It is **dataset-agnostic**: it does *not* own
the backend, the dataset, the prompt, patch extraction, or the (shared)
conversation observer. This task delivers the harness pieces; task 07 assembles
them into `rollout`.

### In scope

- `harnesses/base.py`: the `Harness` **ABC** (the behavior contract, ADR-0002).
- `harnesses/claude_code/`: `ClaudeCodeHarness(Harness)` (mounts + assets + main
  body + to_conversation), and its constants.
- `harnesses/claude_code/convert.py`: `event_stream_to_conversation(raw)` — a
  module function (Claude Code `event_stream` → `Conversation`) that
  `ClaudeCodeHarness.to_conversation` delegates to; **written fresh** (stdlib
  `json` over the stream-json lines), *not* wrapping the soon-deprecated
  `core/agent/trace.py`.
- The **shared** `ConversationObserver` (task 06a) is *wired* here (with the
  harness's `to_conversation` + native output name), not redefined.
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
  rollout records failure via a harness-derived completion signal, it does not
  raise and retry (see §5.4).
- **Fine-tuning the `claude` CLI flags** — the invocation uses today's working
  defaults (§4). Verifying the flag set against the pinned binary (`--bare`,
  explicit `--allowedTools`, `--setting-sources`, model handling) is a **later**
  pass, deferred by the owner (§8 Q4); do not diverge from the current flags in
  this task.

## 2. Module layout

```
harnesses/
  __init__.py
  base.py        Harness(ConversationProducer): mounts() + assets() + run()
                                (+ to_conversation() + native_outputs() from 06a)
  claude_code/
    __init__.py
    harness.py     ClaudeCodeHarness(Harness)
    convert.py     event_stream_to_conversation(raw)  (module fn; harness delegates)
    constants.py   BINARY_AT (asset path), event-stream/stderr names, HOME, model
                   (+ the shared PROMPT_NAME it reads — the prompt is dataset's, §5.6)
```

The conversation observer is the **shared** `ConversationObserver` (task 06a,
`swe_lab/conversation/`); this task does not define a harness-specific observer.

Tests: `tests/test_claude_code_harness.py` (mounts + assets + body script +
conversion, all against FakeBackend / fixtures).

## 3. Key types & signatures

```python
# ─── harnesses/base.py ──────────────────────────────────────────────────────
type Assets = dict[str, Resource]    # container_path → resource, read-only (spec §mounts)

class Harness(ConversationProducer):     # + to_conversation + native_outputs (06a ABC)
  """A harness plug: it contributes the pieces a solving run needs.

  A behavior interface (ABC, per ADR-0002): claude_code now, codex/grok_build
  next, all sharing this contract. The engine (SandboxManager) never imports a
  concrete harness — the *composition* (run_rollout, task 07) calls these
  methods and wires the results into a manager + backend. So the ABC lives in
  the harness layer, not the engine core; "engine stays harness-agnostic" and
  "a harness is an ABC" are independent and both hold. Nothing harness-specific
  (event-stream parsing, the observer class) lives on this base — a harness
  contributes data (mounts, assets) + a `to_conversation`; the observer is shared.
  """

  @abstractmethod
  def mounts(self, workdir: str) -> Mounts: ...
  @abstractmethod
  def assets(self) -> Assets: ...                 # read-only fixed-path resources
  @abstractmethod
  def run(self, sb: Sandbox, *, timeout: float) -> None: ...  # the main action, called in the with
  # to_conversation(workspace) + native_outputs() are inherited (still abstract)
  # from ConversationProducer (task 06a); ClaudeCodeHarness implements them below.

# ─── harnesses/claude_code/harness.py ───────────────────────────────────────
@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  """The Claude Code agent as a sandbox-engine harness plug."""

  model: str = DEFAULT_MODEL
  binary_path: Path | None = None      # inject a ready binary (tests); else provisioned
  # NO `prompt` field — the prompt is dataset-derived (§5.6), staged by the
  # composition as prompt.txt; the harness only cats it in agent.sh.
  # NO `repo_root` field — how the binary is located/downloaded is
  # ensure_claude_binary's own concern (§5.3), not a harness-surface param.

  @override
  def mounts(self, workdir: str) -> Mounts:
    """Stage the harness's own file: the invocation script.

    The prompt is NOT here — it is dataset-derived (`prompt.txt`, staged by the
    rollout composition; §5.6). The harness only reads it in agent.sh.
    """
    return {
        AGENT_SCRIPT_NAME: Mount(
            Inline(self._invocation_script(workdir).encode()), executable=True
        ),
    }

  @override
  def assets(self) -> Assets:
    """Read-only resources placed at fixed container paths (outside the workspace).

    The pinned binary today; a harness may add read-only agent config (e.g. a
    Claude settings JSON) here later — assets are a set, not a single file.
    """
    binary = self.binary_path or ensure_claude_binary()  # provisioner finds/downloads it
    return {BINARY_AT: LocalFile(binary)}   # BINARY_AT = /opt/claude-code/claude

  @override
  def run(self, sb: Sandbox, *, timeout: float) -> None:
    """The main action: run the staged agent.sh by its workspace path."""
    _ = sb.run(AGENT_SCRIPT_NAME, timeout=timeout)  # run a workspace file by name

  @override
  def native_outputs(self) -> dict[str, str]:
    return {                             # every byproduct run() writes
        "event_stream": EVENT_STREAM_NAME,   # "event_stream.jsonl" (the primary)
        "agent_stderr": AGENT_STDERR_NAME,   # "agent.stderr" (the run's stderr log)
    }

  @override
  def to_conversation(self, workspace: Path) -> Conversation:
    return event_stream_to_conversation(workspace / EVENT_STREAM_NAME)  # module fn
```

The composition (task 07) just passes the harness to the **shared**
`ConversationObserver` (task 06a): `ConversationObserver(producer=harness)` — the
harness *is a* `ConversationProducer`. In `before_destroy` the observer calls
`producer.to_conversation(workspace)`, writes `conversation.json`, and registers
`conversation` **plus every native byproduct** (`event_stream`, `agent_stderr`,
…) via `producer.native_outputs()`. The main action is a **direct call** — no
factory/closure — the composition runs it inside the block:
`with manager.sandbox() as sb: harness.run(sb, timeout=timeout)`.

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
(`sb.run("agent.sh")`), so the exact invocation persists for audit. The
`prompt.txt` it `cat`s is **not** a harness mount — it is dataset-derived and
staged by the rollout composition (§5.6); the harness never sees its contents.

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
only thing that knows `Harness` — it calls `mounts()`/`assets()`/`run()`/
`to_conversation()` and wires them into a manager + backend. Nothing
harness-specific (the event-stream shape, an observer subclass) lives on the base
— a harness contributes data + a `to_conversation`; the observer is shared (§5.5).

### 5.2 Reuse only `ensure_claude_binary`; write the event-stream parsing fresh
Binary provisioning is reused as-is: `ensure_claude_binary` (`binary.py:85`) is
imported unchanged (defer its physical move to 10b). The **event-stream parsing
is written fresh** in `convert.py` (stdlib `json` over the stream-json lines →
the typed model) — it does **not** wrap `core/agent/trace.py`'s
`parse_stream_events` / `build_exchange_from_stream` / `last_stream_record`,
because those produce the legacy untyped `dict` and `trace.py` is headed for
**deprecation**. Wrapping a soon-dead dict-parser just to re-shape its output is
churn; parsing the raw lines straight into `Conversation` is simpler and leaves
nothing to unwind. W1 annotation still imports `core/agent/`
(`pipelines/related_files/agent_run.py`), so those legacy files stay put for now
and are removed with the deprecation cleanup at cutover (10b). (§8 Q1.)

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
`harness.assets()` into the backend. The harness holds **no `repo_root`**: how
the binary is located — discovered under the project root today, downloaded from
the network tomorrow — is `ensure_claude_binary`'s own concern; a `repo_root`
field would leak that provisioning detail onto the harness's public surface. The
one override is `binary_path` (inject a ready binary; used by Docker-free tests).
See [`workspace-layout.md`](../workspace-layout.md).

### 5.4 Rollout records failure; it does not classify-and-retry
W1's `errors.py` taxonomy (`classify_error_text`, `UsageLimitError`,
`RetryableError`) drives its retry loop. Rollout's model is different: run the
agent once, `|| true`, capture whatever resulted; a failed/partial run shows up
as a not-complete signal the harness derives fresh from the terminal `result`
event of the raw output (`subtype == "success" and not is_error`) and/or an empty
patch (task 07). No raising, no retry here — so this task pulls in none of
`errors.py`. (A resample tier, if ever wanted, is a composition-level concern,
not the harness's.)

### 5.5 Event-stream capture via a shared observer + a claude `to_conversation`
The conversation observer is **shared and harness-agnostic** (`ConversationObserver`,
task 06a): given the harness (a `ConversationProducer`), it writes
`conversation.json` and registers every native byproduct. Only the *conversion*
is Claude-specific — the module
function `event_stream_to_conversation` (which `Harness.to_conversation`
delegates to) parses the raw `--output-format stream-json` lines **fresh** (stdlib
`json`) into the typed model, not via the soon-deprecated `trace.py`. Proxy
capture (task 08) adds a faithful-wire strategy — the harness's `to_conversation`
grows to handle that format too (or dispatches on a `capture` selector) behind the
same shared observer, again parsed fresh. Stream needs no proxy process and is
what rollout uses today.

### 5.6 The harness is dataset-agnostic — the prompt is a dataset artifact
*(Added 2026-07-24 with the owner.)* The prompt is **data**: it is built from the
dataset instance (SWE-Bench-Pro's `problem_statement` / `requirements` /
`interface`, via `build_solve_prompt`), so it is the **dataset's** contribution,
staged into the workspace as `prompt.txt` by the rollout composition (task 07) —
exactly as the dataset stages `run_script.sh` / `parser.py` for an eval run. The
harness is **dataset-agnostic**: it neither builds nor owns the prompt; its
`agent.sh` only `cat`s `$SANDBOX_WORKSPACE/prompt.txt`, blind to the content. So
`ClaudeCodeHarness` has **no `prompt` field**, and its `mounts()` stages only
`agent.sh`. `PROMPT_NAME` (`prompt.txt`) is a shared solve-input **convention**
(the harness reads it; the composition/dataset writes it) — knowing the *filename*
is structural, not data coupling; a second harness (Codex) reads the same file.

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
- **Conversion:** against a checked-in `event_stream.jsonl` fixture (a few
  stream-json lines incl. a terminal `result` with `subtype:"success"`),
  `event_stream_to_conversation(raw)` (and `harness.to_conversation`) yields a
  typed `Conversation` (role-tagged messages, tool-use blocks paired to
  tool-results); an empty/absent file → `Conversation(messages=[])`. (The shared
  observer's file-plumbing is tested in task 06a.)
- **Main action:** with FakeBackend, `harness.run(sb, timeout=…)` calls
  `sb.run(AGENT_SCRIPT_NAME, …)` once (assert recorded).

## 7. Dependencies

Tasks 02, 03 (the **assets** field + the **materialize seam**), **06a** (the
`Conversation` model + the shared `ConversationObserver` + the `ConversationProducer`
ABC that `Harness` extends), and, at compose time, 04 via task 07. Reuses
`ensure_claude_binary` — no new runtime deps beyond 06a's Pydantic. New code
Google-docstring'd.

## 8. Open questions (need user confirmation)

1. **Reuse `ensure_claude_binary` by import (§5.2)** — OK to import
   `core/agent/binary`'s `ensure_claude_binary` now and defer its physical move
   to 10b? (The trace parsing is **not** reused — written fresh, per the owner
   2026-07-22, since `core/agent/trace.py` is deprecation-bound; it stays only
   because W1 still imports it, and goes with the 10b cleanup.)
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
