# swe-lab-supervisor

The in-sandbox supervision runtime of swe-lab, as a static Linux binary. The
design: it wraps a coding agent (the **actor**) as its child, owns the child's
stdin, stdout, stderr and process group, drains the actor's `stream-json`
output while a judge model is consulted at configured boundaries, and writes
short corrections on the actor's stdin. The design of record is
[issue #375](https://github.com/Luolc/swe-lab/issues/375); the decisions this
implementation made where #375 leaves a choice open are in
[`docs/trace-synthesis/plans/task-20-native-supervisor-runtime.md`](../../docs/trace-synthesis/plans/task-20-native-supervisor-runtime.md).

## Status

The crate lands in slices, and this section says what the binary at this
revision actually does; the rest of this README is the contract the slices
build towards.

- **Complete:** `criteria`, `--version`, `--help`; the config file and its
  validation ([Config](#config)); the criterion digest check; the endpoint
  and credential variables ([Environment](#environment)); and the process
  wrapper — `run` launches the actor in its own process group with the two
  environment variables scrubbed, writes the task on its stdin as one
  `stream-json` user event, drains its stdout to `--actor-event-log` and its
  stderr to `--actor-stderr`, ends it deliberately (`SIGTERM`, `term_grace_ms`,
  `SIGKILL`; also on `SIGTERM` / `SIGINT` to the wrapper), and exits as the
  actor did.
- **Not yet:** no judgment is made and no correction is written, so every
  run is the actor alone, with its stdin closed right after the prompt.
  `--supervisor-log` and `--summary` are accepted and **not written**. The
  judgment loop (boundaries, judge, corrections, the log and the summary) is
  the next slice, which rewrites this section.

The policy it runs descends from the one `src/swe_lab/trace_synthesis/` runs
on the host — the same evidence filter, the same criterion artifact (compiled
in from the same file, so the two cannot drift without the digest saying so),
the same two model calls — with the three defects the replay experiment
measured in the host runtime fixed rather than reproduced (task 20 §7). What
the binary adds is what only a process wrapper can do: continuous draining,
actor blocking, stale-verdict discard, and one owner for the actor's shutdown.

## Usage

```text
swe-lab-supervisor run \
    --config /workspace/supervisor-config.json \
    --actor-event-log /workspace/actor.event_stream.jsonl \
    --supervisor-log /workspace/supervisor.jsonl \
    --summary /workspace/supervisor-summary.json \
    --actor-stderr /workspace/actor.stderr.log \
    -- claude -p --output-format stream-json --verbose --input-format stream-json ...
swe-lab-supervisor criteria     # the embedded criteria and their sha256
swe-lab-supervisor --version
swe-lab-supervisor --help
```

The actor argv after `--` is executed as given — never joined into a shell
command, never augmented. Flag construction for a particular harness stays on
the Python side.

**Exit code.** When the wrapper ran cleanly, its exit status is the actor's
(`128 + signal` when the actor died of a signal), so a script that records
`$?` sees what it would have seen without the wrapper. `2` is a usage error and
`3` a refused run (unusable config, a criterion whose digest is not the pinned
one) — both before any actor process exists. Never classify a run from the exit status alone:
the terminal summary is written for that.

## Config

One JSON file, schema-versioned, **non-secret**. Every field below is
required; an unknown field is refused, not ignored. Where the model is and how
to authenticate to it are not in the file at all — see
[Environment](#environment).

```json
{
  "schema_version": 1,
  "task": "the task text the actor was given",
  "criterion": { "name": "general-practice", "sha256": "<pinned digest>" },
  "policy": {
    "kind": "speak-when-off-track",
    "budget": 3,
    "cooldown": 4,
    "window": 8,
    "judge_every_n_assistant_messages": 3,
    "block_actor_while_judging": "stdout"
  },
  "model": { "name": "anthropic/claude-sonnet-5" },
  "timeouts": { "model_call_ms": 180000, "term_grace_ms": 10000 },
  "limits": { "max_event_line_bytes": 16777216 }
}
```

- `criterion` — the name of a criterion compiled into the binary and the
  sha256 its text must have. Startup verifies the digest before the actor is
  launched and refuses the run on a mismatch. `swe-lab-supervisor criteria`
  prints what a given binary carries.
- `policy` — no value has a default. `budget` is how many corrections a run
  may carry (`0` is the control arm: judged, never spoken). `cooldown` is how
  many judgment boundaries must pass between two corrections. `window` is how
  many of the actor's most recent admitted records the judge sees.
  `judge_every_n_assistant_messages` is the batch size `N` of #375 — a
  boundary falls every `N` admitted assistant messages and at every actor
  `result` with new evidence behind it. `block_actor_while_judging` is what
  the wrapper does to the actor while a judgment is in flight: `"off"` lets
  it run ahead (overtaken verdicts are discarded as stale), `"stdout"` stops
  reading its stdout (the actor blocks on its next write once the pipe is
  full), `"sigstop"` stops its process group with `SIGSTOP` and resumes it
  with `SIGCONT` — always, on every path out, before the wrapper exits.
- `model` — the model name sent on every request, recorded in the summary.
- `timeouts` — `model_call_ms` bounds one judge or writer call; a call past it
  is one recorded lapse. `term_grace_ms` bounds shutdown: how long the actor's
  process group gets to honour `SIGTERM` before `SIGKILL`, and how long the
  actor gets to exit on its own after its stdin is closed deliberately.
- `limits` — `max_event_line_bytes` is the ceiling on one line of actor
  stdout. Framing uses a growable buffer up to it; a longer line is still
  written to the event log verbatim but reaches no judgment, and the summary
  counts it.

## Environment

Two variables, read by the binary in-process and removed from the actor's
environment before it is launched. They travel into the sandbox the way the
actor's own `ANTHROPIC_BASE_URL` and token do — by reference, never on a
command line.

| Variable | Meaning |
| --- | --- |
| `SWE_LAB_SUPERVISOR_BASE_URL` | **Required.** The base URL of an OpenAI-shaped chat-completions API, `http://host[:port]/v1`; the binary appends `/chat/completions`. **Plain HTTP only** — `https://` is refused with the reason: the binary carries no TLS, so it speaks to a loopback forwarder in the sandbox (the `cc-reverse-proxy` instance the Python side starts with `--target https://openrouter.ai/api`), which terminates TLS and forwards the bytes unchanged. |
| `SWE_LAB_SUPERVISOR_API_KEY` | Optional. The bearer credential the endpoint needs, put in the `Authorization` header by the binary and forwarded by the proxy. Several keys may be comma-separated; the first is used, split in-process. Unset or empty, no header is sent. It appears in no config, argv, log or summary. |

## Building

The toolchain is pinned by `rust-toolchain.toml`; `rustup` picks it up. The
release artifact targets `x86_64-unknown-linux-musl` and links self-contained,
so no musl C toolchain is needed — and none may become needed: **every
dependency is pure Rust** (no `*-sys`, no `cc`), which is what keeps the build
surface at `rustup` plus the target.

```sh
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
readelf -l target/x86_64-unknown-linux-musl/release/swe-lab-supervisor | grep INTERP && echo NOT STATIC
```

"Static" is the absence of a `PT_INTERP` program header — the structural fact
that no dynamic linker is needed — not a word in `file` or `ldd` output, whose
phrasing moves with libc and version.

## Checks

The gates CI runs (`.github/workflows/ci.yml`, job `rust`) are one script,
[`scripts/gates.sh`](scripts/gates.sh): format, clippy with warnings as
errors, tests, docs with broken links as errors, a pure-Rust dependency tree,
the static release build, and the `PT_INTERP` check above. With a local
toolchain:

```sh
scripts/gates.sh
```

Without one — this repo's machines deliberately do not install toolchains
per project — the same gates run inside the official Rust image pinned to the
same version, with the registry and build output cached under
`target/container/`:

```sh
scripts/check-in-container.sh                  # the gates
scripts/check-in-container.sh cargo test foo   # or one command
```

There is no pre-commit hook for any of this on purpose: the commit hooks are
for second-level lint that needs no heavy toolchain. CI is the enforcement;
run the gates before pushing.
