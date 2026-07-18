# Plan — current milestone

The **process** planning surface: the phased build order, the standing quality
bar, and review checkpoints for the milestone in flight. Pairs with
[`todo.md`](todo.md) (the ordered task list).

- **What we're building & where it stands:** the [map](../../README.md) (roadmap +
  status) and this workstream's [README](README.md).
- **How we work:** [`AGENTS.md`](../../../AGENTS.md) (build vs experiment mode, git
  workflow, quality bar, boundaries).
- Naming note: this top-level `tasks/` holds **planning** (plan + todo). The
  dataset-pipeline *code* lives under `src/swe_lab/pipelines/` — not to be
  confused with the "task list" here.

## Current milestone — W2: a working solve + eval loop at scale

W1 (annotation) is ✅ complete. W3 (audit) is planned. The active work is
**[W2](README.md)**: the `evaluation` subsystem is
built and validated (full gold sweep done, 731/731); **`rollout` (agent sampling)
is the focus**, then matrix eval to run at scale.

**Patch extraction is settled** in
[ADR-0001](../../decisions/ADR-0001-patch-extraction-and-grading.md) (Accepted);
the code stays authoritative. Still confirm the *actual* current rollout state
against the code before starting a rollout task — don't trust a doc's
"done"/"next" framing blindly.

## Phased order

1. **Settle the extraction base.** ✅ Done — patch extraction is formalized in
   ADR-0001 (Accepted); code and doc agree.
2. **Rollout end-to-end.** Confirm the container agent loop runs on GitHub
   Actions (container-job model, pinned linux-x64 Claude Code binary, one
   instance per job), captures the trajectory + a clean `patch.diff`, and records
   an explicit outcome (`resolved` / `unresolved_tests_failed` / `empty_patch`).
3. **Matrix eval.** One dispatch grading many instances in parallel (256
   matrix-cap → shard across workflows) — the path to running all 731.
4. **Retire the dataset stopgap.** Publish the fully-fixed parquet to HF and
   delete the in-memory `patches.py` correction.
5. **Backlog (from the decisions).** P1: post-setup diff base (D1), `:(exclude)`
   denylist (D2), faithful binary extract+apply (D3), deferred silent-failure
   guards (D7). P2: eval-side empty-patch guard (D8), fuller test-file reset (D5).

## Definition of Done (every task clears this standing bar)

- `uv run pre-commit run --all-files` clean · `uv run pytest` green.
- New behavior has a test; changed behavior updates its test.
- **No silent caps or truncation** — if a run bounds coverage (skips, denylists,
  sampling), it `log`s what was dropped. Silent truncation reads as "covered
  everything" when it didn't.
- Docs flagged *provisional* are not treated as authoritative — verify against
  code.

## Checkpoints (human review gates)

Between phases, stop for a quick review before proceeding — the natural place for
the user to catch a wrong turn while the work is still cheap to redo. Mark them in
`todo.md` as `### Checkpoint — <what to confirm>`.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Building on the provisional D1–D8 before re-review | High | Phase 1 re-reviews them first; treat code as source of truth meanwhile |
| Rollout patch extraction mis-captures silently (gitignored source, submodules, LFS) | High | D7 guards: detect + **log** when they would fire, never silent |
| GH container-job image missing libs (minimal images) | Med | Validate the pinned-binary mount on a real instance early (phase 2) |
| Matrix eval hits the 256-job cap on the full 731 | Med | Shard across workflows; log any instances dropped by a cap |
| Subscription credit wall mid-run | Med | Runners already stop cleanly + resume idempotently (skip existing outputs) |

## Open questions

Carried from [W2](README.md); resolve in-phase:

1. Does any instance need `ENV`-scraping / `test_patch` applied? (Surfaces only
   when a gold self-test fails.)
2. Eval-side apply hardening ladder (`--3way`/`--reject`) — worth diverging from
   Scale's strict grade? (D6, still weighed.)
3. Which parts of the D1–D8 record survive the re-review.
