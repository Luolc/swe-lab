# Task 29 — Grok Build provisioning + `GrokHarness`

> Design, written before implementation, on the pattern of
> [task-28](task-28-codex-provisioning.md) (Codex) and
> [task-06](task-06-claude-code-harness.md) (Claude Code). Every claim below is
> **measured** on the real `grok 1.0.0 (3cd0d0cbce)` binary or read out of the
> published source (`xai-org/grok-build`, snapshot `b189869b`, 2026-07-15)
> unless marked *unverified*. Task-28's lesson is applied throughout: the
> design that was not run against the real artifact was wrong three times.

## 0. Summary

Grok Build (`grok`) is xAI's coding agent — a Rust binary like Codex, and like
Codex the Linux build is **statically linked musl**: it runs bare on Alpine,
ancient glibc and shell-less distroless. **There is no bundle to build**;
provisioning is fetch-verify-pin, one binary, no companion.

Two things make this harness *cheaper* than Codex's, one thing makes it
*riskier*:

- **The headless trace format is Claude Code's.** `--output-format
  streaming-messages-json` emits the stream-json schema our `claude_code`
  converter already parses — measured: `{"type":"system","subtype":"init",…}`
  and a terminal `{"type":"result","subtype":"error_during_execution",
  "is_error":true,…,"errors":[…]}`, byte-compatible with what
  `event_stream_to_conversation` / `event_stream_outcome` consume. The
  converter is therefore mostly **reuse, not new code** — and grok's headless
  mode has a real `--max-turns`, so `AgentOutcome.MAX_TURNS` is *reachable*
  here, unlike Codex.
- **Custom endpoint and effort are plain flags** (`--xai-api-base-url`,
  `--cli-chat-proxy-base-url`, `--reasoning-effort`), so there is no provider
  table to render and no `-c` machinery.
- **The `AGENTS.md` injection door has no off switch** (§6) — the one place
  this agent is *harder* to make safe than the other two.

## 1. Evidence — the binary (measured 2026-08-11)

```
$ ldd grok                    →  statically linked
libc fingerprint              →  musl: 3, GLIBC: 0   (codex control: musl: 5, GLIBC: 0)

bare binary, no bundle:
  alpine:3.19                        grok 1.0.0 (3cd0d0cbce)
  debian:10-slim                     grok 1.0.0 (3cd0d0cbce)
  gcr.io/distroless/static-debian12  grok 1.0.0 (3cd0d0cbce)
```

Same three images that motivated the Claude Code bundle; distroless passing
means no interpreter is even consulted. Size: **164 MB**, a bare ELF (not a
tarball).

One nuance against the task title as asked: there is no "musl **version**" to
choose. Linux ships exactly one artifact, `grok-<ver>-linux-x86_64` — the name
carries no target triple, but the binary *is* the musl build.

## 2. Distribution — closest to Claude Code's scheme

No GitHub releases, no npm. The official installer (`x.ai/cli/install.sh`,
read in full — 17 KB) resolves:

```
https://x.ai/cli/<channel>                        → version string ("stable" → 1.0.0)
https://x.ai/cli/grok-<version>-<os>-<arch>       → the bare binary
fallback: https://storage.googleapis.com/grok-build-public-artifacts/cli/…
```

That is Claude Code's shape (channel pointer → versioned bare binary), minus
one thing: **the installer verifies nothing**. No sha256, no signature,
anywhere in the script. So verification is ours, the same answer as task-28
§3: **pin the sha256 in-repo**, trust-on-first-use when the pin is set,
enforced on every later fetch on every machine.

Pinned for this task: `grok 1.0.0 linux-x86_64`, sha256
`28dbc967a5843dae2374b6834dadbab95354e685c7e5c8dc750b92a4e5fc7c3e`.

`aarch64` exists upstream (the installer maps `arm64|aarch64`); per the
standing rule it is not claimed until the matrix has run on it.

## 3. Provisioning

`harnesses/grok/binary.py`, on the codex module's pattern but simpler in one
material way: **one binary, no companion**. `binary_cache_path` returns a file
(as claude_code's does), not a directory — the codex directory shape exists
only because `codex-code-mode-host` must sit beside `codex`, and grok has no
analogue. *Unverified until the live e2e*: that a real tool-using run needs no
second binary; §9 keeps a check for it.

- `BINARY_AT = "/opt/grok/grok"`, backend-provisioned (ADR-0003; the harness
  never stages its own machinery).
- Version policy: pin + `--pinned`-style refetch, **channel resolution**
  supported (grok has a real `stable` channel, which codex lacks); no version
  floor invented — nothing is measured that would justify one.
- `HostGrokBinaryObserver`, **opt-in** like codex's, and for the same reason —
  the backend cannot see which agent a run uses. The asymmetry is task-28 §7's
  evidence, deliberately not widened into a third default.

## 4. The harness — `codex` sibling, `claude_code` converter

`GrokHarness` follows the codex file layout (`constants.py`, `binary.py`,
`harness.py`, and only if the live capture demands it a `convert.py`):

```
grok -p @<prompt-file>?      ← see below; prompt delivery is --prompt-file
     --prompt-file <ws>/prompt.txt
     --output-format streaming-messages-json
     --permission-mode bypassPermissions
     --max-turns 500
     --reasoning-effort high
     --model grok-4.5
     [bare switches, §6]
     > <ws>/grok.event_stream.jsonl  2> <ws>/grok.stderr.log
