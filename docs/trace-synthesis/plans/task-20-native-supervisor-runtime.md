# Task 20: The native supervisor runtime — a static binary that wraps the actor

**The design of record is [issue #375](https://github.com/Luolc/swe-lab/issues/375)**,
including its three follow-up comments (the blocking mechanism and the
batching requirement; the `N = 5 / 6` correction; the withdrawal of the
evidence-count analysis). This document does not restate it. What it holds is
the set of decisions the implementation had to make where #375 leaves a
choice open, so that each is written down once, where a reviewer of the crate
can find it. The code is [`rust/swe-lab-supervisor/`](../../../rust/swe-lab-supervisor/);
its README owns the CLI, the config schema and the build.

Status lives in [`README.md`](README.md), not here.

## 1. Where it lives, and what it may depend on

- **In this repository**, under `rust/`, beside the Python `src/`. The
  criterion artifact, the Python consumer of the terminal summary and CI all
  live here; a second repository would buy coordination cost and nothing
  else.
- **Pure-Rust dependencies only.** The artifact is a static
  `x86_64-unknown-linux-musl` binary linked self-contained by the toolchain;
  one crate that compiles C would add a musl C toolchain to the build surface.
  So there is no TLS in the binary — both mainstream TLS stacks carry C and
  assembly — and the model endpoint is plain HTTP on loopback: a second
  `cc-reverse-proxy` instance in the sandbox, started by the Python side with
  `--target https://openrouter.ai/api`, terminates TLS and forwards the bytes
  unchanged (its log redacts the credential header). The actor's own calls
  already go through the first instance via `ANTHROPIC_BASE_URL`; the
  supervisor's follow the same pattern. `https://` is refused by the binary,
  and that refusal guards the invariant, not a stopgap: the binary only ever
  speaks to loopback. Should that change, it is an ADR — dependency shape and
  deployment contract move together — not a `Cargo.toml` edit.
- **Where the model is and how to authenticate are environment variables**
  (`SWE_LAB_SUPERVISOR_BASE_URL`, `SWE_LAB_SUPERVISOR_API_KEY`), passed into
  the sandbox by reference the way the actor's own are, read in-process, and
  removed from the actor's environment before it is launched. The config
  file carries neither: it is non-secret run settings only.
- **Static is a structural property**, checked in CI as the absence of a
  `PT_INTERP` program header — after the artifact is proven to exist and to
  be an ELF with a program-header table, because a missing file also has no
  `PT_INTERP`. Not a word grepped out of `file` or `ldd`: a modern musl build
  is `static-pie linked`, and the wording moves. The pure-Rust tree is
  checked the same way, by name in `cargo tree`, after `cargo tree` is shown
  to have listed the crate itself; "builds without `musl-tools`" is not a
  test of it, because rustc still names the link driver `cc` for the musl
  target and a box without gcc fails both ways.
- **The criterion is compiled in from the Python side's own artifact file**
  (`include_str!` of `src/swe_lab/trace_synthesis/criteria/general-practice.md`),
  so the two runtimes hold byte-identical text by construction, and a config
  pins the digest the binary must reproduce before the actor is launched.
- **Toolchain and lint conventions follow `locode-core`**, the owner's other
  Rust project: a pinned toolchain in `rust-toolchain.toml`,
  `unsafe_code = "forbid"`, clippy `pedantic` with `unwrap`/`expect` denied
  outside tests, the four CI gates (fmt, clippy `-D warnings`, test, doc
  `-D warnings`). `print_stdout` / `print_stderr` are denied too, with narrow
  allows in `main.rs`: the wrapper relays the actor's stdio, so nothing else
  in it may print into a stream by accident.

## 2. The config: every policy value is a run's choice

`schema_version`, `task`, `criterion`, `policy`, `model`, `timeouts`,
`limits` — the shape in the crate README. Two things are decided here rather
than in #375:

- **`policy.judge_every_n_assistant_messages` (the `N` of #375 §2) is
  required and has no default.** #375's second comment measured that `N` and
  `window` are coupled on the one corpus, and its third comment withdrew that
  analysis (the quantity it counted is mostly invisible to the judge, #380).
  Whether they are coupled is open; what is settled is that neither may be
  chosen silently by a binary. The replay experiment
  ([`experiments/trace_synthesis/n_batching_replay/`](../../../experiments/trace_synthesis/n_batching_replay/REPORT.md))
  measured no effect of `N` — and found why it could not (#381).
- **`policy.block_actor_while_judging` is required, and three-way.** Blocking
  and the stale gate are two answers to the same lag; a run says which it
  uses. `off`: the actor runs ahead and overtaken verdicts are discarded.
  `stdout`: the wrapper stops reading the actor's stdout for the duration of
  a judgment. `sigstop`: the wrapper stops the actor's process group and
  resumes it. None is a default in the binary; **`sigstop` is the one a run
  should choose**, for the reason measured in §4.
- **`task` is the judge's, and the actor's prompt is a file.** The config's
  `task` is the goal statement quoted to the judge and the writer. The actor's
  prompt — `instance.prompt()`, long, with repository detail and format
  requirements — is a file the wrapper is given as `--actor-prompt`, writes
  on the actor's stdin byte for byte before anything else, and never reads.
  Binding the two would mean that editing what the judge measures against
  changes what the actor was told, a semantic error, not an inconvenience.
  Forwarding the wrapper's own stdin instead would tie the actor's stdin to a
  file's end, when its closing is the loop's decision (§3, ending); the
  wrapper's stdin plays no part.

`model` carries only the name. The endpoint and the credential are the
environment's (§1); a local stub needs no credential, and the binary sends
no `Authorization` header when the variable is unset.

## 3. Judgment boundaries under batching

This section is the one definition of a boundary — of what
`summary.boundaries` counts; the crate's module docs and README point here.

- **A boundary falls at every `N`-th admitted assistant message**, counted
  from the last boundary, **and at every actor `result` event that has new
  admitted evidence behind it** — the turn's end is the last moment a
  correction can reach the actor before its stdin decides the run's fate, so
  the partial batch is judged rather than dropped. A `result` with nothing
  new since the last judgment is not judged again on an identical window.
- **The one exception: a `result` is covered by a boundary that has yet to
  take its snapshot.** A boundary pending behind the judgment in flight, or
  one waiting for the `sigstop` barrier (§4), judges the current snapshot
  when its turn comes — the evidence the `result` closes is in it — so the
  `result` is no boundary of its own and is not counted. Counting it would
  count a judgment point that does not exist: every boundary counted is a
  point at which the policy was asked, or a point on record for why it was
  not (§5 has the mechanics).
- **`N = 1` is "every assistant message", not "every event".** Events that
  are not admitted assistant messages — the actor's echo of a user turn,
  system events, a `result` — count towards no boundary; every-event judging
  is what #375 §2 batches away, and no `N` reproduces it.
- **`cooldown` is counted in judgment boundaries.** A boundary is a
  judgment, whatever its outcome — a lapse, a silent verdict or a stale one
  all pass — and `cooldown` is how many of them must fall between two
  deliveries. The budget, by contrast, is spent only on delivery: a stale
  verdict costs nothing.
- **The empty-window invariant holds ahead of the gates** — a boundary whose
  window holds no admitted evidence consults no judge and is recorded as
  `unjudged` (`policy::tests::an_empty_evidence_window_never_consults_the_judge`
  is the named test; the reason it was first stated is
  [#379](https://github.com/Luolc/swe-lab/pull/379)).
- **A boundary and a judgment are one thread apart.** The loop owns the
  evidence, the policy state, the actor's stdin and the log; a judgment is a
  pure function of a snapshot (window, task, what has been said, the gate
  state) and runs on its own thread, so draining never waits on a model call.
  The two model calls of one judgment happen in order on that thread, and
  neither is retried.

## 4. Blocking the actor: two mechanisms behind one gate

#375's first comment names the first mechanism: **stop reading the actor's
stdout.** The pipe fills, the actor's next write blocks, resuming the read
releases it. Nothing to time out, no signal to get wrong, and it self-releases
if the wrapper dies. The second, `sigstop`, is the exact form: `SIGSTOP` to
the actor's process group when a judgment starts, `SIGCONT` when it completes
— and on every other path out of the wrapper (a failed judgment, a stop signal
to the wrapper, the wrapper's own drop), because a stopped group the wrapper
leaves behind is a hung sandbox. Both sit behind the reader's gate and the
`Actor` handle; a config picks one (§2).

The reach of the first mechanism is measured, not assumed. **The numbers
are of the kernel they were measured on** — the host's 6.17, shared by the
`rust:1.97.1` container; a shell producer writing ~119-byte lines as fast as
it can; three runs, 2026-09-03. Another kernel, or a pipe whose capacity was
tuned, gives other numbers; what they quantify does not change.

| What still lands after the gate closes | Events | Bytes |
| --- | --- | --- |
| Frames the reader had already pulled in (the rest of its 64 KiB read chunk) | 35 – 453 | 4 – 54 KiB |
| What the actor can still write before it stalls (the default pipe) | 544 | 64.8 – 65.1 KiB |

So a judgment started under `stdout` blocking can be overtaken by up to
~128 KiB of events the judge never saw. The chunk in hand lands during the
judgment and is caught by the freshness check (§5). The pipe's content lands
when the gate reopens — which is *after* the freshness check, because the
loop opens the gate and compares revisions in the same step — so up to one
pipe of events written during the judgment is neither in the window nor
counted against the verdict. Under `sigstop` the judge is not started until
the stop is confirmed and the reader has caught up: `killpg` names the
members it finds, and a member in the middle of a fork when it is sent has a
child a moment later that the signal never reached, so the group is read
from `/proc` until two looks in a row find the same members with every
thread of each stopped (a look that finds one running sends the signal
again), and then the stdout reader is asked for a barrier, which it reports
behind everything it had to read. The snapshot is taken when the barrier
arrives — a boundary line and whatever the actor wrote in the same breath
are both in it — and the group stays stopped through the freshness check
and the correction's write, so the check is exact: a verdict on a window
the actor had already moved past is discarded, one on the current window is
delivered, and nothing lands in between. A group that cannot be confirmed
stopped within two seconds (a member deep in disk I/O stops only when that
I/O completes) leaves the boundary unjudged, with the reason. That is what
makes `sigstop` the exact form and `stdout` the one for a run where a
stopped actor is unacceptable.

**Decided (2026-09-03): `sigstop` is the mode a run should use; `stdout`
stays as an option with its blind window documented.** The reason is the
mechanism, and the numbers only say how much it costs: the stale gate exists
to confirm, before a correction is delivered, that the evidence the judgment
rests on is still the latest, and under `stdout` whatever sits unread in the
pipe at that moment is invisible to it — the gate passes verdicts it should
have failed, however large the pipe is. Shrinking the pipe (`F_SETPIPE_SZ`)
or draining the residual before the check would narrow that window, not
close it; neither is built, because `sigstop` closes it by construction —
a stopped actor writes nothing, so there is nothing in the pipe the check
cannot see. That is not a loss of precision, it is the gate not holding
under that mode, and the README says so in as many words. The two modes'
real defects, side by side: `stdout` self-releases if the wrapper dies and
has the blind window above; `sigstop` sees everything the actor wrote and
leaves the actor stopped if the wrapper dies during a judgment — `SIGCONT`
on every path out and the handle's drop backstop are the mitigation, not a
proof.

Whether a *given actor* actually stalls on a full stdout pipe depends on how
it writes. A synchronous write does; a runtime that queues writes in memory
continues working. The fake actor in the crate's tests writes synchronously
and proves the wrapper's side; **whether Claude Code's stdout writes stall on
a full pipe is an empirical question about the actor**, to be read off a real
run before a deployment relies on `stdout` blocking alone. `sigstop` does not
depend on it.

## 5. Freshness: one judgment in flight, latest value wins

- At most one judgment is in flight. While it runs, the event loop keeps
  consuming, filtering and recording. Just before a correction is written,
  the judgment's evidence revision is compared with the current one: equal,
  deliver; newer admitted evidence, record `stale` and neither deliver nor
  spend budget. Events the filter excluded do not bump the revision.
- Under `sigstop` a boundary first waits for the reader's barrier (§4); a
  line that arrives before it — including a `result` — is in its snapshot,
  and a `result` among them is covered by it (§3's exception).
- A boundary that falls while a judgment is in flight keeps the ordinal it
  is given and waits; when the judgment completes, its judgment starts on
  the *current* snapshot — not one per skipped boundary, and not a fresh
  ordinal on completion. A further boundary before then supersedes it, on
  record as `unjudged`; a `result` before then is covered by it (§3's
  exception). A latest-value channel, as #375 says, not a queue of
  prefixes. The judge runs on a named thread whose outcome reaches the loop
  whatever happens — a panic reports as such and is unclean — and which is
  joined once it has reported.
- **A cancellation reaches the judge.** The wrapper's stop flag travels with
  the model: every wait on the socket is a slice of at most 100 ms, and a
  call in progress returns as cancelled within that of the stop; the writer
  is not asked after it. On the way out the loop joins the judge and
  settles its word like any other — every call it made on record, a
  correction it wrote recorded `stale` and not delivered, nothing started
  behind it — before the actor is ended and the summary written.
- **Parallel judgments are not built**: response completion order would
  become an unstated policy.

## 6. The account and the terminal summary

`supervisor.jsonl` is one JSON object per line: `cursor` (the actor event the
row is about, counted from 1), `at` (UTC, millisecond ISO-8601), `policy`,
`kind`, `evidence` (the origin filter's disposition of that event), and
per-kind fields. The kinds:

- **`observed`** — an event consumed at which no decision was sought (under
  batching, the majority).
- **`spoke`** — a correction delivered: `boundary`, `marker`, `text`, `calls`,
  `decision_lag_ms`.
- **`silent`** — judged, nothing to say; with `marker` when the judge found a
  deviation and a gate (budget, cooldown) held the correction back.
- **`unjudged`** — a boundary at which no judge was consulted; `reason` is
  an empty window (§3), a boundary superseded before its judgment could
  start (§5), an actor whose group could not be confirmed stopped (§4), a
  judge that could not be started or panicked, or a run that ended first.
- **`lapse`** — a failed or unusable model call, bounded to that boundary:
  `reason`, `calls`.
- **`stale`** — a correction newer evidence overtook: `reason`, `text`.
- **`gap`** — a correction that could not be written: `reason`.

A boundary's row is written when its judgment completes, carrying the
boundary's cursor, so rows are in time order, not cursor order. `calls`
records every model call of that boundary — purpose, the model requested and
the one that answered, `max_tokens` sent, `finish_reason`, the raw answer,
duration — so a ceiling hit, a refusal and an unparseable answer are told
apart from the account alone.

The account is capped at `limits.max_actor_stdout_bytes` — the cap of the
stdout it accounts for, shared on purpose rather than a key of its own that
the Python side would mirror by hand — and a row that would cross it, or a
write that fails, is a fault: the run ends, not accounted for. A supervisor
that has lost its own account would be producing supervision without
evidence.

The terminal summary is the shape in #375 (schema-versioned, written to a
temporary name and renamed), and it is what the Python side classifies from:
`accounted_for` is true only when there was no `gap`, the wrapper ended
cleanly, at least one actor event was consumed, and both logs have a digest
— each read back through the descriptor the wrapper wrote it by, so it is
the digest of the file the wrapper wrote, whatever is at the name by then.
The line between the two failure words: a bad judge or writer answer, a call
past `model_call_ms`, or a line the intervention cap refuses, is one
**lapse** — the next boundary is judged normally; a failed actor-stdin
write, a broken loop state, an account that cannot be kept or an unclean
ending is a **gap** — the run is not evidence about supervision.

## 7. Three measured defects, fixed here rather than reproduced

All three come from the replay experiment
([`n_batching_replay/REPORT.md`](../../../experiments/trace_synthesis/n_batching_replay/REPORT.md)),
and the owner's ruling is that the native runtime does not align with a host
runtime measured to be wrong in three places: no parity fixtures, no seams
reserved for a later ADR, the correct behaviour directly. The two runtimes
therefore diverge on purpose and are not A/B-comparable; #375 removes the
host runtime anyway. The acceptance is not "matches Python" but "is right on
its own": the judge sees what the actor actually did, speaking does not put
the judge into a self-confirmation loop, and a judgment is not swallowed by
a token ceiling.

1. **[#380](https://github.com/Luolc/swe-lab/issues/380) — the judge sees
   tool results.** The host renderer emitted only text blocks, so the 31
   admitted tool results of the first corpus rendered empty and the judge
   held 0 non-empty records when it wrote each correction. The prompt
   renders tool-result content, under an explicit budget — a per-record cap
   and a per-window cap, with a visible truncation marker — since a tool
   result can be a whole file. The values and their basis are in the
   renderer's comment.
2. **[#381](https://github.com/Luolc/swe-lab/issues/381) — what the
   supervisor has said goes to the writer, not the judge.** With the
   `# What you have already said to them` section in the judge's prompt,
   `off_track` went from 6/330 to 219/280 the moment the supervisor spoke.
   The judge is asked about the actor's evidence alone; the writer is told
   what has been said so it does not repeat itself.
3. **[#383](https://github.com/Luolc/swe-lab/issues/383) — `max_tokens` does
   not cut the judgment off.** Every one of the replay's 85 lapses was the
   judge's 512-token ceiling reached while the model was still reasoning
   (successful calls: median 89, max 441 reasoning tokens). The judge's
   ceiling clears that distribution with margin; the constant's comment says
   so. `finish_reason` is recorded on every call, so a ceiling hit is
   distinguishable from an unparseable answer in the account.

## 8. Out of scope here

The Python side of the migration — `NativeSupervision`, the `AgentAsset`,
the second proxy instance and the environment it passes, the harness argv
handoff, `capture="stream_replay"`, the summary consumer and the removal of
the host runtime. Those are their own tasks; this one is the binary, its
tests, and its CI.
