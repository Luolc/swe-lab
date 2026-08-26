---
license: apache-2.0
language:
- en
tags:
- code
- software-engineering
- benchmark
- swe-agents
pretty_name: DeepSWE 1.1 (materialized)
---

# DeepSWE 1.1 — materialized

A tabular materialization of [DeepSWE](https://deepswe.datacurve.ai/)
v1.1 — Datacurve's 113-task benchmark for coding agents — repackaged from
[`datacurve-ai/deep-swe`](https://github.com/datacurve-ai/deep-swe) into one
parquet row per task. **This is a third-party repack for tooling convenience,
not an official Datacurve release.**

- **Source commit**: see `manifest.json` (`source_commit`) — every file is
  carried over unmodified into columns.
- **Integrity**: `manifest.json` records the parquet's sha256 and a per-task
  content hash (sha256 over each row's canonical JSON), so consumers can
  verify the artifact and diff versions task-by-task.
- **Fixes** (the only deviations from verbatim, each kept auditable): the
  `base_commit` column normalizes three abbreviated/truncated
  `base_commit_hash` values to full 40-hex shas (measured from the task
  images); the verbatim upstream value is preserved beside it in
  `base_commit_hash`. The full list is in `manifest.json` → `fixes`.

## Schema

One row per task. Metadata (`task_id`, `language`, `repository_url`,
`docker_image`, timeouts, resources), the agent-facing `instruction`, the
verifier files (`test_sh`, `grader_py`, `config_json`, `test_patch`), the
held-out reference solution (`solution_patch`, `solve_sh`), the graded test
lists (`f2p`, `p2p`), and per-row provenance (`upstream_repo`,
`upstream_license`).

Note that `test_patch` (the held-out tests) and `solution_patch` are included
— exactly as they are public in the upstream GitHub repository. If you are
evaluating agents, do not let them read this dataset or that repository.

## Licensing

Two layers, per upstream's own
[`PROVENANCE.md`](./PROVENANCE.md) (included verbatim):

1. Datacurve AI Inc.'s original contributions (task specs, instructions,
   verifiers, curation) are **Apache-2.0** (`LICENSE` included).
2. The patches embed code from 91 upstream projects, each under its own
   permissive license (MIT / Apache-2.0 / BSD / ISC / Unlicense — none
   copyleft), listed per task in `PROVENANCE.md` and per row in the
   `upstream_license` column.

This repack complies with both layers by carrying the license, the provenance
table, and this attribution with the data. "DeepSWE" is Datacurve's name for
their benchmark; its use here is descriptive and implies no endorsement.

## Consumption

Built by and consumed with [swe-lab](https://github.com/Luolc/swe-lab)
(`swe_lab.datasets.deepswe`), which pins this artifact's sha256 and verifies
it before use. Any parquet reader works:

```python
import polars as pl
frame = pl.read_parquet("deep-swe-1-1.parquet")
```
