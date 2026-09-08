# ADR-0025: The segment loop is the only supervised carrier

## Status

Accepted. **The decision is the owner's, taken on 2026-09-08**: only the
segment-loop supervised route survives, every other supervisor carrier goes,
and the reason given is Occam — the simplest code that runs the segmented route
is what they want to read. Nothing below adds a reason of its own; what this
record contributes is the inventory of what the ruling removes and the boundary
between what this PR did and what the follow-up does.

**This ADR supersedes no accepted ADR.** The native carrier it removes never
had one: its design of record is
[issue #375](https://github.com/Luolc/swe-lab/issues/375) and
[task 20](../trace-synthesis/plans/task-20-native-supervisor-runtime.md) /
[task 21](../trace-synthesis/plans/task-21-native-supervision-the-python-side.md),
which are plans, not decisions. Two accepted records are nonetheless touched by
the ruling, and each in its own way:

- [ADR-0024](ADR-0024-the-judge-is-not-told-what-the-supervisor-said.md) carries
  clauses about the native runtime — a rejected alternative that would have
  fixed the said-visibility shape there only, and an open item asking for a
  config key so a native arm could run the three modes. Neither survives the
  removal, and both are a **minor** delta to a decision that otherwise stands
  unchanged, so ADR-0024 gains a dated amendment in the same PR rather than
  being superseded.
- [ADR-0013](ADR-0013-supervision-on-the-stdin-channel.md) decides that
  supervision is delivered on the harness's own stdin channel. The ruling
  retires that carrier too, but **this PR does not remove it** and this ADR
  therefore does not supersede it: the correction channel is still in the tree,
  and the record that supersedes ADR-0013 is the one that lands with its
  removal. Until then ADR-0013 describes code that exists.

## Context

Three carriers for one supervision stack were built, and each ran the same
policy, judge and writer over a different way of reaching the actor:

| Carrier | How the actor is reached | Record |
|---|---|---|
| The correction channel | a live FIFO on the actor's stdin, written while it works | [ADR-0013](ADR-0013-supervision-on-the-stdin-channel.md), task 16 |
| The native runtime | a static musl binary inside the sandbox that runs the actor as its child and owns its stdio | [#375](https://github.com/Luolc/swe-lab/issues/375), tasks 20 and 21 — no ADR |
| The segment loop | stop the actor every *N* turns, judge, resume with `--resume` | [task 22](../trace-synthesis/plans/task-22-segmented-supervision-loop.md) |

The native carrier was the most expensive of the three to keep. It is a
**two-language contract**: a Rust config parser and a Python renderer that
mirror each other by hand, checked by a round-trip that needs the built binary
to run at all. It carries its own toolchain pin, its own four-gate script, its
own CI job and its own release artifact. And it is a second implementation of a
policy that already exists in Python — divergence from that Python was
deliberate ([#380](https://github.com/Luolc/swe-lab/issues/380),
[#381](https://github.com/Luolc/swe-lab/issues/381),
[#383](https://github.com/Luolc/swe-lab/issues/383)), so the two could not be
checked against each other either.

Every mechanism the three carriers share — the criterion, the judge, the
writer, the speak policy, the evidence window, the running state, the
guidebook, `said_visibility`, the `supervisor.jsonl` account — is host-side
Python that none of them owns. Removing a carrier removes a way of reaching the
actor, not a piece of the supervision.

## Decision

**The segment loop is the supervised carrier of record.** The native runtime is
removed from the repository, in full, together with everything that existed only
to serve it.

Removed by this PR:

| Removed | Lines | Why it went |
|---|---|---|
| `rust/swe-lab-supervisor/` (the whole crate) | 11,949, of which 11,120 are `.rs` | the carrier itself |
| `src/swe_lab/trace_synthesis/native_supervision.py` | 719 | the Python half of the crate's contract |
| `src/swe_lab/trace_synthesis/supervisor_binary.py` | 220 | placed and verified that binary; only the harness imported it |
| `src/swe_lab/trace_synthesis/transcript_marks.py` + its test and fixture | 432 | see below — dead already |
| `tests/test_native_supervision*.py`, `tests/test_supervisor_binary.py`, `tests/fixtures/native_supervision/` | 1,812 | tested only the removed carrier |
| CI's `rust` job, the `rust/**/target/` ignore, the crate's row in the directory map | — | existed only for the crate |
| `ClaudeCodeHarness.native_supervision` and the `supervisor_proxy_*` field family; the wrapper's flag map, its command builder and its start-up probe; the second capture-proxy instance | — | the harness's half of the wiring |
| `NATIVE_SUPERVISED_ROLLOUT`, `NATIVE_SUPERVISED_ROLLOUT_AND_UNIT_TEST`, their registration and `NATIVE_JUDGE_EVERY_N` | — | the only shipped way to ask for the carrier |

`transcript_marks.py` is a separate finding that this deletion surfaced rather
than caused: **nothing under `src/` imported it.** Its only importer was its own
test, which is why a green suite never noticed — a module kept alive by the
thing that was supposed to be checking it. It went with the rest because the
same pass is the moment it was found, not because the native carrier used it.

**Kept, and worth naming, because each looks removable and is not:**

- `src/swe_lab/trace_synthesis/vocabulary.py` — the metric and artifact names.
  Both surviving carriers report under them: the correction channel through
  `channel.py`, and the segment loop through the harness, which writes
  `supervisor.jsonl` itself. Its docstring is reworded here; the module stays
  whole.
- The `capture="proxy"` machinery, including the in-sandbox capture proxy. The
  segmented route needs it — the wire is what the seam guard reads. Only the
  *second* proxy instance, the one that terminated TLS for the wrapper, is gone,
  and `_proxy_start_lines` loses the `name` / `label` parameters that existed
  solely to keep two instances apart.
- `criterion.py`, `judge.py`, `supervisor.py`, `segmented_loop.py`,
  `channel.py` — all host-side supervision, none of it the native carrier's.

## Alternatives Considered

### Keep the crate beside the loop, unwired

Rejected. An unwired carrier is not free: it keeps the CI job, the toolchain
pin, the release artifact and the hand-mirrored config contract, and it keeps
every future change to the shared policy asking whether the Rust half needs the
same edit. The cost the ruling is about is the cost of reading and maintaining
it, which unwiring does not reduce.

### Move the crate to its own repository

Rejected here, and not by this record's authority: it is a decision about where
code lives, which the owner has not been asked. The history is not lost — the
crate is recoverable from git at `9f39348`, and this ADR names that commit so
nobody has to go looking.

### Delete the native carrier and leave the correction channel

This is what actually happened, and it is a staging decision rather than an
alternative to the ruling. Two removals in one PR would put ~15,000 deleted
lines and two independent couplings in front of one reviewer; the channel's
removal is its own change, with its own record superseding ADR-0013.

## Consequences

- **`native_supervised_rollout_and_unit_test` no longer exists.** A caller
  naming it gets "unknown workflow". No run has ever used it, and the evidence
  is the experiment written to be its first one:
  `experiments/trace_synthesis/first_native_supervised_rollout/README.md` still
  reads *"Status: criteria frozen, not yet run."*
- **Frozen experiments that mention the native runtime stay exactly as they
  are.** `experiments/trace_synthesis/first_native_supervised_rollout/` and the
  reports that reference the runtime are accurate history of what was planned
  and measured; a record edited to match today's tree is no longer evidence.
- **CI has one job behind the required `check`.** The aggregating job is
  unchanged and still exists for the reason it always did — the required status
  keeps its name however the work is split.
- **The `no-stale-module-refs` hook gained the removed names**, so
  `native_supervision`, `supervisor_binary`, `transcript_marks` and their
  symbols cannot return to `src/`, `tests/` or `experiments/` Python without
  failing the gate.
- **One spec invariant lost half its evidence and got it back.** The
  trace-synthesis spec's *"running state creates no separate sandbox artifact"*
  row cited a deleted test for the exact-artifact-set half. The segmented run's
  own test now asserts that set exactly rather than by membership, and the spec
  cites it — the invariant is pinned by a test that can still go red, rather
  than downgraded.
- **What remains to do is the second stage**: removing the correction channel,
  the streaming `Supervisor` and the classes around it, and superseding
  ADR-0013. Until that lands, this repo has two supervised carriers in the tree
  and one of record.
