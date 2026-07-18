# SWE-Bench Pro — related-files annotations

Ground-truth annotations of the code a model needs to read to solve each
SWE-Bench Pro task instance. For each instance we annotate a list of **relevant
code snippets** — one `file_path` plus one contiguous, inclusive line range,
with a short reason. See the
[W1 workstream doc](../../../docs/workstreams/w1-related-files/README.md) for the
objective and how annotations are produced.

## Folder structure

```
outputs/related_files/swebench_pro/
    README.md                 <- this file
    annotations.parquet       <- combined deliverable (one row per instance)
    metadata.json             <- build metadata for the parquet
    intermediate/             <- per-instance runs (audit trail)
        <instance_id>/
            candidate_1.json / candidate_1.last_exchange.json
            candidate_2.json / candidate_2.last_exchange.json
            candidate_3.json / candidate_3.last_exchange.json
            aggregate.json   / aggregate.last_exchange.json
```

- **`intermediate/<instance_id>/`** — the full sample-and-aggregate run for one
  instance. Each annotation is produced by running N (=3) independent samples
  (`candidate_1..3`) and then reconciling them into a single `aggregate`.
  - `<label>.json` — the annotation record (instance id, snippets, run
    metadata).
  - `<label>.last_exchange.json` — the final `cc-reverse-proxy` record for that
    run, kept for auditing.
  - `aggregate.json` is the per-instance deliverable; the candidates are raw
    samples retained for traceability.
- **`annotations.parquet`** — every instance's `aggregate.json` combined into one
  table. This is the merged deliverable.
- **`metadata.json`** — describes the current `annotations.parquet` build.

`annotations.parquet` and `metadata.json` are regenerated from the aggregates by
the `combine` binary (run from the repo root):

```bash
python -m swe_lab.pipelines.related_files.combine
```

Only `aggregate.json` files feed the parquet — candidates and
`.last_exchange.json` files are intermediate audit data and are not included.

## `annotations.parquet` schema

One row per instance. Two string columns:

| Column | Type | Description |
| --- | --- | --- |
| `instance_id` | `String` | The SWE-Bench Pro instance id (stable key). |
| `relevant_snippets` | `String` | JSON string encoding the ordered list of snippet dicts (below). |

`relevant_snippets` decodes to a JSON array of objects, each a snippet:

| Field | Type | Description |
| --- | --- | --- |
| `file_path` | string | Path within the repository. |
| `start_line` | int | First line of the range (inclusive). |
| `end_line` | int | Last line of the range (inclusive). |
| `category` | string | Why the snippet is relevant (see categories below). |
| `description` | string | Free-text explanation of the snippet's relevance. |

List order is the snippet order; a single file may appear in multiple snippets
when the relevant lines are non-contiguous.

### `category` values

- `referenced-function` — a function the solution must call, modify, or build on.
- `context-file` — code needed to understand how things fit together.
- `useful-unit-test` — an existing test that reveals the expected behavior.
- `interface-contract` — a type/signature/schema the change must satisfy.
- `similar-pattern` — existing code whose structure the change should mirror.

### Reading the parquet

```python
import polars as pl

df = pl.read_parquet("outputs/related_files/swebench_pro/annotations.parquet")

# Expand the JSON column into structured snippets.
snippets = df.with_columns(
    pl.col("relevant_snippets").str.json_decode()
)

# One row per snippet, if you prefer a flat view.
flat = snippets.explode("relevant_snippets").unnest("relevant_snippets")
```

## `metadata.json`

Describes the current parquet build so consumers can tell which build they have
and verify it is intact:

| Field | Type | Description |
| --- | --- | --- |
| `parquet` | string | Filename of the parquet the metadata describes. |
| `num_rows` | int | Number of rows (instances) in the parquet. |
| `num_snippets` | int | Total snippets across all instances. |
| `generated_at` | string | ISO-8601 UTC timestamp of the build. |
| `sha256` | string | SHA-256 hex digest of the parquet bytes. |

Example:

```json
{
  "parquet": "annotations.parquet",
  "num_rows": 101,
  "num_snippets": 932,
  "generated_at": "2026-07-07T22:09:32.095851+00:00",
  "sha256": "7d5d17bc818a35a36d29ebc6c91d09d2fd89283be7a2efb590bf363b6f9c6ea9"
}
```
