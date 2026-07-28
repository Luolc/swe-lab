# Task 08 — Proxy capture mode

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.

---

## 1. Purpose & scope

Give the `claude_code` harness a **second output-capture strategy** — the
`cc-reverse-proxy` (`core/agent/proxy.py`) — alongside today's default
event-stream capture, and prove the proxy path converts into a **`Conversation`
equivalent** to the stream path. Per the spec, "proxy is not legacy — it is a
capture *strategy* a future harness will need."

### In scope

- `harnesses/claude_code/convert.py`: `proxy_log_to_conversation(raw) ->
  Conversation` + `proxy_log_complete(raw) -> bool`, **reusing** the existing
  `_content_blocks` / `_one_block` block mappers (the proxy record carries the
  same Anthropic content blocks as the stream).
- A `Capture` enum on the harness (`STREAM` default, `PROXY`); `capture` +
  `proxy_base_url` fields; capture-branched `_invocation_script`,
  `native_outputs`, `to_conversation`.
- `solve.py`: a `capture` parameter; on `PROXY`, wrap the sandbox block in a
  `ReverseProxy` whose output lands **in the workspace** (so it is a normal
  registered artifact) and point the agent at it via `ANTHROPIC_BASE_URL`.
- `DockerHostBackend`: an `extra_hosts` field so the in-container agent can
  reach the host-side proxy over the Docker host-gateway.
- `cli/rollout.py`: `--capture stream|proxy`.
- Fast, Docker-free unit tests; the equivalence test is the CI gate.

### Out of scope

- **Changing rollout auth.** Routing the OAuth token *through* the proxy (audit
  P0-1) is a separate security ADR (plan §Out of scope). This task uses the
  proxy only to **capture**; the token still reaches the agent exactly as today
  (`CLAUDE_CODE_OAUTH_TOKEN` by reference).
- A **live** proxy round-trip in CI. Building the Go proxy needs the
  `cc-reverse-proxy` submodule + a Go toolchain, and `ci.yml` checks out
  neither; the live container→host proxy path is a **manual (CP2-style)**
  validation, exactly like the live flipt rollout. CI covers the pure converter
  + the harness/backend seams (argv- and fixture-level).
- Retiring `core/agent/trace.py`'s dict `build_exchange_from_*` (W1 still uses
  it; it dies with the W1 migration, not here).

## 2. Module layout

```
harnesses/claude_code/
  capture.py        NEW: Capture(StrEnum) = STREAM | PROXY
  constants.py      + PROXY_LOG_NAME, CONTAINER_PROXY_HOST
  convert.py        + proxy_log_to_conversation, proxy_log_complete
  harness.py        + capture / proxy_base_url fields; branch script/outputs/convert
solve.py            + capture param; ReverseProxy lifecycle on the proxy path
sandbox/backends/host.py   + extra_hosts (host-gateway) field
cli/rollout.py      + --capture
```

Tests: `tests/test_proxy_capture.py` (converter + equivalence),
extend `tests/test_claude_code_harness.py` (capture-branched script/outputs),
extend `tests/test_host_backend.py` (extra_hosts argv).

## 3. Key types & signatures

```python
# ─── harnesses/claude_code/capture.py ───────────────────────────────────────
class Capture(StrEnum):
  STREAM = "stream"    # agent stdout → claude.event_stream.jsonl (default)
  PROXY = "proxy"      # cc-reverse-proxy records request/response host-side

# ─── convert.py (reusing _content_blocks / _one_block) ──────────────────────
def proxy_log_to_conversation(raw: Path) -> Conversation:
  """Last proxy record → Conversation. Anthropic is stateless, so the LAST
  request's body.messages IS the whole prior conversation; response.message is
  the final assistant turn. system (if present) becomes a leading SYSTEM msg."""
  record = _last_proxy_record(raw)          # last JSONL line, {} if absent
  body = record.get("request", {}).get("body", {})
  messages: list[Message] = []
  system = _content_blocks(body.get("system"))         # str|blocks → blocks
  if system:
    messages.append(Message(role=Role.SYSTEM, content=system))
  for m in body.get("messages", []):                   # user/assistant turns
    role, blocks = _role(m.get("role")), _content_blocks(m.get("content"))
    if role is not None and blocks:
      messages.append(Message(role=role, content=blocks))
  final = record.get("response", {}).get("message", {})  # final assistant turn
  fb = _content_blocks(final.get("content"))
  if fb:
    messages.append(Message(role=Role.ASSISTANT, content=fb))
  return Conversation(messages=messages)

def proxy_log_complete(raw: Path) -> bool:
  """The proxy's own per-record `complete` flag on the last record."""
  return bool(_last_proxy_record(raw).get("complete", False))

# ─── harness.py ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ClaudeCodeHarness(Harness):
  model: str = DEFAULT_MODEL
  binary_path: Path | None = None
  capture: Capture = Capture.STREAM
  proxy_base_url: str | None = None     # set by the composition on the proxy path
  # native_outputs(): STREAM → {event_stream, stderr}; PROXY → {proxy_log, stderr}
  # to_conversation(): branch STREAM/PROXY
  # _invocation_script(): PROXY adds `export ANTHROPIC_BASE_URL=<proxy_base_url>`
  #   and drops the stream redirect (the proxy is the capture; stdout → json)
```

`_role` gains a `system → Role.SYSTEM` case (today only user/assistant);
`_content_blocks` already accepts a bare string (Anthropic `system` may be a
plain string) — `convert.py:105-108`.

## 4. Where the proxy lives — the container/host seam

