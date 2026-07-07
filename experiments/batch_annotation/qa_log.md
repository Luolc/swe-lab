# Batch annotation — manual QA log

Random-sampled instances annotated with the finalized pipeline (3 samples +
aggregate). Each is QA'd by hand as it lands: a brief note if fine, a detailed
one if something is wrong. Outputs live under `annotations/swebench_pro/<id>/`
(gitignored during QA). Sampling seed: `20260706` (`round{1,2}_ids.txt`).

Legend: ✅ good · ⚠️ minor · ❌ problem.

## Round 1

_In progress. Rolling window of 4 concurrent pipelines (8-core / 16 GB box)._

| # | instance | lang | agg snippets | valid | note |
| --- | --- | --- | --- | --- | --- |
