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
| 4b | A policy that speaks **because of a real deviation** | `SpeakWhenOffTrack`, built by `supervising_policy` over `ModelJudge` / `ModelWriter` | exists; composed by the `supervised_rollout_and_unit_test` definition |
| 5 | **Intervention reaches the actor's stdin** | the FIFO and in-sandbox relay behind `ClaudeCodeHarness(correction_channel=True)` | exists |
| 6 | Patch extracted vs the pre-agent baseline | `DiffExtractObserver(baseline=True)`, [ADR-0014](../../decisions/ADR-0014-the-pre-agent-baseline-is-the-default.md) | exists, default on |
| 7 | Grading on that patch and that base ref | `UnitTestTask`, same base-ref contract | exists |
| 8 | Outcome word + record | `rollout_outcome`, [ADR-0015](../../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md) | exists |

**No stage is missing any more; what is missing is a run.** 3 and 5 are two
different seams — one reads, one writes — and conflating them is how a
supervisor that "is attached" ends up never able to say anything; they are
built as two, and both are in place. A stage existing is not the same as a
stage having run, which is the whole of what task 01 still owes.

**4b is a prerequisite, not a detail of 4.** The policy that speaks on a
deviation is `SpeakWhenOffTrack`, built by `supervising_policy` over a
`ModelJudge` and a `ModelWriter`. The two shipped supervised definitions run
the *same* policy on the same criterion and differ in one number: the control's
budget is zero. The budget gates speech and never gates judgement
([task 05 §4.4](task-05-supervisor-the-component.md)), so both arms consult the
judge at every boundary carrying evidence and record what they would have said
(a boundary whose evidence window is empty is judged in neither arm — task 05
§4.3). What the arms are
and are not matched on is stated once, beside the two definitions, at
`workflow.definitions.CONTROL_BUDGET` — read it there rather than here.
`SpeakAt` remains a knob for tests — a run whose utterances are scheduled cannot satisfy acceptance point 3.

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
2. Run `swe-lab run supervised_rollout_and_unit_test` for that instance;
   `control_rollout_and_unit_test` is the paired arm, and a run of it **fails
   point 3** by construction — it judges every boundary it has evidence for and
   has nothing left to spend.
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

All three are on `main`: the policy with a `Judge` and a `Writer` to build it
(stage 4b), the wiring in both directions (stages 3 and 5), and the criterion's
digest check with a caller on the run's own construction path (point 2b). Each
of the three was consumed by the wiring, which is why none of them could be
shown to refuse or to speak until it existed.

What is left is not a dependency but the run: `swe-lab run
supervised_rollout_and_unit_test <instance>` for the treatment arm and
`control_rollout_and_unit_test` for the control.
