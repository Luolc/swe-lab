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

Complete: everything this README describes is implemented, and the crate's
end-to-end test runs it — a canned actor and a canned endpoint on loopback,
launch to summary, under each blocking mode. What is measured but not yet
decided about the reach of `stdout` blocking is in task 20 §4.

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
one, an unusable endpoint, an actor that could not be launched) — all before
any actor process exists, or before it took its prompt. `1` is the one case
the wrapper's own status replaces the actor's: the actor ran but the summary
could not be written. Never classify a run from the exit status alone: the
terminal summary is written for that.

## Artifacts

Four files, at the paths given on the command line:

- `--actor-event-log` — the actor's stdout, line by line, verbatim, flushed
  per line; a line over `max_event_line_bytes` is written too, and counted.
- `--actor-stderr` — the actor's stderr, verbatim.
- `--supervisor-log` — the supervisor's account, one JSON object per line:
  every actor event consumed (`observed`) and every boundary's outcome
  (`spoke`, `silent`, `unjudged`, `lapse`, `stale`, `gap`), each with its
  cursor, time, the origin filter's disposition of the event, and the model
  calls made. The row shape and what each kind means: task 20 §6.
- `--summary` — the terminal summary, written to a temporary name and renamed
  when the run ends, so it is either whole or absent: schema version,
  `accounted_for`, how the wrapper and the actor ended, the counts (events,
  boundaries, corrections, silent, unjudged, lapses, gaps, stale verdicts,
  undecodable and oversized lines), the maximum decision lag, the model and
  criterion digest, and the sha256 of the two logs. A refused run writes one
  too, with `supervisor_exit: "refused"` and the reason, whenever the summary
  path itself is writable.

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
  "limits": {
    "max_event_line_bytes": 16777216,
    "max_actor_stdout_bytes": 1073741824,
    "max_actor_stderr_bytes": 268435456
  }
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
  counts it. `max_actor_stdout_bytes` and `max_actor_stderr_bytes` cap the
  two logs, exact to the byte: a line that would cross the cap is not
  written, the stream is not read further, and the run is ended and reported
  as not accounted for. Without them an actor that never stops writing fills
  the sandbox before the summary can be written. The wrapper's own memory is
  bounded independently: one line up to the ceiling, plus at most 16 lines
  queued ahead of the loop.

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