```

- **Prompt by `--prompt-file`** — grok reads the prompt from a file natively,
  so the stdin plumbing both other harnesses need does not exist here, and
  argv quoting is moot.
- **Model and effort pinned** (`grok-4.5`, `high`): `grok-4.5` is the
  measured default of the pinned build and `--reasoning-effort` is a real
  flag. Both overridable, `None` omits — the codex contract.
- **`--max-turns` pinned (500)** like claude_code. This is the flag codex
  lacks; the outcome classifier must map whatever the terminal result says on
  turn exhaustion (expected: a distinct `result` subtype, per the schema
  below) onto `AgentOutcome.MAX_TURNS` — *the exact subtype string is
  unverified until a live capture*.
- **Trace conversion**: start from `claude_code.convert` — measured init and
  result events are schema-identical. The design decision is **import and
  delegate, not copy**: `grok/convert.py` wraps the claude_code functions and
  owns only whatever deltas the live capture shows (e.g. grok-specific
  `system` subtypes, the `server_tool_use` usage block). If the live capture
  shows *zero* deltas, the wrapper still exists as the seam where they would
  land, one function deep.
- **Outcome**: same delegation. The captured failure already shows
  `error_during_execution` + `is_error` + `errors[]` exactly as Claude Code
  emits them, and ADR-0011's mapping applies unchanged. `MAX_TURNS` becomes
  reachable — a named test pins it once the live subtype is known.
- **Exit status out-of-band** (`grok.exit_code`), script always exits 0 — the
  house pattern, unchanged.

## 5. Auth

Same two-track shape as codex, same machinery:

- **API key**: `XAI_API_KEY`, an env var → the sandbox's `pass_env`, nothing
  new to build.
- **OAuth login**: `~/.grok/auth.json` — a *file*, keyed by OIDC scope,
  carrying `refresh_token`. Staged by a `GrokAuthObserver` on the codex
  pattern: **inline bytes** (remote-sandbox-safe, secret-manager-friendly),
  `repr=False`, host-side JSON validation, writable so a refresh can land.
  `GROK_HOME`-equivalent: grok derives its dir from `$HOME/.grok` (source:
  `grok_home()`), so the harness exports `HOME=/agent-home` — same layout rule
  the codex review fixed (`$HOME/.grok`, never a bare relocated dir).
- **Custom endpoint**: `--xai-api-base-url` / `--cli-chat-proxy-base-url` are
  **flags**, so the codex `CodexProvider` machinery has no counterpart here; a
  `base_url`-style field on the harness renders one flag. The API key still
  travels only by `pass_env` — never argv (the flag carries the URL, not the
  key).

*Measured limitation*: this machine's grok refresh token is dead (expired
2026-07-31; a live API call does not refresh it). The e2e needs one
interactive `grok login` from the operator first; recorded in §9.

## 6. Bare mode — one door has no handle

What the repo under test can inject, from source (`prompt/agents_md.rs`,
`types/compat.rs`) and `--help`:

| Door | Switch | Status |
|---|---|---|
| Plan mode | `--no-plan` | flag, measured in `--help` |
| Subagents | `--no-subagents` | flag |
| Cross-session memory | `--no-memory` | flag |
| Web search / fetch | `--disable-web-search` | flag — **on by default otherwise**, an egress door ADR-0010 wants shut |
| MCP servers / plugins | config-scoped; `--plugin-dir` only *adds* | expect none in a fresh `$HOME`; verify with `grok inspect` |
| `.claude`/`.cursor`/`.codex` files in the repo | `[compat]` cells in `config.toml` | staged config in the fresh grok home |
| **`AGENTS.md` from the repo (cwd → git root)** | **none found** | see below |

The last row is the finding that shapes this design. Grok injects repo
`AGENTS.md` as a **prepended user message** (`agents_md_user_reminder()`), so:

- `--system-prompt-override` does *not* remove it (it is not in the system
  prompt);
- no config key gates it (`CompatConfig` gates only the vendor-compat dirs);
- deleting the file from the workspace would mutate the repo under test.

**Design position**: run the BANANA probe (task-28's §Result C-series method)
against the real binary first. If injection is confirmed and no knob exists,
ship `bare` covering every door that *has* a switch, and handle `AGENTS.md`
by **detection instead of prevention** — which ADR-0010 already blesses as
the fallback ("verifier tampering detected not blocked"). Concretely: the
prepended user message is *visible in the trace this harness captures* (unlike
Claude Code's reminders, which STREAM never sees — the 2026-08-07 capture
study), so the harness's `outputs`/verifier seam can flag a run whose first
user message carries an `AGENTS.md` block that the instance did not ship.
An upstream feature request is the real fix; the doc records the gap loudly
rather than shipping a `bare` that silently does less than claude_code's.

*The BANANA probe and the detection hook are e2e-gated work, not prose — §9.*

## 7. Interactive tools

Checked because codex needed the answer written down (#217): does headless
grok hang on an ask-the-user tool? *Partially verified*: `--permission-mode
bypassPermissions` + `--always-approve` exist precisely to unblock approvals,
and the failed live run terminated cleanly with a `result` event rather than
hanging on auth. Whether any tool can still block a `-p` run is **open until
the live e2e**; if one can, the codex answer (structural rejection) does not
apply and a denylist via `--disallowed-tools` does — the flag exists.

## 8. File organization

```
src/swe_lab/harnesses/grok/
  __init__.py        # register_harness("grok", GrokHarness)
  constants.py       # BINARY_AT=/opt/grok/grok, names, defaults
  binary.py          # channel resolve + pinned sha256 + fetch (one binary)
  auth.py            # GrokAuthObserver — inline auth.json, codex pattern
  harness.py         # GrokHarness + AgentInfoObserver (--version/--help)
  convert.py         # thin delegation to claude_code.convert + grok deltas
