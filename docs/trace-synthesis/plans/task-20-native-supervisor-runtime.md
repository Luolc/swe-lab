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

- **In this repository**, under `rust/`, beside the Python `src/`. The parity
  fixtures, the Python consumer of the terminal summary and CI all live here;
  a second repository would buy coordination cost and nothing else.
- **Pure-Rust dependencies only.** The artifact is a static
  `x86_64-unknown-linux-musl` binary linked self-contained by the toolchain;
  one crate that compiles C would add a musl C toolchain to the build surface.
  So there is no TLS in the binary — both mainstream TLS stacks carry C and
  assembly — and the model endpoint is plain HTTP, terminated outside the
  binary (the actor's own calls already go to a loopback proxy via
  `ANTHROPIC_BASE_URL`; the supervisor's endpoint follows the same pattern).
  Should a deployment turn out to need the binary to dial a provider
  directly, that is an ADR — it changes the dependency shape and the
  deployment contract together — not a `Cargo.toml` edit.
- **Static is a structural property**, checked in CI as the absence of a
  `PT_INTERP` program header. Not a word grepped out of `file` or `ldd`: a
  modern musl build is `static-pie linked`, and the wording moves.
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
- **`policy.block_actor_while_judging` is a required boolean.** Blocking and
  the stale gate are two answers to the same lag; a run says which it uses.
  Off, the actor runs ahead and overtaken verdicts are discarded. On, the
  wrapper stops reading the actor's stdout for the duration of a judgment
  (§4). Neither is a default.

`model.api_key_env` is optional (a local stub needs no credential); it names
an environment variable, never carries a value. The credential is read
in-process and split on commas there — the Python side's rule, kept.

## 3. Judgment boundaries under batching

- **A boundary falls at every `N`-th admitted assistant message**, counted
  from the last boundary, **and at every actor `result` event that has new
  admitted evidence behind it** — the turn's end is the last moment a
  correction can reach the actor before its stdin decides the run's fate, so
  the partial batch is judged rather than dropped. A `result` with nothing
  new since the last judgment is not judged again on an identical window.
- **`N = 1` is "every assistant message", not "every event".** The Python
  runtime consults the policy at every stream event; no `N` reproduces that,
  and it is not meant to — every-event judging is what #375 §2 batches away.
  Parity fixtures therefore cover the filter, the prompt bytes, the gates and
  the classifications, not the boundary set.
- **`cooldown` is counted in judgment boundaries.** The Python docstring says
  "how many boundaries must pass between interventions" and implements it as
  a cursor difference, which under every-event judging is the same thing.
  Under batching the two diverge, and the docstring's meaning is the one
  kept: a boundary is a judgment, whatever its outcome (a lapse or a stale
  verdict still passes).
- **The empty-window invariant holds ahead of the gates** — a boundary whose
  window holds no admitted evidence consults no judge and is recorded as
  `unjudged`, the same word and the same reason as Python's `Unjudged`
  ([#379](https://github.com/Luolc/swe-lab/pull/379)), and it has the same
  named test.

## 4. Blocking the actor: the absence of a read

The first mechanism of #375's first comment: **stop reading the actor's
stdout.** The pipe fills, the actor's next write blocks, resuming the read
releases it. Nothing to time out, no signal to get wrong, and it self-releases
if the wrapper dies. `SIGSTOP` / `SIGCONT` on the process group is not
implemented in the first release: it is a real state the wrapper would have to
guarantee it exits, and no run has yet needed the precision. The reader
thread's gate is the seam; a second mechanism would sit behind it.

Two facts about the mechanism's reach are recorded rather than assumed:

- The pipe buffer (64 KiB on Linux) and whatever the reader had already
  pulled in are processed before the actor stalls, so a judgment started at
  a boundary can still be overtaken by a few events. The stale gate (§5) is
  what makes that safe; blocking narrows the window, it does not close it.
- Whether a *given actor* actually stalls on a full stdout pipe depends on
  how it writes. A synchronous write does; a runtime that queues writes in
  memory continues working. The fake actor in the crate's tests writes
  synchronously and proves the wrapper's side; **whether Claude Code's stdout
  writes stall on a full pipe is an empirical question about the actor**, to
  be read off a real run before a deployment relies on blocking alone.

## 5. Freshness: one judgment in flight, latest value wins

- At most one judgment is in flight. While it runs, the event loop keeps
  consuming, filtering and recording. Just before a correction is written,
  the judgment's evidence revision is compared with the current one: equal,
  deliver; newer admitted evidence, record `stale` and neither deliver nor
  spend budget. Events the filter excluded do not bump the revision.
- A boundary that falls while a judgment is in flight is recorded as
  `unjudged` (reason: in flight) and marks a pending latest boundary; when
  the judgment completes, one judgment starts on the *current* snapshot — not
  one per skipped boundary. A latest-value channel, as #375 says, not a
  queue of prefixes.
- **Parallel judgments are not built**: response completion order would
  become an unstated policy.

## 6. The account and the terminal summary

`supervisor.jsonl` keeps Python's row shape (`cursor`, `at`, `policy`,
`kind`, `evidence`, and per-kind fields) and its kinds — `spoke`, `silent`,
`unjudged`, `lapse`, `gap` — plus two the wrapper needs: **`observed`** for an
event consumed at which no decision was sought (batching makes these the
majority), and **`stale`** for §5. A boundary's row is written when its
judgment completes, carrying the boundary's cursor and `decision_lag_ms`, so
rows are in time order, not cursor order.

The terminal summary is the shape in #375 (schema-versioned, written to a
temporary name and renamed), and it is what the Python side classifies from:
`accounted_for` is false on any `gap`, on an unclean wrapper ending, or when
no usable actor event was ever consumed (Python's `saw_events`). The lapse /
gap line of v0.3.0 is kept exactly: a bad judge or writer answer, a call past
`model_call_ms`, or a line the intervention cap refuses, is one **lapse**; a
failed actor-stdin write, a broken policy state or an unclean ending is a
**gap**.

## 7. Three measured defects, carried across visibly

All three come from the replay experiment and none has an ADR yet. The rule
applied: reproduce the Python semantics where they are semantics (or parity
has no meaning), isolate each behind one named place, and point at the issue
so that the ADR changes one thing.

1. **[#383](https://github.com/Luolc/swe-lab/issues/383) — `max_tokens = 512`
   on the judge is inside the reasoning distribution.** A constant, not a
   semantic; fixed directly. The judge's ceiling clears the measured
   distribution (successful calls: median 89, max 441 reasoning tokens; every
   lapse: 512) with margin, and the constant's comment says so. The writer's
   256 stays: no writer lapse was measured. `finish_reason` is recorded on
   every call row, which is the issue's third candidate — it changes what the
   record says, not what the judge is.
2. **[#380](https://github.com/Luolc/swe-lab/issues/380) — the renderer emits
   only text blocks**, so admitted tool results render empty. Reproduced, in
   one named rendering function that the ADR will replace.
3. **[#381](https://github.com/Luolc/swe-lab/issues/381) — the supervisor's
   own corrections enter the judge's prompt** and drive its verdicts.
   Reproduced, behind one named function that decides what the judge is told
   it has already said.

## 8. Out of scope here

The Python side of the migration — `NativeSupervision`, the `AgentAsset`,
the harness argv handoff, `capture="stream_replay"`, the summary consumer and
the removal of the host runtime — and the parity fixtures. Those are their
own tasks; this one is the binary, its tests, and its CI.
