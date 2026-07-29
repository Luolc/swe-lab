# ADR-0003: Remote-sandbox support — up-first lifecycle + one lifecycle-bearing `Sandbox`

## Status

Accepted

## Date

2026-07-26

## Context

The [SandboxRun spec](../horizontal/spec.md) scoped execution to **model A**
(host-orchestrated persistent container) with two backends — **A-host**
(`DockerHostBackend`, workspace bind-mounted) and **A-ghjob**
(`GitHubJobBackend`, the CI job *is* the container). Its central simplification:

> "The shared, inspectable state between observers is the **workspace
> filesystem** (`sb.workspace`)."

That workspace is a **host-visible directory** (`Sandbox.workspace: Path`), and
the manager lifecycle is built around it (`sandbox/manager.py`):

```python
self._prepare_workspace()                          # self.workspace.mkdir() — a HOST dir
self.backend.materialize(merged, self.workspace)   # write files HOST-side, BEFORE up
handle = self.backend.up(self.spec, self.workspace)# bring up; hand it the host dir
```

New **P0 requirement:** run against **remote / model-hosted sandboxes** — a
provider-hosted code-execution service (the agent and its container live on a
third-party server, not on our runner). Such a sandbox has **no host-shared
filesystem**: its filesystem does not exist until the sandbox is provisioned,
and every file operation (stage inputs, read outputs) must go **through the
sandbox's own API**.

The current design bakes in three host-filesystem assumptions that make this
impossible:

1. **`_prepare_workspace` operates on `self.workspace` directly** (`mkdir` /
   `iterdir`) — it assumes the workspace is a host directory the manager owns.
2. **`materialize` runs before `up`, writing host-side** — for a remote sandbox
   there is nothing to write to until `up`; staging must happen *after* `up`,
   via the sandbox.
3. **`workspace: Path` (a host FS path) is threaded through the manager, the
   `Sandbox` handle, `materialize`, and every observer.** The observers and the
   grader read/write the workspace as a host directory:
   - `observers/diff_extract.py`: `(sb.workspace / "extract.sh").write_text(...)`,
     `_read_patch(sb.workspace / "patch.raw.diff")`, `(sb.workspace / "patch.diff").write_text(...)`;
   - `conversation/observer.py`: `to_conversation(sb.workspace)`,
     `sb.workspace / "conversation.json"`, and every `native_outputs` path;
   - `evaluation/methods/unit_test/run.py`: `grader.grade(sb.workspace)` reads
     `output.json` host-side.

So this is **not** just a wrong ordering. The host-directory workspace is the
core model's load-bearing assumption, and it pervades the manager *and* the
observer/grader layer. Reordering `materialize` after `up` alone would still
leave every observer doing host-side file I/O that a remote sandbox cannot
satisfy. The spec's "shared state is the workspace filesystem" is, as written,
host-FS-bound.

## Decision

Generalize the engine so **one lifecycle-bearing `Sandbox` object is the whole
thing** — config, lifecycle, and operations — with no separate host workspace
path and no separate "backend" object. Two things are settled here; the
**transfer interface is deliberately deferred** (see §3) so we don't freeze a
wrong assumption.

### 1. Up-first lifecycle

Reorder `SandboxManager.sandbox()` so nothing touches the sandbox filesystem
before it is live:

```
before_create → sb.up() → [mount inputs] → after_create → yield sb (body)
→ before_destroy → sb.down()
```

`up` provisions the sandbox; nothing is staged before it exists. This ordering
is *strictly more general*: A-host and A-ghjob work unchanged under it, and it is
the only ordering a remote sandbox can satisfy. Provisioning (today's
`SandboxManager._prepare_workspace`) moves **into `up`** — the manager never
touches a host directory.

### 2. Merge `Sandbox` and `SandboxBackend` into one lifecycle-bearing `Sandbox`

