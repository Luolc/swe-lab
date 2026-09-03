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
    --actor-prompt /workspace/prompt.stream.json \
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

**The actor's prompt is a file.** `--actor-prompt` is written on the actor's
stdin byte for byte before anything else, and the wrapper does not read it:
whether it is one `stream-json` user event or several, and how it is framed,
is between the side that writes the file and the actor. The wrapper's own
stdin is never read, and when the actor's stdin closes is the loop's decision
(a quiet `result` closes it; a correction at a `result` keeps it open), never
a file's end. The `task` in the config is the judge's statement of the goal
and is not sent to the actor — the two are edited independently.

**Exit code.** When the wrapper ran cleanly, its exit status is the actor's
(`128 + signal` when the actor died of a signal), so a script that records
`$?` sees what it would have seen without the wrapper. `2` is a usage error and
`3` a refused run (unusable config, an unreadable prompt file, a criterion
whose digest is not the pinned one, an unusable endpoint, two outputs on
one file — the same path twice, or a hard link or symlink to it — an
actor that could not be launched) — all before any actor process exists, or before it took its
prompt. `1` is the case the wrapper's own status replaces the actor's: the
actor ran, and the run is **not accounted for** — a drain stopped with an
error or did not finish, the sweep for the actor's descendants could not prove
none survived, the wrapper's ending was unclean, or the summary could not be
written — because then the actor's success cannot be read off its record. And
a wrapper told to stop by `SIGTERM` / `SIGINT` exits `128 + that signal`
whatever the actor made of its own ending (the summary says `terminated`): a
cancelled run is reported as cancelled, never as the actor's success. Never
classify a run from the exit status alone: the terminal summary is written
for that.

**What the wrapper accepts rather than guarantees.** Its containment of the
actor's descendants is a mark in their environment, inherited from launch and
swept for at the end: a descendant that clears its own environment before
`setsid` is invisible to that sweep, and between the sweep's identity check
(pid plus start time) and its `kill` a pid could in principle be reused. Both
are accepted, on the record here: the actor is Claude Code, not an adversary,
and the wrapper runs inside a container that is discarded after the run —
the container is the boundary, the sweep is diligence within it. A process
that is not this user's — another user's, or one of its own that made
itself undumpable — is outside the sweep's sight, and the first outside its
reach: a conclusion drawn from the owner of its `/proc` entry, not a read
that failed. What the sweep cannot read of this user's own, and what it
cannot prove, it reports, and the run is not accounted for.

## Artifacts

Four files, at the paths given on the command line:

- `--actor-event-log` — the actor's stdout, line by line, verbatim, flushed
  per line; a line over `max_event_line_bytes` is written too, and counted.
- `--actor-stderr` — the actor's stderr, verbatim.
- `--supervisor-log` — the supervisor's account, one JSON object per line:
  every actor event consumed (`observed`) and every boundary's outcome
  (`spoke`, `silent`, `unjudged`, `lapse`, `stale`, `gap`), each with its
  cursor, time, the origin filter's disposition of the event, and the model
  calls made. The row shape and what each kind means: task 20 §6. It is
  capped at `max_actor_stdout_bytes` — deliberately the cap of the stdout it
  accounts for, not a key of its own — and a row that would cross the cap,
  or a write that fails, ends the run as not accounted for: an account the
  wrapper can no longer keep is not evidence about supervision.
- `--summary` — the terminal summary, written to a staging name
  (`<summary>.partial`) and renamed into place when the run ends, so it is
  either whole or absent — nothing exists at its name until then; the name
  and the staging name are reserved at the start, against the logs, but not
  created. It carries the schema version, `accounted_for`, how the wrapper
  ended (`supervisor_exit`) and how the actor did (`actor_exit_code`: its
  code, or `128 + signal` when it died of one; `actor_exit_signal` beside
  it), the counts (events, boundaries, corrections, silent, unjudged,
  lapses, gaps, stale verdicts, undecodable and oversized lines), the
  maximum decision lag, the model and criterion digest, and the sha256 of
  the two logs — each read back through the descriptor the wrapper wrote it
  by, so it is the digest of the file the wrapper wrote, whatever is at the
  name by then; `accounted_for` requires both. Every run in which an actor
  existed ends in one — a stop
  that arrives while the prompt is still being written is `terminated`, a
  prompt the actor did not take is `unclean` — and a refused run writes one
  too, with `supervisor_exit: "refused"` and the reason, whenever the
  summary path itself is writable.

