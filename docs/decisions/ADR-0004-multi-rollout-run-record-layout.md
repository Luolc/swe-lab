# ADR-0004: Multi-rollout run-record layout (pass@K persistence)

## Status

Accepted (the key amended by
[ADR-0007](ADR-0007-task-and-workflow-layer.md) §§6–7 — it gains a `<task>`
segment: `<sweep>/<instance>/r<rollout>/<task>/a<attempt>`; and see the
2026-07-29 Amendment below)

## Date

2026-07-29

## Context

The [`Store` persistence seam](../horizontal/plans/task-12-persistence.md)
(ADR-adjacent, task 12) keys each run's artifacts + manifest shard by

```
runs/<sweep_id>/<instance_id>/<run_ts>/run.json
```

where `run_ts` is a launch timestamp injected by the caller. Two consumers build
records: the eval/rollout CLIs (`persist_wiring.new_record`, a real
`YYYYMMDD-HHMMSS` timestamp) and golden `verify` (a fixed `run_ts="latest"`, one
shard per instance, idempotent re-verify).

Many inference/eval jobs need **K rollouts per instance** — pass@K / +K style
metrics sample the same instance K times. The current layout cannot represent
that, and the failure is silent rather than loud:

- The eval/rollout timestamp has **one-second granularity**, so two rollouts of
  the same instance in the same second — routine under parallelism — collide on
  the same key and **overwrite** each other.
- `verify` deliberately pins `run_ts="latest"`, i.e. exactly one shard per
  instance by construction.

So `run_ts` is doing double duty as both "when did this run happen" and "the
unique key for this run," and it is unreliable at the second job. There is also
no stable, addressable identity for "sample *k* of instance *i*," and no notion
of a **retry** of a specific rollout that failed for infrastructure reasons.

## Decision

1. **Add two integer fields to `RunRecord`:**
   - `rollout_id: int` — the sample index within a `(sweep, instance)`, `0..K-1`.
     It gives each of the K pass@K samples a stable identity.
   - `attempt: int` — the retry index of *that specific* rollout (a re-run after
     an infrastructure failure). `0` today; the field exists so retry logic is a
     later behavior change, not a second layout migration.

   Both default to `0`, so single-rollout jobs (`verify`, a one-off `eval`) are
   unaffected.

2. **The run key becomes** `<sweep_id>/<instance_id>/<rollout_id>/<attempt>`.
   `run_ts` stops keying the run (it is dropped from the path), and the `runs/`
   "type step" is dropped from the **key** (see decision 3).

3. **The `runs/` type-namespace moves to the store's configured root/prefix, not
   the per-key path.** `build_store("filesystem", root=.cache/store/runs)` and
   (task 13) `build_store("s3", bucket=…, prefix="runs")`. The key stays the
   clean `<sweep>/<instance>/<rollout>/<attempt>`, and a *shared* cloud bucket
   still keeps runs namespaced away from future siblings (traces, datasets,
   indexes) — the namespace exists once, at the root, instead of repeated in
   every key.

4. **`run_ts` becomes an honest, exported field** — a real UTC timestamp of when
   the run happened — no longer part of the key. `verify`'s `_RUN_TS="latest"`
   hack is retired: idempotency now comes from the deterministic
   `(rollout_id, attempt)` key (re-running a given attempt overwrites it), not
   from a fake timestamp.

5. **Indices are numeric.** `rollout_id` / `attempt` are `int`, and the bulk
   manifest read sorts by the parsed `(instance_id, rollout_id, attempt)`, never
   by lexical path order (so rollout `10` sorts after `2`, no zero-padding).

6. **The manifest read API splits in two:**
   - `read_manifests(sweep_id) -> list[RunRecord]` — every shard under a sweep
     (aggregation / `index`). Renamed to the plural to be honest about returning
     many.
   - `read_manifest(sweep_id, instance_id, rollout_id) -> list[RunRecord]` — the
     attempts of *one* rollout (usually one). A cheap targeted read for
     resume/retry that does **not** scan the whole sweep. This matters for a
     cloud store (task 13): a full-sweep `read_manifests` is a broad `LIST`,
     whereas a targeted read is a narrow prefix lookup.