Drop `workspace: Path`, and **collapse the `Backend`/`Sandbox` split**. Today the
backend is framed as a "frozen factory" that produces a thin `Sandbox` handle —
but a sandbox genuinely **has a lifecycle** (`up` / `down`) and **internal
state** (its live handle/connection, what has been mounted in it). The
frozen-factory framing is a fiction, and the only thing it enables — *one backend
: many sandboxes* reuse — is **never used**: the engine's worldview is
explicitly *one sandbox, one run, one `RunResult`* (batching lives outside, per
the spec §Boundaries). So the split costs a thin wrapper + handle-threading and
buys nothing. The filesystem is likewise **intrinsic to the sandbox**, so there
is no separable `fs` to mis-match either.

One object; the concrete subclasses *are* the backends:

```python
class Sandbox(ABC):            # config + lifecycle + ops, in ONE object
  spec: SandboxSpec
  def up(self) -> None: ...               # provision (subsumes _prepare_workspace)
  def mount(self, mounts: Mounts) -> None: ...# stage inputs — §3/§4
  def run(self, script_name, *, timeout, ...) -> ExecResult: ...
  def read(self, name) -> bytes: ...       # + the minimal ad-hoc read/write
  def down(self) -> None: ...              # best-effort teardown; never raises

class DockerHostSandbox(Sandbox): ...      # shipped by swe-lab
class GitHubJobSandbox(Sandbox): ...        # shipped by swe-lab
# class AcmeSandbox(Sandbox): ...          # a company's OWN infra — user-authored,
                                            #   out-of-tree, import-only (not shipped)
```

"Choosing a backend" is choosing which `Sandbox` subclass + config to construct;
the `SandboxManager` drives the lifecycle on this one object. This also removes
the frozen + `replace(handle=…)` gymnastics and settles the earlier "why is
`Sandbox` a thin wrapper?" question — there is no wrapper.

- **Capability narrowing** (observers must not call `up`/`down`) is preserved by
  handing observers a **narrower view** — a `run`/`read`/`write`/`mount`
  interface without the lifecycle methods — an interface split, not a second
  stateful class.
- **The one real split, deferred:** if a provider needs a shared authenticated
  **client / connection pool** across many sandboxes, that is a stateful
  `Client : Session` split (the client has its *own* `up`/`down` + state), **not**
  the frozen-factory fiction — introduce it as a construction dependency of the
  `Sandbox` if/when a real provider demands it; do not keep the split on its
  behalf now.

### 3. Materialize-in / persist-out is an OPEN host↔sandbox transfer seam — interface deferred

Getting mount **inputs** into the sandbox and pulling **artifacts** out is owned
by **neither** the sandbox alone nor the host alone: it is a transfer *both*
decide, and the only/best path depends on the **(source × backend)** pair. All
of these must remain expressible — so we must **not** bake a fixed method or
direction:

- the host downloads a `Url` / reads a `LocalFile`, then uploads it via the
  sandbox's own FS API;
- the sandbox fetches the `Url` / object-store ref **itself** (the host never
  touches it — sometimes cannot even reach it);
- A-host shares a directory, so the host writes and the sandbox sees it with
  **zero transfer**;
- persist: the host pulls declared artifacts through the sandbox API, **or** the
  sandbox pushes them to the store directly.

So there is **no** `stage(bytes)`, no "sandbox pulls everything", no "host writes
a dir" — and, crucially, **materialization is not a method on `Resource`.** The
same `LocalFile` is copied into a shared dir by a host backend but uploaded by a
remote one; for an object-store ref we may not know *until the concrete
(sandbox × host) pair* whether it is fetched from the host or from inside the
sandbox. A resource therefore cannot own "how I am materialized".

Instead:

