# Task 01 — One instance, end to end

**Status lives in [`README.md`](README.md)**, and so does the acceptance —
[the seven points](README.md#task-01-one-instance-end-to-end), each naming what
proves it. This file is the **design**: what runs, in what order, which seam
each stage sits on, and which of them do not exist yet.

Supersedes [`task-01-one-instance-end-to-end.md`](task-01-one-instance-end-to-end.md),
which designed the terminated hint-injection arm around a person hand-steering a
re-run. The deliverable now is a **pipeline that runs unattended**, on the stdin
channel of [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md).

## What the task is

One rollout of one real instance in which **every stage actually runs**:
supervisor attached to the live stream, a correction delivered mid-turn, the
patch taken against the pre-agent baseline, grading run on it, and the whole
thing recorded so a reader can check each stage happened rather than assume it.

It is not an effect estimate. A supervised rollout that fails to resolve is a
**complete result** — what would make it incomplete is a stage nothing can
demonstrate. The effect measurement belongs to a downstream consumer, not to
this repo, which runs only a small stability batch. Its size is set by owner
ruling and is not restated here — one fact, one home.

## The stages, and the seam each sits on

The pipeline is the existing `rollout_and_unit_test` workflow
([`workflow/definitions.py`](../../../src/swe_lab/workflow/definitions.py)) with
one thing added to its first entry. Nothing about the second entry changes.

| # | Stage | Where it lives | State |
|---|---|---|---|
| 1 | Instance → `SandboxSpec` | the dataset's `TaskInstance` | exists |
| 2 | Actor runs under `CodingAgentTask` | [`rollout.py`](../../../src/swe_lab/rollout.py) | exists |
| 3 | **Actor's live events reach a supervisor** | `SupervisedRun` in [`trace_synthesis/channel.py`](../../../src/swe_lab/trace_synthesis/channel.py), composed by `CodingAgentTask.supervision_factory` | exists |
| 4a | The **seam** a policy plugs into | the `SpeakPolicy` protocol in [`trace_synthesis/supervisor.py`](../../../src/swe_lab/trace_synthesis/supervisor.py) | exists |
| 4b | A policy that speaks **because of a real deviation** | `SpeakWhenOffTrack` in [`trace_synthesis/supervisor.py`](../../../src/swe_lab/trace_synthesis/supervisor.py) | **the policy ships; the `Judge` and `Writer` it consults do not** — both are protocols with no implementation |
| 5 | **Intervention reaches the actor's stdin** | the FIFO and in-sandbox relay behind `ClaudeCodeHarness(correction_channel=True)` | exists |
| 6 | Patch extracted vs the pre-agent baseline | `DiffExtractObserver(baseline=True)`, [ADR-0014](../../decisions/ADR-0014-the-pre-agent-baseline-is-the-default.md) | exists, default on |
| 7 | Grading on that patch and that base ref | `UnitTestTask`, same base-ref contract | exists |
| 8 | Outcome word + record | `rollout_outcome`, [ADR-0015](../../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md) | exists |

**Stage 4b is the remaining work.** 3 and 5 are two different seams — one
reads, one writes — and conflating them is how a supervisor that "is attached"
ends up never able to say anything; they are built as two, and both are in
place.

**4b is a prerequisite, not a detail of 4.** The policy that speaks on a
deviation is `SpeakWhenOffTrack`, and it is shipped — but it consults a `Judge`
and a `Writer`, and neither protocol has an implementation, so it cannot be
constructed into something that runs. The two policies that *can* be
constructed today are both disqualified by construction: `NeverSpeak` is the
**control arm** and never speaks, and `SpeakAt` speaks on a schedule, which
acceptance point 3 excludes. So wiring stages 3 and 5 yields a pipeline
provably complete on six of the seven points, with the seventh waiting on a
judge and a writer rather than on a policy design.

### Stage 3 — reading the live stream

The harness already runs the actor with `--output-format stream-json --verbose`
redirected to an event-stream file
([`claude_code/harness.py`](../../../src/swe_lab/harnesses/claude_code/harness.py)).
That file is written **inside the sandbox**, which is why this is not merely
"parse the file afterwards": the supervisor has to see events while the actor is
still running, and `Supervisor.observe()` is already shaped for exactly that —
one event in, an `Intervention | None` out.

The design constraint that outranks convenience: **the supervisor's evidence is
built only from what the actor produced.** `EvidenceFilter` already enforces it
(`ADMITTED_ASSISTANT` / `ADMITTED_TOOL_RESULT`, with the supervisor's own words
excluded as `EXCLUDED_OWN_INTERVENTION`), so the wiring must feed it raw events
and must **not** pre-filter them into something more convenient — a filter in
two places is a barrier in neither.

### Stage 5 — writing to stdin

ADR-0013 puts the correction on the actor's stdin as a stream-json message. Two
consequences the wiring must respect, both already stated by the component:

- **The sink is borrowed, never owned.** Closing stdin is how the CLI
  terminates, so the process owner closes it — the supervisor only writes.
- **The correction must be byte-identical to the shape already measured**, and
  the in-sandbox fold check is task 05's acceptance condition, not this task's.
  This task *consumes* that result; it must not re-measure it and quietly accept
  a different shape.

## Order of operations for the run

1. Pick the instance from the candidates measured in
   [issue #261](https://github.com/Luolc/swe-lab/issues/261) — the one piece of
   the superseded record that carries over unchanged.
2. Run `swe-lab run rollout_and_unit_test` for that instance with the supervisor
   configured and a policy that can actually fire (`NeverSpeak` proves the
   plumbing and **fails point 3** by construction).
3. Read the seven points off the persisted record and artifacts. Each is a check
   against a file on disk, not a judgement about the run.
4. Write the [experiment](../../experiments/playbook.md) `REPORT.md`.

## What this task must not do

- **Not** measure whether supervision helps. Two rollouts cannot, and framing
  the result that way is the failure ADR-0015 exists to prevent.
- **Not** re-implement the information barrier. It is task 05's, consumed here.
- **Not** accept a run whose only utterances are scheduled. `SpeakAt` is a knob
  for tests; a run that speaks on a timer proves the plumbing and says nothing
  about a policy, which is why acceptance point 3 excludes it.

## Dependencies

Three, and the first is easy to miss because its *seam* is already there:

1. **A `Judge` and a `Writer` for `SpeakWhenOffTrack`** (stage 4b). The policy
   is on `main`; both collaborators it consults are protocols with no
   implementation, so the only constructible policies are the two acceptance
   point 3 excludes.
2. **The wiring** — stages 3 and 5, reading the live stream and writing to
   stdin.
3. **The pinned criterion sha and its refusal path**, for acceptance point 2b.
   A `Criterion` type is on `main`; what is missing is a builder that verifies
   the artifact's sha and a **caller** for it, since "the run refuses to start"
   is only testable where the run is constructed.

The wiring is what the rest hangs off: the first and third are consumed by it,
so neither can be shown to refuse or to speak until it exists.
