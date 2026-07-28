# Task 12 — Persistence: the `Store` seam + post-run persist step + manifest

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**. Builds on the tier model + store selection in
> [`spec.md` §Persistence](../spec.md#persistence--artifact-tiers--store-decided-2026-07-18)
> (R2 for T1, decided 2026-07-18) and the `fetch`/collect seam from
> [ADR-0003](../../decisions/ADR-0003-remote-sandbox-lifecycle.md) / task 14.

## 1. Purpose & scope

Land the **T1 persistence layer**: formal intermediates (trajectories, patches,
per-run results, diagnostics — **including failed runs**) go to a private,
S3-compatible object store, indexed by an append-only manifest. This is the
consumer of the `fetch`/collect seam task 14 built — the manager already lands
the registered artifacts on the host in `output_dir`; task 12 puts them
somewhere durable and records what happened.

### In scope
- A tiny **`Store` seam** (`put` / `get` / `append_manifest`), vendor-as-config
  via an open `build_store` registry (mirrors `build_sandbox`).
- **`FilesystemStore`** (local dir) — the default and the fully-testable,
  cloud-free implementation; **`FakeStore`** for unit tests.
- A **post-run `persist` step** (not an observer — see §4) that consumes a
  finished `RunResult`, uploads its collected artifacts under the run key, and
  appends **one per-run manifest shard**.
- **Tier + `--persist` flag** wiring (entry-point default, no inference) and the
  **`promote`** safety valve.

### Out of scope (→ task 13, gated by CP4)
- `S3Store` (boto3 — a runtime dep, ask-first) and pointing it at **R2**.
- R2 bucket / scoped-token provisioning (CP4) and CI-secret wiring.
- A real cloud end-to-end run.

`S3Store` is deliberately deferred so **task 12 merges with zero cloud/dep
surface** — `FilesystemStore` exercises the whole flow (persist → manifest →
promote → re-fetch) in unit tests.

## 2. Module layout

```
sandbox/
  store.py     Store(ABC) + FilesystemStore + build_store/register_store/registered_stores
               (S3Store added here in task 13)
  persist.py   RunRecord (the manifest entry) + persist(...) + promote(...)
  testing.py   + FakeStore
```

Persistence sits in `sandbox/` because it consumes `RunResult`
(`sandbox/result.py`) and is reused by every composition (rollout, eval, the
sweep driver); it is engine-adjacent, not axis-specific.

## 3. Key types & signatures

```python
# ─── sandbox/store.py ───────────────────────────────────────────────────────
class Store(ABC):                    # behavior interface (ABC — ADR-0002)
    @abstractmethod
    def put(self, key: str, src: Path) -> None: ...          # upload one file
    @abstractmethod
    def get(self, key: str, dest: Path) -> None: ...         # download (promote / re-grade)
    @abstractmethod
    def append_manifest(self, entry: RunRecord) -> None: ... # record one T1 run (a shard)

@dataclass(frozen=True)
class FilesystemStore(Store):        # default; the cloud-free, fully-tested impl
    root: Path                       # e.g. .cache/store  (or a committed outputs/ dir)
    # put/get = copy; append_manifest = write runs/<sweep>/<inst>/<ts>/run.json

# Vendor = config, mirroring build_sandbox's open registry:
def register_store(name: str, factory: StoreFactory) -> None: ...
def build_store(name: str, **cfg) -> Store: ...              # "filesystem" | (task 13) "s3"
```

```python
# ─── sandbox/persist.py ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunRecord:                     # one manifest shard — the T1 ledger entry
    sweep_id: str
    instance_id: str
    run_ts: str                      # injected at launch (no wall-clock in the engine)
    status: str                      # RunStatus.value — failures are kept, not dropped
    tier: str                        # "formal" (T1). debug never reaches here.
    backend: str
    model: str
    artifacts: dict[str, str]        # canonical name → store key
    metrics: dict[str, float]
    extra: dict[str, object] = {}    # e.g. is_empty_patch, error repr, patch stats

# Post-run, consumes the finished RunResult (§4):
def persist(
    store: Store, *, record: RunRecord, result: RunResult, output_dir: Path,
) -> RunRecord: ...                  # put each output_dir artifact, append the shard

def promote(store: Store, *, record: RunRecord, workspace: Path) -> RunRecord: ...
                                     # push a T0 debug workspace into T1 + append a shard
```

**Key scheme:** `runs/<sweep-id>/<instance>/<run-ts>/<name>` — e.g.
`runs/2026-07-30-sonnet/flipt__flipt-1/1706-0/patch.diff`. The manifest shard
for that run is `runs/<sweep-id>/<instance>/<run-ts>/run.json`.

## 4. The persist step is **post-run**, not a `PersistObserver`

The 2026-07-18 spec sketched a `PersistObserver`; the `fetch`/collect seam (task
14, which post-dates it) makes a **post-run step cleaner**, and this task adopts
it (the spec bullet is amended to match).

Why not an observer: the manifest needs the **final `status` + `metrics` +
artifact host paths**, and the manager assembles those into `RunResult` *after*
teardown (`before_destroy` → collect → `down` → `after_destroy` → `_finish`). No
observer hook can see them. The collect step has already fetched exactly the
registered artifacts into `output_dir` on the host, so persistence is a plain
consumer of the finished run:

```python
with manager.session() as sb:
    harness.run(sb, timeout=timeout)
result = manager.result
if tier == "formal":
    persist(store, record=record, result=result, output_dir=workspace)
```

The engine is **unchanged** — no new hook, no `PersistObserver`. `persist` walks
`result.artifacts` (name → host `Path` in `output_dir`), `store.put`s each under
the run key, and `store.append_manifest`s one `RunRecord`. **Failed runs persist
too** (the `if tier == "formal"` guard is on tier, not on success).

**T1 contents:** the collect-registered artifacts (patch, trace, results) plus,
on failure, whatever a future diagnostics step writes into
`workspace/diagnostics/` (same rank as any artifact; persisted by the same path
— see the spec's deferred on-error design). No separate diagnostics channel.

## 5. Manifest — per-run shards, aggregated on read

Object stores have no atomic append, so a single shared manifest would race
under a parallel sweep. Instead **each run writes its own shard**
(`…/<run-ts>/run.json`) — inherently race-free — and an **`index` step**
aggregates the shards into a sweep-level manifest on demand (reusing W1's
`combine`-style pattern). The manifest **indexes T1 only**; debug residue never
touches it.

## 6. Tier, flag, promote (from the spec, unchanged)

- **Tier = entry-point default + one flag, no inference.** Formal sweep
  workflows default `formal`; `workflow_dispatch` one-offs and the local CLI
  default `debug` (opt in with `--persist`). The tier + `run_ts` + `sweep_id`
  are stamped into the `RunRecord` **at launch** (timestamps are injected, never
  read inside the engine).
- **`promote`** — one command pushes a `debug` run's workspace into T1 and
  appends its manifest shard. T0's TTL (workspace `.cache/` locally; GH Actions
  artifacts in CI) gives the recovery window, so classification need not be
  perfect.
- This **subsumes the deferred `outputs/` restructure**: committed intermediates
  in `outputs/` are T1 and migrate under the store root.

## 7. Testing
- `FilesystemStore` + `FakeStore` drive the whole flow with **no cloud, no
  Docker**: a formal run persists artifacts + a shard; a debug run persists
  nothing; `promote` moves a debug workspace into T1 with a shard; `get`
  re-fetches; the `index` step aggregates shards.
- `build_store("filesystem", …)` round-trips; unknown name → `SandboxError`.
- Quality bar green.

## 8. Dependencies & sequencing
Depends on task 14's `fetch`/collect seam (done) and `RunResult`. **CP4**
(R2 provisioning, ask-first) gates **task 13** (`S3Store` + CI), which reuses
this exact seam — only `build_store("s3", endpoint=…, bucket=…)` changes.
Scope: **M**.

## 9. Decisions settled in review (2026-07-30)
- **Post-run `persist` step, not a `PersistObserver`** (§4) — the manifest needs
  post-teardown `RunResult` state.
- **Per-run manifest shards + an `index` aggregation** (§5) — object stores have
  no atomic append; shards are race-free under parallel sweeps.
- **Vendor = config via an open `build_store` registry** (mirrors
  `build_sandbox`); `FilesystemStore` default, `S3Store`→R2 in task 13; B2 /
  Scaleway are drop-in behind the same seam.
- **Key scheme** `runs/<sweep-id>/<instance>/<run-ts>/<name>`; T1 keeps failed
  runs and (later) `workspace/diagnostics/`.