- **`Resource` is extensible data** — variants (`Inline` / `LocalFile` / `Url` /
  `ObjectStore` / …) carrying **enough info**, with **no transfer behavior** on
  it (today's `materialize_to` / `local_path` removed). Adding a kind is a new
  subclass — **import-only**.
- **The receiver decides the transfer** — the live `Sandbox`, coordinated by the
  manager, inspects the resource and materializes it (shared-dir copy,
  host-mediated upload, sandbox-direct fetch, …), handling the kinds it knows and
  **failing loudly** on ones it does not.

**Extensibility (library requirement), kept simple.** swe-lab is imported, not
edited, and the real internal case is a company with its **own sandbox infra**.
The target: *"at Company A I write `AcmeSandbox(Sandbox)` against our infra, and
because we stage from our internal object store I also add
`AcmeObjectStore(Resource)` — using only `import` + subclassing, without touching
swe-lab."* Both axes are subclassed **together and paired**: `AcmeSandbox`'s
transfer logic handles `AcmeObjectStore` (its own kind) plus the built-in kinds
the composition still produces (the prompt is `Inline`, the binary a
`LocalFile`). The mechanism is ordinary override + `super()`: swe-lab's base
`Sandbox` handles the built-in kinds via reusable helpers, and a subclass adds
its own kinds and delegates the rest — so `AcmeSandbox` writes only the
object-store transfer, not the `Inline`/`LocalFile` plumbing. We deliberately do
**not** build a capability-negotiation framework to make an arbitrary resource
work with *every* built-in backend — since a user writes their own paired
backend, that machinery is unnecessary abstraction.

The **concrete transfer/persist interface and the exact `Resource` surface are
deferred to the Task-14 design**, after the real (source × backend) matrix is
enumerated — precisely so this ADR does not over-assume them.

### 4. One `Mount` type; "asset" is a wording convention, not an interface

Today there are **two** types with two interfaces: `Mounts = dict[str, Mount]`
(`Mount(resource, executable)`, workspace-relative, read/write) and `Assets =
dict[str, Resource]` (bare `Resource`, fixed path, read-only). But "mount a
resource into the sandbox" is **the same operation** for both — the difference
is only in **constraints** (read-only vs read/write; a workspace-relative name
vs an absolute path). They must not be two interfaces.

Keep **`Mount` / `Mounts`** as the one type; carry the constraints as
*attributes*:

```python
@dataclass(frozen=True)
class Mount:
  resource: Resource     # what content (extensible data, §3)
  executable: bool = False
  read_only: bool = False
type Mounts = dict[str, Mount]   # target path (workspace-relative or absolute) → mount
```

**"Asset" stops being a type and becomes a wording convention** — it just means
"a mount the model shouldn't normally modify" (a `read_only` mount, typically at
an absolute path, e.g. the pinned binary). No `Asset`/`Assets` type, no
`with_assets` seam. The composition provides **one** `Mounts` collection; the
receiver stages them all through the §3 seam, honoring each mount's constraints
(a host backend bind-mounts `read_only` ones `:ro`, a remote copies + revokes
write, etc.). This also fixes a real inconsistency: **the old `Assets` had no
`executable` field**, yet the pinned agent binary *is* an executable read-only
mount — the omission is why the A-ghjob backend had to *infer* the bit from the
source mode (the exec-bit fix); a `Mount` declares `executable` explicitly.

## Alternatives Considered

- **Only reorder `materialize` after `up`, keep `workspace: Path`.** Rejected:
  a half-measure. It fixes staging order but leaves every observer/grader doing
  host-side I/O on `sb.workspace`, which a remote sandbox cannot serve.
- **A host-mirror shim** (sync a host dir to/from the remote around each op).
  Rejected: chatty, race-prone, and still leaks a host `Path` through the whole
  observer layer — it hides the remote behind a leaky local dir instead of
  modeling it.
- **A separate engine for remote sandboxes.** Rejected: defeats the spec's
  "one engine, pluggable backend" goal and duplicates the manager + every
  observer.
- **Keep host-only; run remote work outside this engine.** Rejected: remote is
  P0 and must compose with the same harness/dataset/eval-method axes and
  observers; a parallel path would fork the whole stack.
- **Keep the `Backend` / `Sandbox` split** (a frozen backend factory producing a
  thin sandbox handle). Rejected: the sandbox genuinely has a lifecycle + state,
  so "frozen factory" is a fiction, and its only payoff — *one backend : many
  sandboxes* reuse — is never used (the engine is one-sandbox-per-run; batching
  is outside). The split costs a wrapper + handle-threading for nothing.
  Capability-narrowing (no `up`/`down` for observers) is kept with a narrower
  view, not a second class.
- **A separate `SandboxFs` type paired with the backend.** Rejected: a
  sandbox's FS is intrinsic to it (a remote-only FS can't run against a host
  sandbox), so a separable `fs` field is a mis-matchable footgun — the file ops
  belong on the one live `Sandbox`.
