# Conversation traces & Hugging Face storage

How the large agent-conversation **traces** are stored, and how the tooling keeps
three copies of the truth — your **local files**, the **git manifest**, and the
**Hugging Face revision** — reconciled. Read this before pushing/pulling traces
from more than one machine.

This is a **general mechanism**, not a related-files-specific one: every
annotation task produces large per-run traces that want the same off-repo +
manifest treatment. It is documented here at the repo level for that reason. The
implementation currently lives in
[`pipelines/related_files/traces.py`](../src/swe_lab/pipelines/related_files/traces.py)
(the first annotation task); it will be promoted to `core/` when the second task
lands — see [§7](#7-multi-task-note-future). The model below is task-agnostic.

---

## 1. Why traces live off-repo

Each annotation run records the agent's **entire conversation** in a
`*.last_exchange.json` file (tens to hundreds of KB each — 1600+ of them, ~230
MB and growing). That is too big and too noisy to keep in git, but we still want
it: it is the audit trail behind every annotation.

So the repo splits its data into **three planes**:

| Plane | Where | In git? | What |
| --- | --- | --- | --- |
| **Input dataset** | `datasets/<name>/data/*.parquet` (downloaded from a public HF dataset, e.g. `ScaleAI/SWE-bench_Pro`) | no (gitignored) | the tasks to annotate |
| **Output annotations** | `outputs/related_files/<dataset>/` — `annotations.parquet`, `metadata.json`, `README.md`, `traces_manifest.json` | **yes** (small) | the deliverable |
| **Output traces** | HF **dataset** repo `luolc/swe-lab-traces` (private) | no — only the *manifest* is | the big per-run conversations |

Only the **manifest** (`traces_manifest.json`) is version-controlled. It is the
bridge between the small committed deliverable and the big off-repo blobs.

## 2. The manifest is the single source of indirection

`traces_manifest.json` records, for one dataset:

```jsonc
{
  "repo_id": "luolc/swe-lab-traces",
  "repo_type": "dataset",
  "revision": "2a88ec59bfcf…",        // the exact HF commit this manifest describes
  "git_commit": "eb83421…",           // the repo commit it was generated at
  "dataset": "swebench_pro",
  "num_traces": 1604,
  "total_bytes": 238…,
  "traces": {
    "swebench_pro/intermediate/<id>/<label>.last_exchange.json": {
      "sha256": "…", "bytes": 12345
    },
    …
  }
}
```

Two properties make everything else work:

- **Content integrity** — every trace is pinned by `sha256`, so a download can be
  verified and a local file can be checked against what the manifest claims.
- **Revision pinning** — `revision` is the exact HF commit, so `fetch` is
  *reproducible*: a given git commit always retrieves the traces it was built
  with, even if HF later advances.

Nothing else in the repo hardcodes an HF path. Reorganising the HF layout later
means changing where `push` uploads and re-pushing; consumers only read the
manifest, so they keep working. (All trace files are held locally, so a re-push
to any new structure is a safe, reversible migration.)

## 3. The three states, and who wins

At any moment there are three copies of "the traces":

```
   local files            git manifest              HF revision
   (outputs/.../  ◄─sha256─►  traces_manifest.json  ◄─revision─►  the dataset repo
    intermediate)                (committed)                       (the blobs)
```

**Git is the source of truth for "latest."** Because the manifest is committed,
"which state is newest" is normally just "which git commit is newest" — and git
already answers that. The rule of thumb:

> Pull before you work. After a push, commit the updated manifest in the same
> change. Let a git conflict on `traces_manifest.json` force a human to
> reconcile.

The commands below make that rule enforceable and the divergences detectable.

## 4. Commands

Run from the repo root with `HF_TOKEN` set (e.g. `source ./.envrc.local`):

```
python -m swe_lab.pipelines.related_files.traces <action> [flags]
```

| Command | What it does |
| --- | --- |
| `status` | Compare **local vs manifest vs HF head** and print who is ahead, with a recommendation. Read-only. |
| `push` | Upload local traces, **guarded by an optimistic-concurrency check** (fails if HF advanced past your manifest), then rewrite the manifest at the new revision. |
| `push --mirror` | Also **delete** remote traces that are absent locally — makes HF exactly equal the local set. |
| `push --force` | Skip the concurrency guard (last-writer-wins). Use only when you are certain local is the truth. |
| `fetch` | Download every trace the manifest names, **at its pinned revision**, verifying sha256. Skips already-correct files. |
| `adopt-remote` | Take **HF head** as truth: download it and rewrite the manifest. For when HF was pushed but its manifest was never committed to git. |

### `status` output

```
local vs manifest : 1604 ok, 0 changed, 0 missing, 0 extra
manifest revision : 2a88ec59bfcf
HF head           : 2a88ec59bfcf
=> in sync — local, manifest, and HF all agree.
```

- **local vs manifest** — `ok` match by hash; `changed` differ; `missing` are in
  the manifest but not on disk; `extra` are on disk but not in the manifest
  (un-tracked new runs).
- **manifest revision vs HF head** — equal ⇒ your manifest describes HF's current
  state; different ⇒ **HF has advanced past your manifest**.

## 5. Why it is robust — the reconciliation decision table

`status` collapses the three states into two questions — *is local clean vs the
manifest?* and *is the manifest in sync with HF head?* — and every combination
has a safe, explicit resolution:

| local vs manifest | manifest vs HF | meaning | do this |
| --- | --- | --- | --- |
| clean | in sync | everything agrees | nothing |
| **dirty** | in sync | you re-ran / added traces locally | `push` (or `push --mirror` to also purge remote-only) |
| clean | **HF ahead** | someone else pushed; you are behind | `git pull` then `fetch` — or `adopt-remote` if their manifest was never committed |
| **dirty** | **HF ahead** | **diverged** — both sides changed | decide the source of truth: `adopt-remote` (take HF) or `push --force`/`--mirror` (take local) |

This is exactly the two scenarios that motivate the design:

- **Local is stale, HF is newer → take HF.** `status` reports *HF ahead*. Normal
  path: `git pull` (gets the newer manifest) then `fetch`. If the newer manifest
  was never committed, `adopt-remote` reconstructs it from HF head.
- **HF is legacy, you re-ran locally → overwrite HF.** `status` reports *local
  dirty*. `push` publishes it (`--mirror` also removes traces you deleted). If a
  colleague pushed in between, the guard makes your plain `push` **fail** rather
  than silently union — you re-`status`, reconcile, and push again.

What guarantees each property:

- **Integrity** — `fetch`/`status` verify every file's `sha256`; corruption or a
  wrong revision is caught, never silently accepted.
- **Reproducibility** — `fetch` is pinned to `manifest.revision`, so history is
  stable regardless of later pushes.
- **Concurrency safety** — `push` passes `parent_commit=<manifest revision>` to
  HF, an atomic compare-and-swap: two clones pushing concurrently cannot both
  win — the second is rejected. And because each push also rewrites the committed
  manifest, a concurrent race additionally surfaces as a **git merge conflict on
  `traces_manifest.json`**, forcing a human decision.
- **Explicit truth direction** — `adopt-remote` (remote wins) and `push --mirror`
  (local wins) name the two ways to resolve a divergence, instead of leaving it
  to guesswork.

## 6. What is *not* automatic (know the edges)

- If you `push` to HF but **never commit the manifest**, other clones cannot learn
  the new revision from git. `status` will still catch it (it compares against HF
  head live) and tell you to `adopt-remote`; but the git-first discipline is what
  keeps this from happening. Always commit the manifest with the push.
- `push` (without `--mirror`) is **additive** — it will not delete remote traces
  you removed locally. Use `--mirror` to make HF exactly match local.
- The HF repo is currently a **private blob store**, not a polished, loadable HF
  dataset. `annotations.parquet` (the actual deliverable) lives in git, not on
  HF. Publishing a consumable public dataset would be a separate step.

## 7. Multi-task note (future)

Today `traces.py` lives under `pipelines/related_files/` and the HF repo layout is
`<dataset>/intermediate/<id>/…` — no `<task>/` segment. A second annotation task
pushing to the same repo could collide. When the second task lands, promote this
module to `core/` parameterised by `(task, dataset, repo_id)`, and shift the
upload root up to `outputs/` so paths become `<task>/<dataset>/intermediate/…`
(mirroring the git layout, collision-free in one repo). Because everything is
manifest-driven and all blobs are held locally, that migration is a small,
reversible re-push.
