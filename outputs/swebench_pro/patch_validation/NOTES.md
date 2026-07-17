# Golden patch validation — findings

Human-curated companion to the auto-generated `summary.json` / `report.md` (those
are regenerated verbatim by `evaluation.verify --aggregate`; this file is not).

## Latest full sweep — run `29484642902` (2026-07-16)

730 `OK` / 731, with **1 `GOLDEN_FAIL`** — but that lone failure is a **flaky
external-dependency test**, not a dataset defect. Effectively the dataset is
**clean** under our harness.

### The 3 fixed truncated-name instances now pass ✅

The three dataset-side truncated-`fail_to_pass` false negatives corrected in
`core/datasets/swebench_pro/patches.py` all verify `OK` in this run:

- `instance_NodeBB__NodeBB-00c70ce7…`
- `instance_ansible__ansible-de5858f4…`
- `instance_future-architect__vuls-bff6b755…`

(See `experiments/eval_issues/truncated_golden_test_names/` for that fix.)

### The remaining `GOLDEN_FAIL` is flaky (not a defect)

`instance_NodeBB__NodeBB-76c6e30282906ac664f2c9278fc90999b27b1f48-vd59a5728…`
— golden passed **2932** tests and missed exactly **one**:

> `test/plugins.js | Plugins should get plugin data from nbbpm`

`nbbpm` is NodeBB's package manager; this test fetches plugin data from an
**external plugin registry**, so it is network-dependent and intermittently
fails. Evidence it is flaky, not broken:

- It was `OK` in the earlier full sweep (`29463094538`, 728/731) on the same
  code + data — only this test's external call differs run to run.
- A local golden re-verify (`python -m swe_lab.evaluation <id> --gold`)
  **resolves**: 2933 passed, 0 missing.

So the "true" verdict here is `OK`; the one-off failure is infra flakiness in an
externally-dependent test. No dataset correction is warranted (unlike the 3
truncated-name rows). If a perfectly green sweep is desired, re-running just that
shard will almost always come back `OK`.