- **Pin a concrete staging/transfer interface now** (e.g. `stage(bytes)` /
  "the sandbox owns all file transfer"). Rejected: materialize-in and
  persist-out are **(source × backend) joint decisions** — host-mediated
  upload, sandbox-direct fetch, and shared-dir zero-copy are all valid and
  differ per pair. Freezing one method/direction bakes a wrong assumption;
  the transfer seam's shape is designed in Task 14 against the real matrix.

## Consequences

### Code changes (by file)

| File | Change |
|---|---|
| `sandbox/manager.py` | Reorder `sandbox()` to up-first; delete `_prepare_workspace`; the manager holds no host `workspace: Path` — it drives the one `Sandbox`'s lifecycle (`up → mount → … → down`). |
| `sandbox/backend.py` → `sandbox/sandbox.py` | Merge `Sandbox` + `SandboxBackend` into one `Sandbox` **ABC** (config + `up`/`mount`/`run`/`read`/`down`); concrete subclasses are the backends. `materialize`/`with_assets`/the handle field are gone. `ExecResult` unchanged. Observers get a narrower view (no `up`/`down`). |
| `sandbox/resources.py` | `Resource` is **extensible data** — variants + enough info, **no transfer behavior** (`materialize_to`/`local_path` removed). A new kind is a subclass (import-only), handled by a backend that knows it. **Exact surface designed in Task 14.** |
| `sandbox/mounts.py` | Keep `Mount`/`Mounts`; add `read_only` (and keep `executable`) to `Mount`. Drop the `Assets` type + `with_assets` seam — "asset" becomes a wording convention for a read-only `Mount` (the binary is an executable read-only mount). |
| `sandbox/backends/host.py` | `DockerHostBackend` → `DockerHostSandbox(Sandbox)`: `up` = `docker create`+`start` over a self-owned dir; shared-dir zero-copy `mount`; `run`/`read`/`down` over that container. |
| `sandbox/backends/ghjob.py` | `GitHubJobBackend` → `GitHubJobSandbox(Sandbox)`: `up` provisions the local dir; `mount` handles read-only + executable (the exec-bit fix stays); `run`/`read`/`down` local. |
| _(no shipped remote backend)_ | swe-lab ships only `DockerHostSandbox` + `GitHubJobSandbox`. A **remote / internal** sandbox is authored by the consuming company as their own `Sandbox` subclass (import-only) — see Extensibility. |
| `sandbox/testing.py` | `FakeBackend` → `FakeSandbox(Sandbox)`; add a `FakeRemoteSandbox` (in-memory, no host dir) — an out-of-tree-style subclass proving the seam without a network. |
| `cli/*.py` (`build_backend`) | `--backend host\|ghjob` selects which `Sandbox` subclass to construct (was: which backend). |
| `sandbox/observers/diff_extract.py`, `conversation/observer.py`, `evaluation/methods/unit_test/run.py` (grader call) | `sb.workspace / name` → the live sandbox's read/write ops; `grade` takes the sandbox (or its read op), not a host `Path`. |
| `Contribution.artifacts` / `RunResult.artifacts` (`dict[str, Path]`) | Generalize away from host `Path` (not meaningful for remote) → logical names resolved through the transfer seam; `PersistObserver` (Task 12) pulls declared artifacts via that seam. |
| `docs/horizontal/spec.md`, `docs/horizontal/workspace-layout.md` | Supersede "shared state = host workspace directory" → "shared state is the live sandbox's own filesystem, reached through its ops; host backends realize it as a directory." |

### Migration (phased; suite green at each step)

1. **Enabling refactor (host-only, no behavior change).** Merge Backend/Sandbox
   into one lifecycle-bearing `Sandbox` + up-first lifecycle; `Resource` → pure
   data; the receiver-decides transfer seam realized for A-host/A-ghjob
   (shared-dir); port observers/grader off the host `Path` onto the sandbox's
   ops. A-host/A-ghjob behave identically;
   full suite + the flipt `eval`/`rollout` E2E stay green. This lands the
   abstraction with zero functional change and enumerates the (source × backend)
   matrix that pins the transfer interface.