W1 runs the agent as a **host subprocess**, so `proxy.base_url`
(`http://127.0.0.1:<port>`) is directly reachable (`agent_run.py:301,429`). In
rollout the agent runs **inside the instance container**, so two things change:

1. **The proxy writes into the workspace.** The composition points
   `ReverseProxy.output_path` at `workspace / PROXY_LOG_NAME`. The proxy is a
   host process, but the workspace is the bind-mounted shared dir, so the log is
   a normal workspace artifact — `native_outputs` registers it and
   `to_conversation(workspace)` finds it, exactly like the stream file. (The
   `event_stream` is written by the *container*; the proxy log is written by the
   *host* — both land in the same shared workspace. Consistent with spec §"the
   shared state is the workspace filesystem".)
2. **The container reaches the host over the Docker host-gateway.** The proxy
   binds `:port` (all interfaces — `reverse_proxy.go:769`), and the container
   gets `--add-host=host.docker.internal:host-gateway`; the agent's
   `ANTHROPIC_BASE_URL` is `http://host.docker.internal:<port>`
   (`CONTAINER_PROXY_HOST`). This is the one piece only the live (manual) path
   exercises; it is argv-unit-tested on the backend.

Composition sketch (`solve.py`, proxy branch only — the default stream path is
untouched):

```python
if capture is Capture.PROXY:
  binary = build_proxy(find_repo_root())
  port = port_for_index(_port_index(spec.instance_id))
  base = f"http://{CONTAINER_PROXY_HOST}:{port}"
  harness = ClaudeCodeHarness(model=model, capture=capture, proxy_base_url=base)
  backend = backend.with_extra_hosts((f"{CONTAINER_PROXY_HOST}:host-gateway",))
  with ReverseProxy(port, workspace / PROXY_LOG_NAME, binary):
    with manager.sandbox() as sb:
      harness.run(sb, timeout=timeout)
```

## 5. Equivalence — the CI gate

The heart of the task (spec: "capture=proxy produces an exchange record
**equivalent** to the stream path"). The test builds **parallel fixtures** for
one session — a stream `event_stream.jsonl` and a proxy `.jsonl` whose last
record's `request.body.messages` + `response.message` encode the *same* turns —
and asserts the **user/assistant messages match**:

```python
stream_conv = event_stream_to_conversation(stream_fixture)
proxy_conv  = proxy_log_to_conversation(proxy_fixture)
# proxy additionally carries the SYSTEM turn (a richer capture); compare the
# shared user/assistant surface:
assert _non_system(proxy_conv) == stream_conv
```

Reusing `_content_blocks`/`_one_block` for both paths makes the block mapping
identical **by construction** (`TextBlock`/`ReasoningBlock`/`ToolUseBlock`/
`ToolResultBlock`), so equivalence reduces to "same turns, same order". Fixtures
are inline Python literals, mirroring `test_exchange_record.py`'s
`_raw_proxy_record` and `test_claude_code_harness.py`'s `_EVENTS`.

Also tested: absent/empty file → `Conversation(messages=[])`;
`proxy_log_complete` reads the last record's `complete`; the harness emits
`ANTHROPIC_BASE_URL` + registers `proxy_log` under `capture=PROXY` and is
byte-for-byte unchanged under the default `STREAM`.

## 6. Design decisions

### 6.1 Capture is a harness enum, not the legacy `trace.py` strings
`trace.py` already defines `CAPTURE_STREAM/CAPTURE_PROXY` (`trace.py:37-40`), but
it is the soon-deprecated W1 dict world. The harness gets its **own** `Capture`
enum so the new engine never imports the legacy module; the two converge only in
that they name the same two strategies.

### 6.2 Reuse the block mappers, not `build_exchange_from_proxy`
`trace.py:build_exchange_from_proxy` (`:232-272`) produces the untyped W1
exchange **dict** (with PII redaction for host runs). The rollout agent runs
in-container with no operator identity in the trace (task 06 §convert), so we do
**not** need redaction and we **do** want the typed `Conversation`. Mapping the
proxy record's messages through `convert.py`'s existing helpers gives the typed
model directly and guarantees stream/proxy parity.

### 6.3 The proxy log is a workspace artifact
Writing the proxy output into `sb.workspace` (not a side path) keeps the capture
model uniform: one `to_conversation(workspace)`, one `native_outputs` contract,
one persisted-artifact story for task 12 — the only difference between the
strategies is *which* file holds the trace and *who* wrote it.

### 6.4 Auth is untouched
The proxy forwards to `api.anthropic.com` and only *records*; the agent still
authenticates with `CLAUDE_CODE_OAUTH_TOKEN` as today. Token-via-proxy (audit
P0-1) is explicitly a separate ADR (plan §Out of scope) — not decided here.

## 7. Open questions (decided under full-auto; revisit on review)

1. **Proxy port derivation.** `port_for_index` wants a stable index; rollout has
   an `instance_id`, not a dataset index. Decision: a small stable hash of
   `instance_id` into the port band (documented, deterministic). Alternative: an
   ephemeral free port. Chose the hash to mirror W1's per-run distinct-port
   discipline.
2. **`--output-format` in proxy mode.** W1 uses `--output-format json`
   (`agent_run.py:428-439`) — the agent's own result — while the trace comes
   from the proxy. Decision: mirror W1 (`json`), since the stream file is not the
   capture in proxy mode.
3. **Live path verification.** Deferred to a manual CP2-style run (Docker + Go +
   submodule), like the live flipt rollout. CI proves the converter + seams.
