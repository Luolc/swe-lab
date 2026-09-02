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
| 3 | **Actor's live events reach a supervisor** | the harness's event stream | **missing — the wiring** |
| 4 | Policy decides whether to speak | `SpeakPolicy` in [`trace_synthesis/supervisor.py`](../../../src/swe_lab/trace_synthesis/supervisor.py) | exists (`NeverSpeak` is the only implementation) |
| 5 | **Intervention reaches the actor's stdin** | the harness's invocation | **missing — the channel** |
| 6 | Patch extracted vs the pre-agent baseline | `DiffExtractObserver(baseline=True)`, [ADR-0014](../../decisions/ADR-0014-the-pre-agent-baseline-is-the-default.md) | exists, default on |
| 7 | Grading on that patch and that base ref | `UnitTestTask`, same base-ref contract | exists |
| 8 | Outcome word + record | `rollout_outcome`, [ADR-0015](../../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md) | exists |

**Stages 3 and 5 are the whole of the remaining work**, and they are two
different seams — one reads, one writes. Conflating them is how a supervisor
that "is attached" ends up never able to say anything.

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

Task 05's `SpeakPolicy` — present. What is genuinely blocking is the wiring
(stages 3 and 5) and, for acceptance point 2b, the criterion artifact's pinned
sha and its refusal path, neither of which exists today.