2. **Prove the seam + author guide (no shipped remote backend).** swe-lab does
   **not** ship a remote backend — internal users own theirs. Instead: a
   Docker-free `FakeRemoteSandbox` (an out-of-tree-style subclass with its own
   `Resource` kind) proves `claude_code × swebench_pro × unit_test` composes on
   it unchanged and that a company can add a `Sandbox` + `Resource` by import
   only (spec Success #3/#4); plus a short author guide/example.
3. **Artifacts + persistence generalization.** Move `RunResult.artifacts` off
   host `Path` and make `PersistObserver` pull declared artifacts through the
   same transfer seam, so persistence (Task 12) works off-host too.

### Sequencing

- **This refactor precedes Task 12 (`Store` seam + persistence).** Task 12 adds
  an observer that persists artifacts; building it on the host-`Path` assumption
  would weld that assumption deeper. Land the lifecycle + transfer seam first,
  then Task 12 on top of it.
- Proposed task index entries (horizontal `plans/`): **Task 14** — merged
  lifecycle-bearing `Sandbox` + up-first lifecycle + the receiver-decides
  transfer seam (Phase 1); **Task 15** — extensibility seam proof + author guide
  (Phase 2; no shipped remote backend). Task 12 rebases onto the transfer seam
  (Phase 3).

### Notes

- **Scope:** swe-lab ships `DockerHostSandbox` + `GitHubJobSandbox` only.
  Remote / model-hosted / company-internal sandboxes are authored by the
  consuming company as their **own** `Sandbox` subclasses (import-only). This
  ADR makes that possible and proves it (Task 15's seam test + guide); it does
  **not** ship a remote backend.
- `up`-first is strictly more general, so A-host/A-ghjob lose nothing; the
  reorder is not a compatibility break for them.
- This ADR **amends the spec's core model** (the host-FS workspace assumption
  *and* the Backend/Sandbox split); the spec's status note points here.
- `Sandbox` is a behavior interface (ABC, per ADR-0002); its concrete subclasses
  are the backends. `Resource` is **re-classified** to a **data shape** (extensible
  variants) — the *transfer* behavior it carried (`materialize_to`) moves to the
  receiver.
- **Extensibility is a first-class constraint, kept simple:** swe-lab is
  imported, not edited, and users subclass **both** `Resource` and `Sandbox`.
  A new `Resource` kind ships with (or reduces to something handled by) a backend
  — all import-only. No capability-negotiation framework (a user can write their
  own backend, so it is unnecessary). Task 14 must preserve import-only
  extensibility on both axes.

## Amendment (2026-07-28): finish dropping `workspace: Path` at the construction seam

Task 14 removed the host `workspace: Path` from the `Sandbox` ABC, the manager,
and the observer/grader layer (they go through the sandbox's own read/write/run
ops). One place kept it, though: the **construction seam**. `SandboxFactory` was
`Callable[[SandboxSpec, Path, SandboxConfig], Sandbox]` and `build_sandbox` took
a required positional `workspace: Path` — so every backend registered through
the open registry, including a company's remote one, was still handed a host
path it cannot honor. That reintroduced, at construction time, exactly the
assumption this ADR set out to remove (and the rejected host-mirror shim warned
against).

Fix: `workspace` moves **into `SandboxConfig`** as an optional field (`Path |
None`), alongside the other backend-specific options each factory takes only
what it needs (`network` / `pull` are A-host-only; `workspace` is local-only).
`SandboxFactory` becomes `Callable[[SandboxSpec, SandboxConfig], Sandbox]`;
`build_sandbox` keeps `workspace` as a keyword and folds it into the config; the
local `host` / `ghjob` factories read `config.workspace` and raise if it is
absent, while a remote backend ignores it. No behavior change for the shipped
backends — the CLIs still pass the same directory — and the generic seam no
longer bakes in a host filesystem.