tests/test_grok_harness.py
```

`sandbox/backends/host.py` gains `HostGrokBinaryObserver` (opt-in). The
registration lands in `workflow/definitions.py` alongside codex's — the same
"selectable by name must not depend on a shipped definition using it" fix as
#214's follow-up.

## 9. E2e verification plan (gates the implementation PR)

1. **Portability matrix** on the pinned binary — done for `--version` (§1);
   re-run as part of the harness path.
2. **Live headless run** (needs operator `grok login` first — the local
   refresh token is dead): trivial prompt → capture the real
   `streaming-messages-json` stream; diff its event/subtype inventory against
   claude_code's; fix the converter deltas from evidence.
3. **Tool-using run**: edit + verify a file; confirms single-binary (no
   code-mode-host analogue), tool events convert, `is_error` mapping.
4. **BANANA probe ×2**: repo `AGENTS.md` with and without `bare` — decides §6;
   plus `grok inspect` output archived in the run artifacts.
5. **Outcome taxonomy probes**: bad model name (expected `error_during_
   execution`), `--max-turns 1` on a multi-step task (pins the MAX_TURNS
   subtype), kill mid-run (TRUNCATED).
6. **Full chain**: `rollout_and_unit_test` on a real SWE-Bench Pro instance
   with `--rollout.harness=grok` — the bar codex set: `unit_test.resolved`
   from a real graded verdict, `agent_outcome` on the shard.

## 10. Out of scope

- arm64 (unclaimed until run), the GH-job backend path, a shipped workflow
  definition using grok, and the §7-of-task-28 provisioning-seam
  generalization — which this task makes *more* urgent (a third hardcoded
  observer) but does not do.
- `grok agent stdio|serve|headless` — the ACP/embedding surface; our path is
  top-level `-p`.

## 11. Decisions taken

1. **No bundle; fetch-verify-pin one binary** (§1–2). Verification is a
   pinned in-repo sha256 — the installer checks nothing.
2. **Reuse the claude_code converter by delegation** (§4) — the wire format is
   measured-identical for init/result; deltas land in `grok/convert.py` only
   with capture evidence behind them.
3. **`--prompt-file` for prompt delivery** — no stdin plumbing.
4. **Bare mode ships with every door that has a switch; `AGENTS.md` is
   detection-not-prevention** pending the BANANA probe (§6), recorded loudly.
5. **Model/effort/turns pinned** (`grok-4.5` / `high` / 500), `None` opts out
   — the codex contract.