7. **Resume/"done" keys on `(instance_id, rollout_id)`.** A shard existing for
   that pair marks it done. This preserves today's *skip-if-a-shard-exists*
   semantics at finer granularity — a **failed** attempt still counts as done and
   is **not** auto-retried. Genuine retry-on-failure is a *future* behavior built
   on `attempt` + `status`; this ADR only lays the identity down.

## Alternatives Considered

- **Keep `run_ts` as the disambiguator (finer clock / a uuid suffix).** Rejected:
  an opaque timestamp or uuid gives no *stable, addressable* sample identity
  (you cannot name "sample 3 of instance i"), and models neither pass@K nor
  retries. It would also keep the silent-overwrite footgun one clock-tick away.
- **Keep the `runs/` prefix inside every key.** A soft call — rejected in favor
  of moving the namespace to the store root: same protection for a shared bucket,
  cleaner keys. (If a future store genuinely needs multiple run-*types* under one
  configured root, this can be revisited without touching the record.)
- **One `read_manifest` that always reads the whole sweep.** Rejected: it forces
  a full-sweep scan for a targeted resume/retry check — negligible on the local
  filesystem, but a broad `LIST` on a cloud store. The targeted read is a real
  optimization for R2/S3 (task 13).
- **Defer `attempt` until retry logic exists (YAGNI).** Rejected: `attempt` is in
  the *key*, so adding it later is a second breaking layout migration. Paying it
  now — while the layout is already changing and only gitignored cache holds
  data — is strictly cheaper.

## Consequences

- **Breaking store-layout change, but low-risk migration.** Only the gitignored
  `.cache/store` holds data (the `golden-verify` sweep + `adhoc` runs); a re-run
  repopulates. Nothing committed or on HF uses the layout.
- **Behavior-preserving for single-rollout jobs.** `verify` and one-off `eval`
  write `rollout_id=0` / `attempt=0`; their observable output is unchanged.
- **Enables pass@K.** A sweep runner can now persist K addressable samples per
  instance and a retry framework can bump `attempt` — neither needs a further
  layout change.
- **Touch points (for the implementation task):** `RunRecord` (+ two fields);
  `persist.run_prefix`; `Store` ABC + `FilesystemStore`
  (`append_manifest`, `read_manifests`, targeted `read_manifest`);
  `build_store` root convention; `persist_wiring.new_record`; `verify` (done-key
  tuple, `_RUN_TS` removal, the `aggregate()` report-dir mirror that hardcodes
  `runs`).

## Future tasks

This ADR is the decision; the code lands as two sequenced tasks:

1. **Persistence — multi-rollout run-record layout** (horizontal, extends
   task 12). Implement decisions 1–7: the record fields, the new key, the split
   manifest API, and `verify`'s done-key + `run_ts` cleanup. One PR,
   behavior-preserving for single-rollout jobs.
2. **Eval/rollout — K-rollouts sampling** (W2 solve-eval, depends on task 1). A
   sweep runner that expands its todo into `instance × rollout 0..K-1`, runs each
   respecting the `(instance, rollout)` done-set, and persists each with its
   `rollout_id`; wire a `--samples K` option into `eval` / `rollout`; compute
   pass@K / +K from `read_manifests`. Retry-on-failure (bumping `attempt`) is a
   further increment on top.

## Amendment (2026-07-29): the rollout / attempt segments carry `r` / `a` prefixes

Decision 2 wrote the key as `<sweep_id>/<instance_id>/<rollout_id>/<attempt>`.
The **implemented** key labels the two numeric segments:

```
<sweep_id>/<instance_id>/r<rollout_id>/a<attempt>/run.json
```

Rationale: the layout is self-describing when browsing the store or an artifact
bundle — `r3/a0` reads as "rollout 3, attempt 0", where a bare `3/0` is ambiguous
about which number is which. It also keeps the run levels distinguishable from an
instance id that happens to be numeric, and gives the manifest globs a shape that
matches only real run directories (`*/r*/a*/run.json`).

Everything else in decision 2 stands: no `runs/` segment in the key (it is the
store root), and no `run_ts` (it is recorded on the shard). Task 1 landed with
this format; the numeric fields on `RunRecord` are unchanged.