The three logs must be three regular files (a device or a pipe is not a
record with a digest), and no two of the four paths may name one file —
the same path twice, a hard link, a symlink to another, the summary's
staging name among them. That is checked before anything is truncated, so
a refusal leaves every file as it was.

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
    "block_actor_while_judging": "sigstop"
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

- `task` — the goal the judge measures the actor against, quoted in every
  judge and writer prompt. It is not the actor's prompt (that is the
  `--actor-prompt` file) and is not sent to the actor.
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
  the wrapper does to the actor while a judgment is in flight, and
  **`"sigstop"` is the mode a run should use**: the process group is stopped
  with `SIGSTOP` when a boundary falls, and the judge is not started until
  the stop is confirmed — the group read from `/proc` until two looks in a
  row find the same members, every thread of each stopped, with the signal
  sent again after a look that finds one running (for the child a member
  mid-fork had that the first signal never reached) — and the stdout reader
  has reported a barrier behind everything the actor had written. The
  snapshot is taken then, so nothing the actor wrote before it stopped is
  missing from it; the group stays stopped through the freshness check and
  the correction's write, and is resumed with `SIGCONT` after — and on
  every other path out, before the wrapper exits. A group that cannot be
  confirmed stopped within two seconds leaves that boundary unjudged, with
  the reason, rather than judged on evidence that may still be moving. Its
  cost is a real state: a wrapper that dies during a
  judgment leaves the actor stopped, which the `SIGCONT`-on-every-path and
  the handle's drop backstop mitigate but cannot rule out. `"stdout"` stops
  reading the actor's stdout instead, and self-releases if the wrapper dies,
  but it leaves a blind window: the actor can still write one pipe after the
  gate closes (544 events, ~65 KiB, measured on the host's 6.17 kernel; the
  size is the kernel's, the window is the mechanism's), and those events
  land only after the freshness check — **the stale gate is not guaranteed
  under `"stdout"`**, and a smaller pipe narrows the window without closing
  it. `"off"` lets the actor run ahead and relies
  on the stale gate alone. The measurement and the reasoning: task 20 §4.
- `model` — the model name sent on every request, recorded in the summary.
- `timeouts` — `model_call_ms` bounds one judge or writer call; a call past it
  is one recorded lapse. `term_grace_ms` bounds every wait on the actor: how
  long it gets to exit on its own once its stdout has closed (or to close
  its stdout once its leader has exited), how long its process group gets to
  honour `SIGTERM` before `SIGKILL`, and how long a write on its stdin — the
  prompt, or a correction — may make no progress before the wrapper gives up
  on it.
- `limits` — `max_event_line_bytes` is the ceiling on one line of actor
  stdout. Framing uses a growable buffer up to it; a longer line is still
  written to the event log verbatim but reaches no judgment, and the summary
  counts it. `max_actor_stdout_bytes` and `max_actor_stderr_bytes` cap the
  two actor logs (and the first the supervisor log too, see Artifacts),
  exact to the byte: a record that would cross the cap is not
  written (an oversized one already begun is rolled back whole), the stream
  is not read further, and the run is ended and reported as not accounted
  for. The event log is the actor's stdout byte for byte, a last line left
  unterminated included. Without them an actor that never stops writing fills
  the sandbox before the summary can be written. The wrapper's own memory is
  bounded independently: one line up to the ceiling, at most 16 lines
  queued ahead of the loop, the policy's `window` of admitted records and
  no more, and a rendered prompt bounded by its budget — records the budget
  cannot reach are omitted under one line that says how many.

## Environment

Two variables, read by the binary in-process and removed from the actor's
environment before it is launched. They travel into the sandbox the way the
actor's own `ANTHROPIC_BASE_URL` and token do — by reference, never on a
command line.

| Variable | Meaning |
| --- | --- |
| `SWE_LAB_SUPERVISOR_BASE_URL` | **Required.** The base URL of an OpenAI-shaped chat-completions API, `http://<loopback ip>:<port>/v1`; the binary appends `/chat/completions`. **Plain HTTP, to loopback, by number** — `https://` is refused with the reason (the binary carries no TLS), and so is any hostname, `localhost` included, or any address that is not loopback: the binary sends a bearer token in clear, and it speaks to a loopback forwarder in the sandbox (the `cc-reverse-proxy` instance the Python side starts with `--target https://openrouter.ai/api`), which terminates TLS and forwards the bytes unchanged. A hostname would be resolved, and what it resolves to is the box's business; a numeric loopback address is checked on the spot, so no stray environment variable can point a request carrying `Authorization` off the box. |
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
