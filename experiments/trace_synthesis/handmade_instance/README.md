# Handmade instance — one instance, end to end

The design and how to run it. Findings live in [`REPORT.md`](REPORT.md).

This is the experiment side of trace-synthesis
[task 01](../../../docs/trace-synthesis/plans/task-01-one-instance-end-to-end.md):
walk the whole oracle-guided pipeline over **one** real SWE-bench Pro instance
and keep what it produces. Nothing here is production code — the existing
`swe-lab` CLI plus the three scratch scripts in this directory.

## Question

Not "does the design work" — the owner ruled on 2026-09-01 that the core
assumption is taken as given. The question this round answers is the operational
one that gates everything after it:

> On this machine, can we cheaply obtain a **genuine phase-A failure** — a
> rollout that ran to completion and graded as unresolved, on an instance whose
> gold patch demonstrably resolves — and preserve it against the CLI's own
> cache-clearing?

A failure that is really an infrastructure fault is the wrong raw material: the
Oracle would write a guidebook against a problem the actor never had. So the
run is split into a cheap gate and an expensive harvest, and the two are told
apart by exit code rather than by reading logs.

## Method

**Candidates.** The four fastest mixed-outcome SWE-bench Pro instances from
[issue #261](https://github.com/Luolc/swe-lab/issues/261), in wall-time order,
listed in [`instances.txt`](instances.txt). All four are 1/2 resolved and none
is on that issue's flakiness watchlist. Rationale:
[the plan](../../../docs/trace-synthesis/plans/task-01-one-instance-end-to-end.md#step-0--candidate-selection-decided).

**Step 1 — gold gate.** `gold_unit_test` per candidate. No agent runs, so it is
cheap, and it answers the one question that can waste a whole rollout: does this
instance's image build and does its golden test resolve *here*? A candidate
whose gold does not resolve is dropped.

**Step 2 — harvest.** `rollout_and_unit_test` on the survivors in wall-time
order, increasing `--rollout-id`, until one exits `2`. The exit codes are the
acceptance criterion (`src/swe_lab/cli/run.py`, `ExitCode`):

| code | meaning | for us |
| --- | --- | --- |
| `0` | ran, resolved | not a sample; try the next rollout id |
| `1` | a task or edge failed, or the run was refused | **infrastructure** — never harvested as a reasoning failure |
| `2` | ran, and what it graded did not resolve | **the failure we want** |

**Step 3 — freeze.** `run.py` calls `output_dir.rmtree(missing_ok=True)` at the
start of every non-`--resume` run, where `output_dir` is
`.cache/runs/<workflow>/<instance_id>`. The next rollout of the same instance
therefore *deletes* the failure just harvested. `freeze.sh` copies the whole
directory out of `.cache/` and records the provenance beside it — and
`harvest.sh` calls it in-process the moment a sample exits `2`, so the window
closes mechanically rather than depending on an operator being quick.

## Run

```bash
cd experiments/trace_synthesis/handmade_instance

# Step 1 — the gold gate over all four candidates (sequential; logs + summary).
./gold_check.sh

# Steps 2 + 3 — one rollout sample, frozen in the same breath if it fails.
./harvest.sh <instance_id> <rollout-id>
```

**Use `harvest.sh`; do not call `swe-lab run` directly for step 2.** On exit `2`
it invokes `freeze.sh` itself, before control returns to you — which is the
whole point. Calling the CLI by hand leaves the harvested failure sitting in
`.cache/`, and the *next* rollout of that instance deletes it
(`run_cmd` rmtree's the output directory on any non-`--resume` run,
`src/swe_lab/cli/run.py`). The freeze window is not something to hit manually.

`freeze.sh <instance_id> <rollout-id>` stays callable on its own, but only for
rescuing a run that was already made some other way:

```bash
./freeze.sh <instance_id> <rollout-id> [workflow] [label]
```

All three scripts need the direnv environment
(`SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN`); in a non-direnv shell, prefix with
`direnv exec .`.

## Layout

```
instances.txt          the four candidates, in wall-time order
gold_check.sh          step 1 driver
harvest.sh             steps 2+3 — one rollout sample; freezes it on exit 2
freeze.sh              step 3 — copy a run out of .cache/ with its provenance
guidebook/             step 4 — the Oracle's guidebook
runs/gold/             step 1 artifacts: per-instance log + summary.jsonl
runs/*.log             step 2: full CLI output per rollout sample
runs/rollouts.jsonl    step 2: one append-only line per rollout sample
runs/frozen-manifest-*.txt   step 3: sha256 of the frozen tree, before/after
frozen/                step 3: the harvested failure, out of .cache/'s reach
```

`frozen/` holds full agent conversations and is **gitignored** — large traces
stay out of the repo (`AGENTS.md`). The report quotes excerpts and names the
absolute path.

## Capture strategy

Default `STREAM` capture, so the conversation lands as `event_stream.jsonl`
(`ClaudeCodeHarness.native_outputs`). `PROXY` capture would give `proxy_log.jsonl`
instead, but it needs `go build` of `cc-reverse-proxy` and Go is not installed on
this box yet — out of scope for this round.
