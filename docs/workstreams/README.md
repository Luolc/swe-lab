# Workstreams

`swe-lab` is organized as independent workstreams over shared infrastructure in
[`src/swe_lab/core/`](../../src/swe_lab/core/) (dataset loading, per-instance repo
checkout, a headless-agent harness, a Docker execution layer, and the
dataset-agnostic benchmark contracts). Each workstream has its own detail doc;
[`PLAN.md`](../../PLAN.md) is the top-level status snapshot and index.

| # | Workstream | Status | Detail |
| --- | --- | --- | --- |
| W1 | Related-files annotation | ✅ Complete (731/731) | [w1-related-files.md](w1-related-files.md) |
| W2 | Solve + evaluate pipeline | 🚧 Active (eval done; rollout in progress) | [w2-solve-eval.md](w2-solve-eval.md) |
| W3 | Quality auditing / skew | 📋 Planned | [w3-quality-audit.md](w3-quality-audit.md) |

See also:

- [conventions.md](../conventions.md) — codebase map, commands, hazards.
- [experiments/playbook.md](../experiments/playbook.md) — how we run experiments.
- [decisions/](../decisions/) — architectural decisions (ADRs).
