# Oracle-guided trace synthesis — task index

Ordered task index + status for the trace-synthesis component (per the repo's
planning convention: [`spec.md`](../spec.md) = target design, `plans/` = one
deep design per task, indexed here). Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8
(break down if larger).

> [!WARNING]
> **Task 03 is still pending reconciliation** (2026-09-01): it is written
> against hint injection, which was closed by its own pre-registered kill
> condition ([spec §15.1](../spec.md#15-success-criteria)). **Do not start it**
> before [Pending reconciliation](#pending-reconciliation-2026-09-01) at the end
> of this file is resolved. **Task 01 is reconciled** — its acceptance was
> rewritten on the owner's 2026-09-01 ruling; see [Task 01](#task-01-one-instance-end-to-end).

> [!NOTE]
> **Delivery moved off hooks on 2026-09-01**
> ([ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)): a
> correction is written on the actor's stdin, not returned from a hook. **Read
> every passage below that describes a hook channel as a record of the design in
> force when that task was written or run.** The measurements in them stand, and
> so does what the completed experiments found — a finished task's record should
> say what it actually did, not what we would do now. Only **task 05** is
> rewritten, because it is the live one. Where a task's scope and this note seem
> to disagree, the task's own plan wins on **scope** and the ADR wins on
> **attribution**.

**Single source of truth for status:** this table is the *only* live status for
these tasks. Any `plans/task-NN-*.md` that appears later is a point-in-time
**design record** — don't read status from it.

**The ordering principle is the cheapest falsifier first — and which task *is*
the falsifier has moved.** The pipeline still rests on one assumption — *that a
supervisor holding a good guidebook can steer a blind agent to a correct
solution at tool-call granularity* — which the owner ruled on 2026-09-01 is
**taken as given**. What changed is everything downstream of it. Task **02** was
the falsifier while the open question was whether a hook could put a genuine
user turn at a tool boundary; that question is closed twice over — task 02 is
complete, and both its criterion (the wire-level role, replaced by
[(a)/(b)](../spec.md#what-disqualifies-a-trace--the-two-criteria-of-record)) and
its channel (replaced by
[ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)) were
superseded. **Nothing is blocked on it, and a reader should not order work
against it.**

Two things can still kill the idea, and they are ordered by cost:

1. **The in-sandbox fold check**, an acceptance condition of task **05**. Every
   measurement of the stdin channel is host-side; the sandbox runs a pinned
   binary. If the fold differs there, the byte-identity result describes an
   artifact we do not ship — [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
   **refutation** condition. It is one delivery, it is cheap, and it can
   invalidate the long experiment; the long experiment cannot invalidate it.
   **So it goes first.**
2. **Whether supervision raises the resolved rate**, measured over paired arms.
   Expensive, and it decides whether any of this is worth delivering —
   ADR-0013's **retirement** condition.

Task 01 keeps its place at the head for a different reason — it produces the
first real artifacts the later tasks are designed against. The hook-mechanics
research that preceded this index is not a task: its results are recorded in
[`spec.md` §10](../spec.md#10-what-is-measured-about-hooks).

| # | Task | Status |
|---|---|---|
| 01 | **One instance, end to end** — one supervised rollout in which every stage of the pipeline actually runs, and each of the seven acceptance points names what proves it | ⬜ Acceptance rewritten 2026-09-01 (owner ruling); prerequisites are the ones [the design record names](task-01-pipeline-end-to-end.md#dependencies) — a deviation-triggered policy, the live-stream wiring, and the stdin channel. Every prerequisite is now on `main`: the policy (`SpeakWhenOffTrack`), a `Judge` and a `Writer` to construct it with, the channel and its wiring, and the criterion gate on the run's own path. What remains is the run itself — `supervised_rollout_and_unit_test` and its control `control_rollout_and_unit_test` are registered and startable by name, and points 3 and 4 can only be closed by executing one |
| 02 | **Measure the injection shape** — can a hook put a *visibly external* hint at a tool boundary, and does it survive conversion? | ✅ |
| 03 | **Hint log + conversion guard** (pure, tested) | ⚠ ⬜ **proposed for closure** — the task exists only for the terminated arm; see [below](#pending-reconciliation-2026-09-01) |
| 04 | **Oracle analysis task + guidebook schema** — [`task-04-oracle-analysis-task.md`](task-04-oracle-analysis-task.md) | 🔶 Code landed — `OracleAnalysisTask`, the schema check, the one-entry `oracle_analysis` workflow, tests; one live run made — the guidebook it produced failed the schema check on one missing field and awaits a human judgement. Wording follow-up from #276's review (P2, not a task — fold into the next edit of those passages): the design record's rationale and `oracle.py`'s module docstring still use the shorthand "the fix commit is reachable, and the brief says so" / "a run handed the answer" — scoped to phase B, where the purge is off, so consistent with the purge measured in rollouts, but untrue for a dataset that records no fix commit or reference patch, which the task supports — and `datasets/oracle_failures/README.md` lists the delegated gold patch without its when-recorded qualifier |
| 05 | **The supervisor: what it may see, when it speaks, what it may say** — the component that consumes the actor's live output stream and writes a short correction on its stdin; the barrier is a constructor, the trigger is a seam, and the in-sandbox fold check is an acceptance condition — [`task-05-supervisor-the-component.md`](task-05-supervisor-the-component.md) | ⬜ Re-scoped by [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md); the hook-wiring form is retired |
| 06 | **Trace-quality scorer** (decide whether to build) | ⬜ |
| 07 | **The `oracle_guided_trace` workflow + integrity separation** | ⬜ |
| 08 | **Batch run: N instances, measure yield / cost / quality** | ⬜ |
| 09 | **Converge redaction onto one home and publish behind a gate** — the header/body redaction itself shipped with task 10 | ✅ |
| 10 | **Run the capture proxy inside the sandbox** — removes the host port scheme, the firewall dependency and the tailnet exposure | ✅ |
| 11 | **Start from a cached failure** — the `oracle_failures` dataset: a record that delegates the instance and stages the failure, plus the builder from a finished run — [`task-11-oracle-failures-dataset.md`](task-11-oracle-failures-dataset.md) | ✅ First record built locally: the qutebrowser/9ed748ef baseline failure (data gitignored by design) |
| 12 | **Fold a run's outcome over its segments** — `event_stream_outcome` reduces a run to its *last* `result`, so an interrupted or turn-limited segment is invisible behind a later success | ⬜ Registered, not started ([§13.5](../../../experiments/trace_synthesis/streamjson_input/REPORT.md)) |
| 13 | **Confirm the stream-json correction channel in the sandbox** — every measurement of it so far is host-side, against the host `claude` and the host user-level `CLAUDE.md`; the rollout harness runs in a container with a pinned `CLAUDE_CONFIG_DIR` | ⬜ Registered, not started ([§11](../../../experiments/trace_synthesis/streamjson_input/REPORT.md)) |
| 14 | **The channel's edges that a real rollout will hit** — an interjection at turn 40 rather than turn 3, models other than `claude-sonnet-5`, non-text content blocks, and how several queued messages fold | ⬜ Registered, not started ([§11](../../../experiments/trace_synthesis/streamjson_input/REPORT.md), [§14.6](../../../experiments/trace_synthesis/streamjson_input/REPORT.md)) |
| 16 | **A live correction channel in the harness** — stdin from a file to a FIFO, and who may act while `run()` blocks — [`task-16-live-correction-channel-in-the-harness.md`](task-16-live-correction-channel-in-the-harness.md) | 🔶 Channel and wiring landed — the FIFO, the in-sandbox relay, the host-side pump, and `SupervisedRun` as the observer that brackets the blocked `run()` ([§9](task-16-live-correction-channel-in-the-harness.md#9-the-wiring-who-runs-the-supervisor-while-run-blocks)). What no test covers yet is a real actor being corrected mid-run, which is task 01's job and needs a constructible deviation-triggered policy |
| 17 | **A missed reading and a slow actor render identically** — `SupervisorPump.at_rest` is `False` both for "not at rest yet" and for "never saw a `result` event", so an actor that produces events and never emits one leaves `_feed` holding the channel open: the run burns to the wall clock and reports `TIMED_OUT`, which [ADR-0011](../../decisions/ADR-0011-fair-retry.md) charges to the actor, while `supervision_accounted_for` stays true. **Ours, rendered as theirs** | ⬜ **Waits on the first run's reading, deliberately.** Whether it can happen at all is an empirical question about the actor's wind-down under `--input-format stream-json` with a live stdin — [task 16 §8.2](task-16-live-correction-channel-in-the-harness.md) says the actor does not exit but waits for more input, and nothing has observed what it emits first. **The first input is one line: does the first run's event stream contain a `result` event?** Designing a corroborating signal (exit code? stderr?) before that reading is choosing a mechanism for a failure nobody has seen. Not a gate on the first run: the [pre-registration](../../../experiments/trace_synthesis/pipeline_end_to_end/PREREGISTRATION.md#7-failure-classification) §7 already presumes a `TIMED_OUT` is **ours** unless the proxy log shows the actor working to the wall clock, so this failure stays readable — which is the standard a gate has to meet |
| 15 | **Segmentation and interrupt edges** — MCP tool calls vs. the 2.1.246 interrupt claim, `cancel_queued: true`, `--max-turns` above 1, whether a parallel tool batch can be prevented, whether the two interrupt records can be suppressed | ⬜ **Parked, not merely unstarted** — this is the machinery [§14](../../../experiments/trace_synthesis/streamjson_input/REPORT.md) superseded; it becomes live only if segmentation or interrupt returns as a design |
| 18 | **The actor finished its answer, and nobody closed the channel** — observed on the first end-to-end run (2026-09-02, `main` at `3e97442`, actor pinned 2.1.220), read by the run's owner while it was still up: the actor emitted `type=result, subtype=success` at 00:26:19 as the **last line of the event stream, newline-terminated with zero bytes after it** (so not a half-line stranded outside the parser); `claude.event_stream.jsonl` and `claude.proxy.jsonl` then stopped changing in both size and mtime; `/opt/claude-code/claude` was still running under `docker top`, consistent with [task 16 §8.2](task-16-live-correction-channel-in-the-harness.md) — with a live stdin the actor does not exit, it waits for more input; and `corrections/done` did **not** exist, which is exactly `CorrectionChannel.close()` never having completed, with `claude.correction_channel.unclean` still present as the failure-closed marker agrees. The chain is `at_rest` → `_feed` closes the channel → the relay closes the FIFO's write end → the CLI sees EOF and exits, and a missing `done` puts the break **before the second link** | ⬜ **Waits on this run's own diagnostics — the cause is deliberately not written here.** `supervisor.jsonl` and the run's metrics are written in `before_destroy`, so they exist only once the run ends; they are the first input (`supervision.unhealthy`, the lapse count, and the pump's `failure`). Writing a cause before reading them would be choosing an explanation for a failure nobody has diagnosed, and writing a *fix* before that is choosing an implementation for it — the same reason row 17 states |

**Rows 17 and 18 are not part of that set.** Row 17 comes from reading the
pump during review of #348 rather than from the report below, and row 18 from
watching the first run; both are *authorized to start* once that run's records
exist. They are listed with the others only because they, too, are written down
so they are not rediscovered as mysteries.

**Rows 17 and 18 are two members of one family, not one finding.** Row 17 is an
actor that produces events and *never* emits a `result`; row 18 is its
opposite — the `result` arrived, complete and on time, and the channel stayed
open anyway. A corroborating signal that answers "is the actor still working?",
which is the shape row 17's fix would take, says *yes, it finished* here and
changes nothing. Neither row covers the other, and merging them would lose the
half that is left.

**Rows 12–16 are a registration, not a plan.** They come from the measurements
in [`experiments/trace_synthesis/streamjson_input/REPORT.md`](../../../experiments/trace_synthesis/streamjson_input/REPORT.md)
(landed 2026-09-01) and are written down so they are not rediscovered as
mysteries. **None is authorized to start**: the arm they serve is gated on a
compliance test that has not run — whether an actor acts on a mid-turn
correction at all — and engineering for A′ before that gate is a bet. Task 12 is
the exception in kind rather than in status: it is a defect in a shipped
collector, true regardless of which channel A′ ends up using — and task 16
identifies it as a **prerequisite** rather than a neighbour, because a supervised
run produces several `result` events in one process. Task 16 is the other
exception in kind: it is a design document written *because* the gate may fail,
and it authorizes nothing.

---

## Task 01: One instance, end to end

**Description:** One rollout of one real instance in which **every stage of the
pipeline actually runs** — supervisor attached, correction delivered mid-turn,
patch extracted, graded, recorded. The deliverable is a *working pipeline*, not
an effect estimate: how much supervision helps is measured by a downstream
consumer, not here. This repo runs only a small stability batch, whose size is
set by owner ruling. **That size is deliberately not written here**: it has no
committed home yet, and restating a number in two documents is how a fact stops
having one home. It belongs in the handoff note, with its provenance, and this
row will link there once that note lands.

**What this replaces, and why.** The previous acceptance belonged to the
**hint-injection arm**, closed by its own pre-registered kill condition
([spec §15.1](../spec.md#15-success-criteria)), and delivery has since moved off
hooks onto the actor's stdin
([ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)).
That ADR moved the mechanism without rewriting this row — this rewrite is the
missed half of "the PR that outdates a spec reconciles it", done late. It also
supersedes the post-hoc-grader form proposed in
[Pending reconciliation §2](#2-task-01--rewrite-do-not-delete), which was
written for a different arm again; see the note there.

**Acceptance — seven points, each naming what proves it.** A point whose proof
does not exist yet names the obligation on the PR that wires the supervisor,
rather than a mechanism that is not there.

| # | The claim | What proves it |
|---|---|---|
| 1 | The supervisor is attached to the actor's **live** output stream | The rollout entry persists the supervisor's own account, `supervisor.jsonl`. Composition and artifact are pinned by `test_the_rollout_composes_the_supervisor_when_one_is_configured` and `test_the_supervisors_account_of_the_run_is_persisted` — both, because either can hold while the run is unsupervised. What no test reaches is attachment to a **real** actor: every one of them drives a synthetic event stream. |
| 2a | The **barrier holds** on the interface: the supervisor's input carries no gold patch and no hidden tests | Task 05's `test_supervisor_input_carries_no_privileged_field`. **Consumed here, not re-implemented** — a second barrier in this layer would be a second thing to keep true. |
| 2b | The criterion artifact's sha256 is verified, and a mismatch **refuses to start the run** | `test_a_forged_criterion_stops_the_run_before_a_sandbox_exists` drives one attempt of a supervised rollout against a forged artifact and asserts the sandbox was never created — the refusal is a refusal *to start*, not a late abort that has already paid for a container. That the gate is on the path a command takes, rather than on a composition written by the test, is pinned by `test_the_shipped_supervised_arm_carries_the_pinned_criterion`. |
| 3 | The policy speaks at least once **because of a real deviation** | The supervisor's persisted log — `supervisor.jsonl`, one row per event consumed — records `policy` on every row, so a `kind: "spoke"` row names what produced it, and `supervision.corrections` counts them on the record. `SpeakAt` is a knob for tests: **a run whose only utterances are scheduled does not satisfy this point.** Not satisfied, and nothing in the code blocks it any more: `supervised_rollout_and_unit_test` runs `SpeakWhenOffTrack` over a real judge and writer, so this point turns on executing it and reading the log. |
| 4 | The correction arrives **mid-turn**, in the wire shape already measured | The run's capture artifact shows the injected block as the last `role: system` message before the actor's next action, matching the in-sandbox fold check (block byte-identical, 4 system-reminder blocks). |
| 5 | The rollout completes, the patch is taken **against the pre-agent baseline**, and grading runs | `patch_base_ref` present in the rollout record (baseline mode, [ADR-0014](../../decisions/ADR-0014-the-pre-agent-baseline-is-the-default.md)) and `unit_test.resolved` present in the grading entry's metrics. Guarded by `test_a_stub_agent_produces_an_empty_patch_on_a_dirty_image`. |
| 6 | The trace is persisted, **the interjection is in it**, and provenance is complete | `test_an_interjection_survives_conversion_into_the_trace` builds the bytes with the channel's own rendering path and asserts they survive conversion — an interjection that is delivered but lost in conversion passes points 1–4 and still leaves no evidence. Provenance: the fields `run_provenance()` stamps, plus `extra["agent_model"]`, which pins the actor even once the trace is dropped. |
| 7 | The **outcome word is correct** | `rollout_outcome` in the rollout record is the one that matches what happened; the four words are pinned apart by the named tests in `tests/test_rollout.py` ([ADR-0015](../../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md)). |

**Points 1 and 4 can only be closed by a real run, and a real run's evidence is
collected by the code being checked.** `supervisor.jsonl`, the trace and the
record are all written by the pipeline itself, so "the run says it did" and "it
did" are one statement made twice. Closing either point therefore requires **at
least one piece of evidence that does not come through our collection path** —
the actor's own subsequent behaviour showing it read the correction, or the
container-side raw capture, which is an independent chain from the supervisor's
log. Without that, a self-consistent account is being taken for a proof.


**All seven green = the pipeline works end to end.** Only then the stability
batch, whose purpose is to show the pipeline is *stable* — **not** to measure an
effect, which it is far too small to do.

**A precondition on the reporting path, not an eighth point.** Before *any* rate
is reported from this pipeline — which means before the stability batch, not
after it — the rate has to carry the two counts ADR-0015 §5 and
[ADR-0016](../../decisions/ADR-0016-the-endings-nobody-could-attribute.md)
require: how many runs were excluded as ours, and how many nobody could
attribute. Both ADRs recorded that as a contract on a reporter that did not
exist, and a contract with nothing enforcing it is the decoration this
component's own hazard entry is about.
[`swe_lab/reporting.py`](../../../src/swe_lab/reporting.py) is the canonical
value and renderer for it: the rate and its counts are **one value with one
rendering**, and the named tests in `tests/test_reporting.py` fail if either
count is deleted from that rendering. **Obligation:** nothing reports a rate
today, so the PR that adds the first reporting path routes it through that
renderer and tests the call site. Until then this is a safe value with no
caller, not a run-wide invariant — and it is a batch that was *entirely* ours
which shows why the distinction matters: it has no rate at all, and says so
rather than printing `0 / 0`.

- **Verification:** an [experiment](../../experiments/playbook.md) `REPORT.md` —
  hypothesis, logged run, conclusion. A supervised rollout that **fails to
  resolve is a complete result**; what would make it incomplete is a point above
  that nothing can demonstrate.
- **Dependencies:** three of the four are in place — the stdin channel and the
  live-stream wiring (stages 5 and 3 of the
  [design record](task-01-pipeline-end-to-end.md)), and the criterion with its
  loader-level refusal. What remains is a `Judge` and a `Writer` to construct
  `SpeakWhenOffTrack` with (stage 4b, point 3), and a caller for the criterion
  on the run's own path (point 2b). Verify with
  `rg -n 'class SpeakWhenOffTrack|def load_criterion|supervising_policy\(' src`
  rather than from this sentence. **Scope:** M

**The design record for this form is**
[`task-01-pipeline-end-to-end.md`](task-01-pipeline-end-to-end.md).
[`task-01-one-instance-end-to-end.md`](task-01-one-instance-end-to-end.md) is
kept as the record of the terminated hint-injection arm (its Step 5 is "the
steered re-run") and is marked superseded in its own header, pointing here.

## Task 02: Measure the injection shape

**Description:** Settle the spec's
[head open question](../spec.md#11-open-questions) by measurement. What shape
can a hook actually put into the conversation at a tool boundary, and which
shapes pass the three tests the question now asks — the actor sees it, it is
**marked as an external injection**, and our conversion preserves it? (The
wire-level `role` field is not the criterion; owner, 2026-09-01.) `PostToolUse`
`decision: "block"` is already measured and fails the third (it lands as an
`attachment`). The candidates — `updatedToolOutput` carrying a tagged suffix
appended to the tool's real output, `PostToolBatch`'s `decision` /
`additionalContext`, and a re-confirmation of `additionalContext` at this
version — get measured together with the two event-coverage questions
(`PostToolUseFailure` for the spinning-after-an-error case, `PostToolBatch` for
the parallel-batch case), because those change what the experiment is asking.

Each candidate is measured on **two** things: what the actor does with it, and
what our typed `Conversation` conversion does with it.

- **Acceptance:** a table of candidate → transcript shape → what the model sees
  it as → whether the converter preserves it → any observation on whether the
  actor complies; plus a recommendation for the head question and, if no
  candidate survives with its marker intact, the evidence the owner needs to
  rule on materialization.
- **Verification:** an experiment `REPORT.md` with the raw transcripts kept.
- **Dependencies:** none (runs in parallel with 01). **Scope:** S
- **Outcome:** [`experiments/trace_synthesis/injection_shape/REPORT.md`](../../../experiments/trace_synthesis/injection_shape/REPORT.md).
  `PostToolUse` `updatedToolOutput` with a tagged suffix appended to the tool's
  real output is the recommendation — the only candidate kept by **both**
  converters. Survival turned out to be a property of the converter rather than
  the channel, materialization is not needed, and two defects surfaced that the
  task did not go looking for (`proxy_log_to_conversation` keeps only the last
  thread; routing the actor through a proxy changes whether it follows a hint).

## Task 03: Hint log + conversion guard

**Description:** What is left of the [phase D](../spec.md#phase-d--collection)
step once [task 02](#task-02-measure-the-injection-shape) removed the
materialization half: a host-side log of every hint the Supervisor injected, and
a guard that cross-checks it against the converted `Conversation`. Pure
host-side code — no Docker, no model calls — which is where the correctness
risk belongs.

The load-bearing half is the **guard**: a hint that is not present in the
converted trace must fail the conversion loudly. Silently emitting a hint-less
trace is the one fatal failure mode in the spec, and task 02 found two live
routes to it — `proxy_log_to_conversation` keeps only the last proxy record's
thread, so a hint delivered inside a subagent's conversation disappears; and a
hint injected after the actor's last API call never reaches the model at all,
which is legitimate but must be recorded rather than assumed.

- **Acceptance:** the guard is pinned by a named test (a run whose hint is
  absent from the converted trace → conversion errors, no trace produced);
  round-trip tests over the typed model; `tool_use` ↔ `tool_result` pairing
  preserved.
- **Verification:** unit tests, no Docker; the full quality bar.
- **Dependencies:** 02 (its outcome decided what is left to build).
  **Scope:** S–M

## Task 04: Oracle analysis task + guidebook schema

**Description:** Phase B as a `Task`: a sandbox with the grading procedure,
the failed conversation and — when the dataset records one — the golden patch
mounted, the **git-history purge off**, producing a validated `guidebook.md`. The schema enforces the
`justification` field per stage — the field that makes an honest hint possible
at all. The failure arrives as the instance's own mounts — the instance is an
`oracle_failures` record ([task 11](#task-11-start-from-a-cached-failure)) —
which is what lets the shipped `oracle_analysis` workflow be a single entry
run from a name alone. The design record is
[`task-04-oracle-analysis-task.md`](task-04-oracle-analysis-task.md).

Phase B is independently useful: a guidebook is a readable artifact even
without phase C.

- **Acceptance:** schema validation rejects a guidebook with a stage missing
  its `justification`; the task declares `guidebook.md` as an output and the
  purge-off configuration is explicit rather than incidental.
- **Verification:** unit tests for the schema; one live run producing a
  guidebook a human judges usable.
- **Dependencies:** 01 (which shapes what a usable guidebook looks like), 11
  (the input). **Scope:** M
- **Outcome so far:** the task, the schema check and the workflow are landed
  with docker-free tests covering the whole composition, and the purge-off
  configuration is a named test. One live run has been made and is written
  up against its pre-declared scope in the design record: the path holds
  end to end, the produced guidebook failed the schema check on one missing
  field, and no human has judged it — the row above stays 🔶 until one has.

## Task 05: The supervisor — what it may see, when it speaks, what it may say

**The design lives in [`task-05-supervisor-the-component.md`](task-05-supervisor-the-component.md).**
This entry is the index summary; the plan is canonical about scope.

**Description:** A supervisor that consumes the actor's live output stream and,
when its policy says the moment has come, writes one short user message on the
actor's stdin ([ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)).
Three parts carry it: the **information barrier** is a property of the type the
supervisor is constructed with rather than an instruction in its prompt; **when
to speak** is a replaceable policy, because that is the measured unknown (8 of 8
non-compliances arrived too late); and what it may say is **bounded in length
and tagged**, with every intervention logged.

- **Acceptance:** the in-sandbox fold check — one intervention delivered inside
  the sandbox whose block length and `sha256` match the host measurement, which
  is [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
  refutation condition carried here so that it is scheduled; named tests for the
  privileged-field allowlist, the actor-only evidence, the length cap, the tag,
  and the emitter; a policy replaceable without touching the stream consumer;
  and a log that accounts for every cursor with a judgement, a silence or an
  explicit gap.
- **Verification:** unit tests over a **recorded** event stream with a stub
  sink, plus the single in-sandbox delivery. No live rollout is part of this
  task's acceptance — that budget belongs to the measurement rig.
- **Dependencies:** [ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md).
  **No longer** task 04: the judge measures against a criterion pinned to one
  committed artifact, not against phase B's guidebook, which is written with the
  reference patch and the unpurged history in hand. **Not** blocked on task 16
  either — the supervisor writes into a sink it is handed and never opens or
  closes the channel.
  **Scope:** M

## Task 06: Trace-quality scorer (decide whether to build)

**Description:** A blind judge — a model seeing only the visible prefix — is no
longer needed as a *leak detector*, because keeping the hints satisfies that by
construction. The open question is whether it earns its keep as a plain
trace-**quality** score: does this trace teach anything, or did the hint do the
work? **This task starts with the decision, and may end there.**

- **Acceptance:** either a written "not worth it" with the reasoning, or a
  scorer that produces a per-trace number plus evidence it correlates with a
  human reader's judgement on a sample.
- **Verification:** if built, a scored sample with human agreement reported.
- **Dependencies:** 01 (needs real traces to judge). **Scope:** M

## Task 07: The `oracle_guided_trace` workflow + integrity separation

**Description:** Wire B→C→D — with A ahead of it only for an instance no sweep
has failed on yet; a cached failure enters as an `oracle_failures` record
([task 11](#task-11-start-from-a-cached-failure)) — as a registered workflow on the existing
[workflow layer](../../decisions/ADR-0007-task-and-workflow-layer.md), with the
edges resolved from the store. The integrity half is not optional: every phase
B / C record carries the oracle-guided **policy stamp**
([ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §5), so these runs
can never be pooled with benchmark numbers, and the
[result verifier](../../horizontal/plans/task-26-result-verifier.md) is left
free to flag them as contaminated — which is correct behaviour, not a bug.

- **Acceptance:** a named test asserting the stamp is on the record and that
  aggregation across differing stamps still errors; the verifier's contaminated
  flag on an oracle-guided run is asserted as *expected*, not suppressed.
- **Verification:** unit tests plus one end-to-end workflow run.
- **Dependencies:** 03, 04, 05. **Scope:** M

## Task 08: Batch run — N instances, measure yield / cost / quality

**Description:** Run the pipeline over a batch and produce the numbers that
decide whether it scales: kept-trace yield, cost per kept trace (a baseline
rollout + eval, an oracle analysis, a guided rollout + eval, and one supervisor
call per tool call — with the guided rollout still able to fail and cost all of
it for nothing), and a quality read on the traces. Also the first real data on
the [selection question](../spec.md#11-open-questions): measured `pass@10` band
versus the cheap "failed once, gold self-test passes" proxy.

- **Acceptance:** a report with yield, cost per kept trace, and a comparison
  against rejection sampling on the same instances.
- **Verification:** an experiment `REPORT.md`.
- **Dependencies:** 07. **Scope:** L

## Task 09: Converge redaction onto one home, behind a publishing gate

**Description:** `ClaudeCodeHarness(capture="proxy")` declares
`claude.proxy.jsonl` as a native output, so the proxy log is a registered run
artifact.

**The disclosure this task opened for is fixed** (see below); what opened it is
kept here because it explains the shape of the work that is left. The proxy
recorded the headers it forwarded — its `excludedRequestHeaders` dropped only
`host` and `accept-encoding`, its response `excludedHeaders` only the hop-by-hop
four — so **every proxy-captured run stored the run's `Authorization` bearer
token and the operator's account identifiers verbatim**, and nothing under
`harnesses/claude_code/` redacted anything. It was **latent rather than live**:
those artifacts land in `.cache/` and the cache-backed T1 store, and no running
path publishes a rollout artifact — but
[`.gitignore`](../../../.gitignore) states that full-conversation trace records
live off-repo in a HF dataset repo, so whoever first published a proxy-captured
trace would have published a credential with it. Task 02 put proxy capture near
this pipeline's critical path, which is why the task is filed here.

**The header and body redaction itself shipped with
[task 10](#task-10-run-the-capture-proxy-inside-the-sandbox)**, because once the
proxy runs in the sandbox it writes straight into the workspace and "write time"
is physically inside `cc-reverse-proxy`. Credentials, the operator's
organization / workspace ids, the rate-limit representative claim, `Set-Cookie`
and the request body's `metadata.user_id` are masked there by default, and
`swe_lab.harnesses.claude_code.redaction` checks a capture from this side. What
remains is the part that was never about a header list.

**1. Converge the redaction facts onto one home.** The same fact turned out to
be written down **six** times, not the two this entry first assumed: the
checker, the experiment's post-run redactor, W1's exchange builder, the inline
set in the committed-captures test, and two hardcoded placeholders in tests.
The copies had drifted twice — in *membership* (the accepted set knew the
representative claim and `metadata.user_id`; the `src` set was written narrower
without consulting it) and in *representation* (`<redacted>` versus
`[REDACTED]`, which made the `src` scanner call **every** committed capture
dirty: 790 findings, all false).

The most consequential copy was the one this entry did not know about:
**W1's `exchange.py` held the narrowest set of all** — four names, missing
`Proxy-Authorization`, `Anthropic-Workspace-Id`, the representative claim and
`Set-Cookie` — and it is the copy whose output a publishing path would ship.
**The shortest list guarded the most exposed artifact.**

That is the general lesson, and it is why duplicated facts are worse than they
look: **the harm is not spread evenly.** Each fork drifts toward whatever is
locally sufficient for the code around it, and nothing arranges for the most
exposed path to hold the most complete copy. Expect the worst copy to sit
where it costs most, because nothing is pushing the other way.

`src` owns the canonical set, the placeholder constant and the body field list;
everything else imports from it, and `experiments/` never the reverse, since
`experiments/` is exempt from the hooks.

**2. Keep the old placeholder as a named legacy alias, with a date boundary.**
The 37 committed captures are a **record** and must not be rewritten, so the
scanner has to accept `<redacted>` too. That is not a second source of truth: a
legacy alias is *closed* and dated, whereas two live constants keep diverging.

**3. Make "unclassified" a state the scanner can report.** Redaction is a
deny-list, so by construction an unenumerated field is recorded verbatim —
"unseen fields are redacted by default" is not true today and nothing enforces
it (ADR-0012 §4). The fix is three classes on the reading side: must be masked,
deliberately kept, and **everything else reported as unclassified until someone
classifies it**. The cost is one noise event whenever an upstream adds a field;
the alternative is silent publication.

What that defends against is worth stating precisely, because it changes the
design: **the risk is changing upstream, not upstreams steadily adding fields.**
Measured 2026-09-01 — across the 37 committed captures (158 records) the
Anthropic response header space holds 26 names, and a fresh real Anthropic
response returned 25 names of which **zero** had never been seen. The one field
that *was* new arrived by switching upstream: `Set-Cookie`, on the OpenRouter
path.

**4. The publishing gate and the wider PII sweep.** Redacting the envelope is
not the same as clearing a trace for publication: bodies carry repository
contents and whatever the agent typed. The gate is what stands between a scanned
capture and a HF dataset repo, and it is the part still missing.

**Both are done.** `refuse_unpublishable_traces` runs inside `push_traces`,
before it reaches the HF API and with **no bypass flag** — a way to skip a
safety gate becomes the way it is used. It checks the **normalized exchange
record** (`*.last_exchange.json`), which is what actually gets uploaded, not
the capture it came from: a raw log can be spotless while the record built
from it carries an unclassified header, and the conversation bodies exist only
in the record.

Three classes of blocker, and findings name the field and the *class* of value,
never the value: an unmasked sensitive header, an unclassified header, and the
operator's own identity (home path, git name, email) anywhere in the record —
the body sweep. The identity is read from the same source the builder redacts
against, so the gate verifies the substitution actually ran rather than
trusting it.

Still open, and deliberately not claimed: this reasons about identity and the
envelope. Repository contents in the bodies are a separate judgement nobody has
specified yet.

- **Acceptance:** met. One home for the set, the placeholder and the body field
  list, with `experiments/` importing from `src`; the scanner reports
  unclassified fields; `push_traces` refuses to upload a trace carrying an
  unmasked secret, an unclassified header, or the operator's identity.

  **Spec re-check** (required when a task flips): no success criterion in
  [`spec.md`](../spec.md#15-success-criteria) is met or invalidated by this —
  they concern hint visibility, steering, trace honesty, the policy stamp and
  cost — and nothing on the out-of-scope list shipped. The spec makes no claim
  about redaction or publishing, so there is no section to reconcile.
- **Verification:** unit tests, each observed to fail on a mutant before being
  trusted — including the one that mattered: **removing the gate call from
  `push_traces` left every gate test passing**, because they exercised the
  function rather than the call site. The test added in response asserts the
  fake HF API was never reached, and does fail when the call is removed. Same
  shape as the placebo regression test in task 10; found here by running the
  mutation rather than by review — the producer skipping the body identity, the reader forgetting the
  legacy placeholder, and the classification ignoring which upstream it is
  reading. The committed-captures test now runs the shared checker and reports
  no false findings.

  **The same standard applies to a number said out loud and a number written
  down.** One statistic in this line of work was quoted from an impression and
  measured only when it was time to commit it to a document, by which point the
  capture it came from had been destroyed. Speaking it was not a check.
- **Dependencies:** [task 10](#task-10-run-the-capture-proxy-inside-the-sandbox)
  (done — it shipped the write-time redaction this builds on).
  **Gates:** publishing any proxy-captured trace — not local runs. **Scope:** S

## Task 10: Run the capture proxy inside the sandbox

**Description:** *(The state below is the **pre-task** one. The task is done:
`ProxyRecorder` is deleted, the proxy runs in the sandbox, and the three
dependencies described here are gone — see the Verification line.)*

`ProxyRecorder` started `cc-reverse-proxy` **on the host** and the container
dialled back through the Docker host gateway. That made a
**required** component — on the OpenRouter path the proxy's `X-Anthropic-Beta`
mirroring and `provider` injection are what make interleaved thinking work at
all — depend on three fragile things:

1. **A host firewall rule.** `ufw`'s `default deny (incoming)` blocks the Docker
   bridge, so every box that captures has to allow the recorder's port ranges
   explicitly — a `machine-setup` concern, on a schedule this repo does not
   control. Until one was in place this component's experiments were hard-blocked
   for a round (2026-09-01).
2. **A port derived from a dataset index.** `port_for_index(index) = base_port +
   index` ([`proxy.py`](../../../src/swe_lab/harnesses/claude_code/proxy.py))
   with **no upper bound**, and the aggregator holds a second base at 25000. The
   firewall rule therefore has to open ranges, guessed from how large a sweep
   anyone expects to run.
3. **A listener bound to every interface.** `reverse_proxy.go` binds
   `:%d`, and `machine-setup`'s `base` role already carries
   `ufw allow in on tailscale0` — so these ports are reachable from any node on
   the tailnet. That comes from the `base` role's standing rule, not from
   allowing the recorder's ranges, which is the point: it is an exposure nobody
   chose, and no capture-side change introduced it.

Moving the proxy **into the sandbox** removed all three at once. Each container
has its own network namespace, so a **hard-coded** port cannot collide by
construction; the agent dials container loopback, so **no firewall rule is
needed**; nothing binds on the host, so the **tailnet exposure disappears**. It
also makes the design backend-agnostic — `GitHubJobSandbox` is handed a job that
is already running, where a host-side proxy has nowhere to stand.

**The mechanism already exists.** The proxy is a static, standard-library-only
Go binary, and `MountedAssetsObserver` + `Mount(executable=True)` is the same
path that already places the pinned Claude Code binary in the container. Nothing
new has to be built.

**Two known costs, written down now rather than discovered later:**

- **Lifecycle moves into the invocation script** — start in the background, poll
  for readiness, flush before teardown. The log is append-only JSONL, so a
  killed process truncates at a line boundary and every already-written line
  stays complete.
- **The log lands inside the sandbox, where the agent can reach it — and that is
  a new exposure, not a level one.** An earlier version of this paragraph called
  it "level, not new" on the grounds that `event_stream.jsonl` is already in the
  workspace and already just as reachable. Both halves are wrong, and the review
  of the implementing PR established it: the host-side recorder kept its log in
  a **host** temporary directory until teardown, so the agent could not reach it
  at all while it ran; and "as reachable" is not "as sensitive" — the proxy log
  carries HTTP **headers**, which the event stream does not. One real capture
  held **65** live credentials and operator identifiers across its 13 records
  where the stream capture held none. (An earlier header-only count of the same
  file said 39; the scopes differ — the wider one also covers the request body's
  account id and the rate-limit representative claim — so the two numbers are
  not comparable.)

  What makes it acceptable is therefore a mechanism, not a comparison:
  **redaction at write time**, so an unredacted capture never exists on disk
  inside or outside the sandbox, and **no unredacted capture may enter any
  collected artifact**. Post-hoc cleanup does not satisfy this — it leaves a
  window in which the raw file is on disk and reachable.

- **Acceptance:** proxy capture works with **no host firewall rule and no port
  allocation scheme**; `machine-setup` can then drop both `ufw` ranges together
  with their `defaults` variables and the bringup-acceptance row.
- **Verification:** **met, in the constructive form** — a proxy-captured run
  that binds **no host port at all**, with the agent dialing container-local
  loopback. Deliberately *not* "one proxy-captured run on a box with the rules
  removed": that criterion is weaker than the property it tests (it shows the
  rules went unused on one run, not that nothing can depend on them) and it is
  circular, since removing the rules waits on the very change it would verify.
  What was checked: the acceptance rollout bound no host listener of its own
  (`ss -ltnp`; the sole listener in the old range belonged to another agent's
  host-side proxy), and **both `ufw` rules were still present at the time** —
  which is what makes the run evidence that they are not load-bearing rather
  than evidence that they are gone. The structural half is stronger than the
  run: the backend publishes no ports at all
  (`test_up_maps_no_host_gateway`), so there is no host port to bind.
- **Dependencies:** none. It gates nothing today (the firewall workaround has
  landed), but it removes a required component's dependency on machine-level
  configuration. **Scope:** M

## Task 11: Start from a cached failure

**Description:** Make phase A skippable. A full eval sweep has already cached
the failures phase B needs, so the pipeline's input becomes a **dataset of
cached failures** rather than a fresh rollout: the `oracle_failures` dataset,
whose record names the underlying instance (dataset + id) and carries the one
failed attempt — typed conversation, grader's verdict, submitted patch. The
record **delegates** the whole runnable surface to the underlying dataset's
record and adds the failure through `TaskInstance.mounts()` (ADR-0007 §2), so
the compile contract is touched by nothing; a builder turns a finished
`rollout_and_unit_test` run directory into a row, refusing anything that is
not a finished actor graded unresolved and anything credential-shaped. The
design record is
[`task-11-oracle-failures-dataset.md`](task-11-oracle-failures-dataset.md).

- **Acceptance:** `load_dataset("oracle_failures")` yields runnable records
  whose `sandbox_spec` / `prompt` / `gold_patch` / `unit_test_spec` are the
  underlying instance's and whose mounts stage the failure; the builder
  refuses a timed-out, crashed, unfinished or resolved run, a run whose
  persisted grading workspaces disagree with the recorded grade or with each
  other, and a credential-shaped conversation; one real record exists.
- **Verification:** unit tests over the record, the loader and the builder;
  the first record built from the qutebrowser/9ed748ef baseline failure
  (PR #265's harvest) with the parquet confirmed untracked.
- **Dependencies:** none. **Scope:** M
- **Outcome:** landed as designed. The first record's re-graded verdict names
  the same two failed tests the experiment's report diagnoses. Follow-ups are
  named in the design record: a blind run of the task — guided or not — must
  run `record.instance`, and the policy stamp on phase-B records is task 07's.

---

## Pending reconciliation (2026-09-01)

**Nothing here is decided.** These are proposals awaiting the owner's ruling,
recorded in the repo rather than left in a session, because the rows they
concern are *live* and a reader would otherwise take them as work to start.

### Why this exists at all

[#279](https://github.com/Luolc/swe-lab/pull/279) closed the injection arm on
its own pre-registered kill condition and reconciled
[`spec.md`](../spec.md) accordingly. It did **not** reach this index, so the
component's target design and its task list now disagree — and the task list is
the one people act on. Three places are affected, and they are three different
kinds of decay:

| where | what it says | kind |
|---|---|---|
| the ordering principle (top of this file) | the pipeline "rests on one assumption — that a supervisor holding a good guidebook can **steer** a blind agent" | a *premise* built on a terminated arm |
| task 01 (⬜) | acceptance includes the hint texts, where they were injected, and whether hints "stayed directional or drifted into specifics" | a task whose **direction** is wrong |
| task 03 (⬜) | "a host-side log of every hint the Supervisor injected" plus a guard cross-checking it | a task that **should not be built at all** |

Task 03 is the expensive one. A wrong sentence costs a reader a minute; a queued
⬜ task costs whatever an agent spends implementing it before anyone notices,
and that cost is paid on the day someone picks it up, not today.

### 1. The ordering principle — replace the premise

The default arm does not depend on whether an agent can be steered. It depends
on whether a guidebook can, **after the fact**, separate a solve whose reasoning
holds from one that does not survive inspection ([spec §15.2](../spec.md#15-success-criteria)).
So the cheapest falsifier changes with it: not "can a hint land at a tool
boundary", but "does a guidebook's post-hoc judgement of one real rollout hold
up when a human reads the same trace". If it does not, collection, scoring and
batch yield are all downstream of a judgement nobody can trust.

### 2. Task 01 — rewrite, do not delete

> [!NOTE]
> **Resolved 2026-09-01, but not by this proposal.** The owner ruled task 01's
> acceptance to be the seven end-to-end points now in
> [Task 01](#task-01-one-instance-end-to-end): the deliverable is a working
> pipeline, and measuring the effect moved to the downstream consumer. The
> proposal below was written for the **post-hoc grader** arm and is kept as the
> record of a form that was not taken.
>
> **One question it leaves open, deliberately not answered here:** the
> confrontation it describes — a guidebook's judgement against the unit-test
> verdict and a human reading — is not part of task 01 any more, and has no
> other home. Whether it still matters is the owner's call, not this file's.


Under the new arm, "one instance end to end" becomes the thing this product line
most needs and currently lacks: an **uninterfered** rollout of one real
instance, graded afterwards by a guidebook, with that grade confronted by both
the unit-test verdict and a human reading.

**Proposed description.** Run one real instance (gold self-test validated) with
no intervention; write a guidebook for it in the [spec's shape](../spec.md#phase-b--the-oracle);
apply the guidebook as a **post-hoc grader** to the resulting trace; record its
judgement, the real verdict, and whether the two together survive a human
reading. Automated, scratch code, nothing wired into a workflow definition.

**Proposed acceptance.**

- The artifacts: the instance, the frozen rollout with its conversation, the
  guidebook, and the grader's output (per-criterion, plus a keep / discard).
- The confrontation: the grader's judgement against the unit-test verdict, with
  the four cells named — resolved+kept, resolved+discarded, unresolved+kept,
  unresolved+discarded — this instance placed in exactly one, and the reasoning
  written down.
- A human reading of the same trace that either endorses or contradicts the
  grader, quoted rather than summarized.
- An explicit statement of what one case can and cannot support.
- **A rollout that resolves and one that fails are both complete results.**
  What would make it incomplete is a grader judgement nobody can check.

**Its relation to the honesty-scorer pilot, which must be stated in both
places and duplicated in neither.** The pilot
([`experiments/trace_synthesis/honesty_scorer/`](../../../experiments/trace_synthesis/honesty_scorer/))
is the **statistical** form of the same §15.2 question — cells × attempts,
yield and cost. Task 01 is its **single readable** form. Task 06's build /
don't-build decision on a trace-quality scorer needs both: numbers from the
pilot, human-checkable evidence from task 01. Neither restates the other's
facts; each links.

**The old design record is superseded, not edited.**
[`task-01-one-instance-end-to-end.md`](task-01-one-instance-end-to-end.md) is
*forward-looking* design for the terminated arm (its Step 5 is "the steered
re-run"), so leaving this index pointing at it as "the design record" would
mislead. Per the repo's convention for superseded material: keep the file, mark
it superseded at the top with a pointer, and write a new record for the new
form. Do not rewrite it in place.

Also unchanged on purpose:
[`experiments/trace_synthesis/steered_rerun/REPORT.md`](../../../experiments/trace_synthesis/steered_rerun/REPORT.md)
is the record of an arm closed by its own kill condition. **That is a result,
not a failed draft.** Task 01's new form gets a new experiment directory.

### 3. Task 03 — close it, and say why

Proposed notation, since ✅/⬜ cannot express it: **⛔ closed** in the status
column, the row kept, and a short "why not" in the section below it. A task that
quietly disappears and a task that was ruled out are different things to whoever
reads this next.

Reason to record: task 03 is *entirely* the injection arm — a log of hints that
are no longer emitted, and a guard that a hint reached a trace that no longer
carries hints.

**One fact inside it outlives it, and it now has a home.**
`proxy_log_to_conversation` keeps only the last proxy record's thread, so a
thread that is not the last request's is silently dropped. That is a **real
conversion defect independent of hints** — found by task 02, and recorded until
now only inside task 03's rationale, where closing the row would have deleted it
along with the task. It is rehomed as
[horizontal task 36](../../horizontal/plans/README.md), together with the open
question next to it: whether a subagent's turns appear in the event stream at
all, which `--disallowedTools …,Task` sidesteps rather than answers.

With that migration done, closing this row costs nothing that is still true.

### The completion criterion

Not "the list above is ticked off" — the list is itself one enumeration, and
this whole entry exists because the first pass only caught the item that had
been noticed. The criterion is a **search that comes back empty**:

```sh
grep -rniE "steer|inject|hint" docs/trace-synthesis --include="*.md"
```

Every surviving hit must either be about the terminated arm *as history*
(`spec.md`'s struck criteria, task 02's record) or carry a sentence saying why
it should still be there. Anything else is the same decay in a place nobody
listed.
