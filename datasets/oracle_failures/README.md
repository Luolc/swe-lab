# Oracle failures

Cached **failed rollouts** of instances from another registered dataset, one
row per failure, for the trace-synthesis pipeline's Oracle
([design](../../docs/trace-synthesis/plans/task-10-oracle-failures-dataset.md)).
A row names the underlying instance (`dataset` + `instance_id`) and carries the
one attempt that failed: its typed conversation, the grader's verdict and the
patch it submitted. Loading a row resolves the underlying instance, so the
record runs with that dataset's own sandbox, prompt, gold patch and grading.

- Loader name: `oracle_failures` (`load_dataset("oracle_failures")`)
- Record: `src/swe_lab/datasets/oracle_failures/record.py`
- Back to the datasets index: [../README.md](../README.md)

## Building rows

There is nothing to download. Rows are built **locally** from finished
`rollout_and_unit_test` runs — the run's own output directory, as
`swe-lab run` leaves it under `.cache/runs/rollout_and_unit_test/<instance_id>/`
(or a copy of it):

```bash
uv run python -m swe_lab.datasets.oracle_failures.build \
    --run-dir .cache/runs/rollout_and_unit_test/<instance_id> \
    [--dataset swebench_pro]            # the dataset the instance belongs to
```

The builder writes `data/oracle_failures.parquet` (creating it on the first
row) and **replaces** an earlier row for the same instance: one failure per
instance per file. It refuses — and writes nothing — when the run is not a
usable failure sample: the actor did not finish, was timed out or crashed, the
grade resolved, the persisted grading workspace disagrees with the recorded
grade, or the conversation or patch matches a credential-shaped pattern (the
refusal names the pattern, never the value). The underlying dataset's data
must be present locally, since the row is validated against it.

Then run the Oracle over a row:

```bash
uv run python -m swe_lab run oracle_analysis <instance_id> --dataset oracle_failures
```

## Details

- File: `data/oracle_failures.parquet` (gitignored, like every dataset's data)
- Columns (7): `dataset`, `instance_id`, `rollout_id`, `conversation`,
  `verdict`, `patch`, `provenance` — JSON text for the three structured ones
- Identity: `instance_id` is the underlying instance's id, so runs of a row
  land in the store beside that instance's other runs; ids are unique within
  a file across sources — the builder refuses a same-id row from a different
  source dataset rather than overwrite it
- Provenance names the source run by its workflow-record key, sweep and
  timestamp, never by a host path
- Every task run against a row stages the three failure files
  (`failed_conversation.json`, `failed_verdict.json`, `failed_patch.diff`);
  a run that must not see them runs the underlying instance instead
