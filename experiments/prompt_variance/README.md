# Prompt-variance experiment

Goal: check how the annotation prompt behaves across languages and how stable it
is run-to-run, then iterate the prompt to reduce variance. See
[`REPORT.md`](REPORT.md) for findings.

## What it does

- One instance per language (go / python / js / ts), each run **3 times**.
- Judge: (a) is each result reasonable, (b) how big is the run-to-run variance.
  Small differences (a line off, one more/fewer snippet) are acceptable; large
  divergence means the prompt needs to be more stable.

Variance is judged on **two** axes, not just snippet count:

1. **Which files** are selected (file-set agreement across the 3 runs).
2. **The actual line ranges** within each file — do the runs point at roughly
   the same lines, and are the ranges *reasonable*? A run that grabs a whole
   200-line file where others take a focused 20-line range is a variance/quality
   problem even if the file set matches. `analyze.py` reports a per-file
   line-coverage IoU across runs plus the concrete ranges, so range drift and
   over-broad ranges are visible.

## Instances

Chosen as the first instance of a representative repo per language:

| lang | repo | dataset idx |
| --- | --- | --- |
| go | flipt-io/flipt | 27 |
| python | qutebrowser/qutebrowser | 1 |
| js | NodeBB/NodeBB | 0 |
| ts | element-hq/element-web | 14 |

## Run

```bash
# Run (or resume) a round; <round> names the output subdir (baseline, v2, ...).
python experiments/prompt_variance/run_experiment.py <round> [model]

# Summarize variance / cost / tokens for a round.
python experiments/prompt_variance/analyze.py <round>
```

Each run's full annotation is saved to `runs/<round>/<lang>__run<k>.json` and a
compact line is appended to `runs/<round>/summary.jsonl`. Completed runs are
skipped on re-invocation, so an interrupted round can be resumed.

## If variance persists: sample-and-aggregate (self-consistency)

Iterating the prompt aims for one stable prompt. But if variance stays high and
occasionally produces large errors, a fallback is to **run each instance N times
and have an aggregator agent synthesize the final annotation** from the N traces
/ results / selected files — majority-vote / self-consistency. This trades more
sampling for higher correctness. See PLAN.md ("Option: sample-and-aggregate").

If we test this here, the harness must **parallelize the repeats of one
instance** too (not just across languages), since it means far more sampling.
That needs per-run isolation the runner lacks today: a distinct checkout, proxy
port, and proxy-log path per run (all currently keyed by `instance_id`).
