# Batch annotation — QA and audit tooling

The batch run that produced the committed `related_files` annotations under
`outputs/related_files/swebench_pro/`. This folder holds the **round lists** and
the **checking scripts**, not the annotations themselves.

- `round{1..37}_ids.txt` — 730 instance ids, the order they were annotated in.
  Sampling seed `20260706`.
- [`qa_log.md`](qa_log.md) — the hand QA of each instance as it landed. This is
  the deliverable; the findings live there.
- `qa_check.py` — per-instance QA: validity, and how well the aggregate covers
  the gold patch's *existing* files.
- `recall_audit.py` — sweeps every annotated instance for real source-file
  recall misses, separating them from the docs / i18n / CI files the annotator
  correctly drops.
- `perf_check.py` — wall-clock vs. the agent's own active time, to tell "the
  agent was slow" from "the run stalled".

## Status of the scripts

`qa_check.py` and `recall_audit.py` **no longer run.** They import
`swe_lab.core.*`, a package dissolved on 2026-07-25 by #53, and raise
`ModuleNotFoundError` on import. They are kept as evidence of how the numbers in
`qa_log.md` were obtained, and are exempted **by name** from the
`no-stale-module-refs` pre-commit hook — see the dated exemption list in
`.pre-commit-config.yaml`. Reviving one means porting it to today's module
layout, not deleting its guard entry.

`perf_check.py` is unaffected: its one `swe_lab` import
(`swe_lab.pipelines.related_files.storage`) is still live, so it stays under the
guard like any other file.
