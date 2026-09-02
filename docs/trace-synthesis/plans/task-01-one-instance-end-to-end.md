# Task 01 — One instance, end to end (superseded)

> [!WARNING]
> **Superseded on 2026-09-01 by
> [`task-01-pipeline-end-to-end.md`](task-01-pipeline-end-to-end.md).** This
> file is the design for the **hint-injection arm**, which was closed by its own
> pre-registered kill condition ([spec §15.1](../spec.md#15-success-criteria));
> its Step 5 is "the steered re-run", a person hand-driving an actor. Delivery
> has since moved off hooks onto the actor's stdin
> ([ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)) and
> the run is automated. **Kept, not edited**: what it records was true when it
> was written, and the candidate-selection measurement in Step 0 is still the
> one the new record reuses. Do not plan work from it.

**Status lives in [`README.md`](README.md).** This file is the design.

## What this task is now

The [spec](../spec.md) framed this task as the design's cheapest *falsifier*:
if a person holding a perfect guidebook cannot steer a blind actor from failure
to a passing verdict, the design is dead. The owner has since ruled that the
**core hypothesis is assumed, not tested** (2026-09-01), and that the whole run
is to be **automated** rather than hand-driven by a person.

So the deliverable changes shape. This is no longer an experiment that might
kill the design; it is the **end-to-end walkthrough that produces the first real
artifacts of the pipeline** on one real instance:

1. a genuine phase-A failure, frozen offline with its full conversation;
2. a guidebook written against that failure by an Oracle with privileged access;
3. a steered re-run of a blind actor, with every injected hint logged;
4. the resulting conversation, which is the first candidate SFT trace.

Everything below is done with **scratch scripts, not production code**. Tasks
03 / 04 / 05 are what turn any of it into something the repo keeps.

## Step 0 — Candidate selection (decided)

[Issue #261](https://github.com/Luolc/swe-lab/issues/261) measured both corpora
for mixed-outcome instances and ranked the ten fastest of each. **Take the four
fastest SWE-bench Pro instances**:

| mean rollout wall (s) | instance |
| ---: | --- |
| 510 | `instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f-v08d8e8889ec945ab821fb156c04c7d2e2810debb` |
| 557 | `instance_ansible__ansible-c1f2df47538b884a43320f53e787197793b105e8-v906c969b551b346ef54a2c0b41e04f632b7b73c2` |
| 613 | `instance_navidrome__navidrome-5001518260732e36d9a42fb8d4c054b28afab310` |
| 759 | `instance_future-architect__vuls-4c04acbd9ea5b073efe999e33381fa9f399d6f27` |

Why this list rather than the DeepSWE one:

- **Speed dominates.** This loop is iterative and every wasted rollout costs a
  wall-clock cycle; these run 2x faster than the DeepSWE candidates.
- **Both outcomes are known to exist.** All ten SWE-bench Pro candidates are
  1/2 resolved, so a failure is reachable in few samples.
- **None is on the flakiness watchlist.** Issue #261's caveat names four
  instances to check before concluding a hint changed anything — all four are
  DeepSWE, and one of them (`claude-code-by-agents-recursive-delegation`) sits
  in the DeepSWE top four. Taking the SWE-bench Pro head avoids the confound
  entirely.

## Step 1 — Validate the environment before spending a rollout

For each of the four, run the **gold** patch through the suite first:

```sh
uv run swe-lab run gold_unit_test <instance_id> --dataset swebench_pro
```

No agent runs, so this is cheap. It answers the only question that can waste an
entire rollout: does this instance's image build and does its golden test
actually resolve *on this machine*? An instance whose gold does not resolve is
unusable and no hint fixes it — issue #261's caveat about environment-sensitive
instances is exactly this failure mode, caught for the price of a test run.

**Acceptance:** at least one of the four resolves gold. Instances that do not
are dropped with a one-line note saying what failed.

## Step 2 — Harvest a real failure

On each surviving instance, in wall-time order:

```sh
uv run swe-lab run rollout_and_unit_test <instance_id> --dataset swebench_pro --rollout-id <n>
```

increasing `--rollout-id` until a failure appears, then moving to the next
instance if a few samples all resolve.

**The exit code is the acceptance criterion, and the distinction is
load-bearing** (`src/swe_lab/cli/run.py`, `ExitCode`):

- `2` = `UNRESOLVED` — it ran, and what it graded did not resolve. **This is the
  failure we want.**
- `1` = `FAILED` — a task failed, an edge failed, or the run was refused. This
  is infrastructure, **not** a reasoning failure, and must never be harvested as
  one. Issue #261 makes the same point from the other side: a rollout lost to
  infrastructure and counted as a failure is precisely the wrong signal.

**Acceptance:** one run with exit code `2`, on an instance whose gold resolved
in step 1.

## Step 3 — Freeze it before anything else runs

**Hazard, and the single easiest way to lose this work:** `run.py` deletes the
output directory at the start of every non-`--resume` run —
`output_dir.rmtree(missing_ok=True)`, where `output_dir` is
`.cache/runs/<workflow>/<instance_id>`. The *next* rollout of the same instance
destroys the failure you just harvested.

So the moment a run exits `2`, copy the whole directory out of `.cache/` before
issuing any further command. Keep:

- the conversation trace (`event_stream.jsonl`, or `proxy_log.jsonl` under proxy
  capture — `harness.native_outputs()`);
- the agent's patch, the unit-test verdict, and the run record;
- the instance id, rollout id, and the exact command line.

**Acceptance:** the frozen directory survives a subsequent rollout of the same
instance.

## Step 4 — The Oracle pass (orchestra writes this by hand)

Owner ruling: the guidebook is **not** written by the user, and **not** yet by
an Oracle model. `swelab-orchestra` writes it by hand, playing the Oracle, with
the privileged access the spec grants that role: the failed conversation in
full, the gold patch, the gold test patch, and free exploration of the repo.

It obeys the spec's [Phase B](../spec.md#phase-b--the-oracle) rules — staged,
directional, and **it never says or implies it saw the answer**. Its output is
the input to step 5 and nothing else in this task reads the gold patch.

## Step 5 — The steered re-run (simulated supervisor)

> **This step is a hook-era record.** It describes the design in force when the
> steered re-run was actually built and run, and it is kept in that form so the
> record says what was done. Delivery has since moved to the actor's stdin
> ([ADR-0013](../../decisions/ADR-0013-supervision-on-the-stdin-channel.md)); a
> rewritten form of this step belongs to whatever resolves this task's pending
> reconciliation, not to this paragraph.

A blind actor runs the same instance again. On each tool boundary a **scratch**
hook fires, calls a supervisor model with the guidebook plus the run so far, and
injects a directional hint when the actor is off track.

- The **injection shape** is task 02's output. Until it lands, work to the
  current leading candidate: a tagged postfix appended to the tool result
  (`updatedToolOutput`, actual output preserved, hint wrapped in an explicit
  tag). If task 02 contradicts that, this step follows task 02.
- The supervisor is an **API call from the hook**, never a nested `claude` — the
  `CLAUDECODE=1` guard blocks nesting and there are recorded recursive
  cost-explosion incidents ([spec §10](../spec.md#10-what-is-measured-about-hooks)).
- The hook subprocess does **not** inherit the agent's credential; pass the
  supervisor's own explicitly (same section).
- Every hint is logged host-side with its tool boundary, whether or not the
  channel preserves it.

**Acceptance:** the run completes and its conversation is captured, with the
hint log alongside. **A steered run that still fails is a complete result** —
it feeds task 06 and the specificity dial in
[spec §8](../spec.md#8-what-hint-specificity-now-trades), and it is not a reason
to retry until something passes.

## Deliverable

An [experiment](../../experiments/playbook.md) `REPORT.md` under
`experiments/trace_synthesis/handmade_instance/`, carrying: which of the four
instances survived gold validation, how many samples it took to reach a failure,
the guidebook verbatim, every hint with its injection point, the steered run's
verdict, and a judgement on whether the hints stayed directional or drifted into
specifics.

Large traces stay out of git (repo rule); the report quotes excerpts and names
where the frozen artifacts live.

## Out of scope

Production code of any kind: no hook wired into a workflow definition, no
supervisor task, no `oracle_guided_trace` workflow, no schema. Those are tasks
03, 04, 05 and 07, and each is gated on what this walkthrough learns.
