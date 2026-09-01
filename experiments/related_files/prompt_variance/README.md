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

Two suites of one instance per language (the first instance of each repo). `s2`
uses different repos — and the only true `ts` repo — for diversity.

| lang | `s1` repo (idx) | `s2` repo (idx) |
| --- | --- | --- |
| go | flipt-io/flipt (27) | navidrome/navidrome (9) |
| python | qutebrowser/qutebrowser (1) | internetarchive/openlibrary (6) |
| js | NodeBB/NodeBB (0) | protonmail/webclients (25) |
| ts | element-hq/element-web (14)¹ | tutao/tutanota (83) |

¹ element-web is TypeScript code but labeled `js` in the dataset; tutanota is
the only repo the dataset labels `ts`.

## Naming

Round output subdirs are `runs/<suite>-<round>/`, e.g. `s1-baseline`, `s1-v2`,
`s1-v3`, `s2-v3`. LLM-aggregate output is `runs/<round>-agg-llm/`. Keeping the
suite in the name makes each folder unambiguous.

## Run

```bash
# Run (or resume) a round. Args: <round-label> [model] [suite]. The label names
# the output subdir; suite selects the instance set (s1 default, or s2).
python experiments/related_files/prompt_variance/run_experiment.py s1-v3 sonnet s1
python experiments/related_files/prompt_variance/run_experiment.py s2-v3 sonnet s2

# Variance / cost / tokens for a round.
python experiments/related_files/prompt_variance/analyze.py s1-v3

# Aggregation over a round's 3 runs:
python experiments/related_files/prompt_variance/aggregate.py     s1-v3   # mechanical majority
python experiments/related_files/prompt_variance/aggregate_llm.py s1-v3   # LLM reconciler
```

Each run's full annotation is saved to `runs/<round>/<lang>__run<k>.json` and a
compact line is appended to `runs/<round>/summary.jsonl`. Completed runs are
skipped on re-invocation, so an interrupted round can be resumed.

## Sample-and-aggregate

Iterating the prompt aims for one stable prompt, but some variance is inherent
(how much of a test to include; whole-vs-focus on borderline-size files). For
those, run each instance N times and reconcile the samples into one answer —
self-consistency. Two implementations here (see [`REPORT.md`](REPORT.md)):

- `aggregate.py` — mechanical majority (keep files/lines ≥ 2 of 3 agree).
- `aggregate_llm.py` — an LLM aggregator agent that reads the N candidates + the
  checkout and synthesizes the best annotation. This is the better form (it
  rescues correct single-run regions and can override the majority).

Scaling this to the full dataset would also want the **repeats of one instance**
parallelized (not just across languages), which needs per-run isolation the main
runner lacks today (distinct checkout / proxy port / proxy-log per run — all
currently keyed by `instance_id`).

## Status of the scripts

`run_experiment.py` no longer runs: it imports `swe_lab.core.*`, a package
dissolved on 2026-07-25 by #53, and raises `ModuleNotFoundError` on import. The
experiment's deliverable is [`REPORT.md`](REPORT.md); the script is kept as
evidence of the method. It is exempted by name from the `no-stale-module-refs`
pre-commit hook — see the dated exemption list in `.pre-commit-config.yaml`.
Reviving it means porting it to today's module layout, not deleting the guard
entry. The other scripts here are unaffected and remain under the guard.
