# Instance screening — design

**Question.** Of the 40 SWE-bench Pro instances in
[issue #261](https://github.com/Luolc/swe-lab/issues/261), which are *good
tasks* — and which are broken in the way OpenAI's
[2026-07 audit](../../../docs/research/swebench-pro-task-quality.md) describes,
so that feeding them to trace synthesis would teach a hint to fix the dataset
rather than the agent?

**Why it blocks the main line.** Phase A harvests a failure and phase B writes a
guidebook against it. If the instance is broken, the "failure" is the solver
picking one of two defensible readings, the guidebook teaches a coin flip, and
the trace is worse than no trace.

**Criterion.** Determinacy — is the graded behavior uniquely pinned by
`problem_statement` + `requirements` + `interface` + the repository at
`base_commit`? Issue #261 selects on *mixed outcome*, so "some rollout resolved"
is true of every candidate and screens nothing.

**Constraint.** Pure data analysis. Three rollouts belonging to other task pairs
were running on this four-core box, so **no container is started**: everything
comes from the parquet plus repository source fetched over HTTP at
`base_commit`.

## Layout

| File | What it is |
| --- | --- |
| [`REPORT.md`](REPORT.md) | the results: per-instance verdicts, screen overlap, the control, what to hand the trace-synthesis line |
| [`candidates.json`](candidates.json) | the same verdicts, machine-readable, with each screen's raw output per instance |
| [`screens.py`](screens.py) | the four mechanical screens, plus `--random N --seed S` for the control |
| [`screens.json`](screens.json) | screen output over the 40 candidates |
| [`control-screens.json`](control-screens.json) | screen output over a seeded random 40 of the full 731 |
| [`instances.txt`](instances.txt) | the 40 instance ids, extracted from issue #261 |

## Method

1. **Screen mechanically.** Four screens, each catching a different disease;
   every hit is an alarm routed to manual review, never a verdict. Two of them
   report their own precondition, so "no signal" is never read as "no evidence".
2. **Judge each instance by hand** against the determinacy criterion, quoting
   the prompt text and the repository source that settles it.
3. **Calibrate the instrument.** Alarms that turned out to be artifacts were
   fixed in `screens.py` and the screens re-run, because an alarm rate dominated
   by false alarms cannot stand in for a broken-task rate.
4. **Control.** Run the same screens over a seeded random 40 from the full 731,
   to test whether the mixed-outcome subset is enriched for broken tasks.

Reproduce:

```sh
direnv exec . uv run python experiments/trace_synthesis/instance_screening/screens.py
direnv exec . uv run python experiments/trace_synthesis/instance_screening/screens.py \
    --random 40 --seed 261 --out control-screens.json
```

Both cache repository tokens under `$SCREEN_CACHE` (default
`/tmp/swe-lab-screen-cache`); the first run downloads one source tarball per
distinct `repo@base_commit` and takes roughly half an hour.
