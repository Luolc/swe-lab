# ADR-0003: Remote-sandbox support — up-first lifecycle + workspace as a sandbox FS capability

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

Generalize the engine so the sandbox's filesystem is an **interface obtained
from the live sandbox**, not a host path known before it exists. Four coupled
changes:

### 1. Up-first lifecycle

Reorder `SandboxManager.sandbox()` so nothing touches the sandbox filesystem
before it is live:

```
before_create → backend.up(spec) → [stage mounts + place assets THROUGH the
live sandbox] → after_create → yield sb (body) → before_destroy → backend.down
```

`up` provisions the sandbox and returns the handle; **it no longer receives a
host workspace path for staging**. This ordering is *strictly more general*:
A-host (bind-mount the backend-owned dir, then stage into it) and A-ghjob
(local dir) work unchanged under it, and it is the only ordering a remote
sandbox can satisfy.

### 2. `workspace: Path` → `sb.fs: SandboxFs`

Replace the raw host path on `Sandbox` with a **filesystem capability bound to
the live sandbox**:

```python
class SandboxFs(ABC):                       # obtained from a live sandbox
  def write(self, name: str, data: bytes, *, executable: bool = False) -> None: ...
  def read(self, name: str) -> bytes: ...
  def exists(self, name: str) -> bool: ...

@dataclass(frozen=True)
class Sandbox:
  label: str
  spec: SandboxSpec
  backend: SandboxBackend
  handle: str
  fs: SandboxFs            # replaces `workspace: Path`
  def run(self, script_name, *, timeout, ...) -> ExecResult: ...
```

Host backends realize `SandboxFs` over a real directory (a plain read/write);
remote backends realize it over the provider's file API (upload/download). The
manager and observers depend only on `SandboxFs` — the host directory becomes a
backend-internal detail, exposed (for persistence/debug) only through an
explicit escape hatch (e.g. `fs.local_path(name) -> Path | None`, mirroring
`Resource.local_path`).

### 3. `materialize` → `stage`, after `up`, through the sandbox

Staging becomes "push these mounts into the *live* sandbox," keyed by handle,
run **after** `up`. `Resource` grows a `read_bytes()` (Inline returns its bytes;
LocalFile reads the file; Url downloads) so staging is
`sb.fs.write(name, resource.read_bytes(), executable=...)` regardless of
backend — no host `dest` path required. `materialize_to(dest: Path)` stays as a
host convenience but is no longer on the staging hot path. Read-only **assets**
are likewise placed after `up` (A-host, which bind-mounts assets at
`docker create`, keeps a backend-owned host dir it can bind-mount and then
populate; remote copies via API).

### 4. Provisioning moves into the backend

Delete `SandboxManager._prepare_workspace`. Workspace/sandbox provisioning
(mkdir + empty-check for host backends; API provisioning for remote) becomes the
backend's `up` responsibility. The manager never touches a host dir.

### Backend ABC (new shape)

```python
class SandboxBackend(ABC):
  def up(self, spec: SandboxSpec) -> str: ...              # provision; return handle (no host path)
  def fs(self, handle: str) -> SandboxFs: ...              # file capability for this live sandbox
  def stage(self, handle: str, mounts: Mounts) -> None: ...# push mounts in, AFTER up
  def run_script(self, handle, name, *, timeout, env=None, stream_to=None) -> ExecResult: ...
  def down(self, handle: str) -> None: ...
  # with_assets stays construction-time config; asset PLACEMENT happens in up/stage.
```

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

## Consequences

### Code changes (by file)

| File | Change |
|---|---|
| `sandbox/manager.py` | Reorder `sandbox()` to up-first; delete `_prepare_workspace`; `Sandbox.workspace: Path` → `fs: SandboxFs`; `sb.run` unchanged (still handle-bound). |
| `sandbox/backend.py` | New ABC: `up(spec)->handle` (no path), `fs(handle)->SandboxFs`, `stage(handle, mounts)`; `materialize(mounts, workspace)` removed. New `SandboxFs` ABC + `ExecResult` unchanged. |
| `sandbox/resources.py` | Add `Resource.read_bytes()`; `materialize_to`/`local_path` retained for host convenience. |
| `sandbox/backends/host.py` | `DockerHostBackend` owns its workspace dir; `up` creates + bind-mounts it (create → start), returns handle; `fs` reads/writes that dir; `stage` writes host-side; assets bind-mounted at create as today. |
| `sandbox/backends/ghjob.py` | `GitHubJobBackend.up` provisions the local dir + places assets (the exec-bit fix stays); `fs`/`stage` over the local dir. |
| `sandbox/backends/remote.py` **(new)** | The P0 remote backend against the provider API: `up` provisions; `fs`/`stage` via upload/download; `run_script` via the exec API; `down` tears down. |
| `sandbox/testing.py` | `FakeBackend` gains `fs`/`stage`; add a `FakeRemoteBackend` (in-memory FS) proving the seam without a network. |
| `sandbox/observers/diff_extract.py`, `conversation/observer.py`, `evaluation/methods/unit_test/run.py` (grader call) | `sb.workspace / name` → `sb.fs.read/write`; `grade` takes an fs, not a `Path`. |
| `Contribution.artifacts` / `RunResult.artifacts` (`dict[str, Path]`) | Generalize to logical names resolved through `sb.fs` (a host `Path` is not meaningful for remote). Ripple into `PersistObserver` (Task 12), which must pull declared artifacts via `sb.fs`. |
| `docs/horizontal/spec.md`, `docs/horizontal/workspace-layout.md` | Supersede the "shared state = host workspace directory" model → "shared state = the sandbox's filesystem via `sb.fs`; host backends realize it as a directory." |

### Migration (phased; suite green at each step)

1. **Enabling refactor (host-only, no behavior change).** Introduce `SandboxFs`
   + up-first lifecycle; back `fs` with the existing dir in A-host/A-ghjob; port
   observers/grader to `sb.fs`; generalize `Resource.read_bytes` + `stage`.
   A-host/A-ghjob behave identically; full suite + the flipt `eval`/`rollout`
   E2E stay green. This lands the abstraction with zero functional change.
2. **Remote backend (the P0 capability).** Add `sandbox/backends/remote.py`
   against the target provider + a Docker-free `FakeRemoteBackend` for tests;
   prove `claude_code × swebench_pro × unit_test` composes on it unchanged
   (spec Success #3/#4). Live validation is a manual run (like CP2/CP3).
3. **Artifacts + persistence generalization.** Move `RunResult.artifacts` to
   fs-resolved names and make `PersistObserver` pull via `sb.fs`, so persistence
   (Task 12) works for remote too.

### Sequencing

- **This refactor precedes Task 12 (`Store` seam + persistence).** Task 12 adds
  another observer that reads the workspace to persist artifacts; building it on
  the host-`Path` assumption would weld that assumption deeper. Do the
  workspace-fs abstraction (Phase 1) first, then Task 12 on top of `sb.fs`.
- Proposed task index entries (horizontal `plans/`): **Task 14** — workspace-fs
  abstraction + up-first lifecycle (Phase 1); **Task 15** — remote sandbox
  backend (Phase 2). Task 12 rebases onto `sb.fs` (Phase 3).

### Notes

- `up`-first is strictly more general, so A-host/A-ghjob lose nothing; the
  reorder is not a compatibility break for them.
- This ADR **amends the spec's core model** (the host-FS workspace assumption);
  the spec's status note should point here for the workspace/lifecycle design.
- Backends stay behind one ABC (ADR-0002); the new `SandboxFs` is a behavior
  interface (ABC), and `Resource` remains the shared content source.
