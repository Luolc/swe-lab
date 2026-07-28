# Task 09 — A-ghjob backend

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.

---

## 1. Purpose & scope

Add the **second** `SandboxBackend` — **A-ghjob**, where the GitHub job *is* the
container: `exec` runs in the job's own shell, the workspace is a plain local
directory, and there is no `docker create/start/exec/rm`. The same engine
composition (manager + observers + harness/eval-method) runs **unchanged** on
either backend; the backend is chosen by config (spec Success #4).

The elegant consequence: A-ghjob's "container" is the local shell, so — unlike
A-host — it is **fully unit- and integration-testable in CI with real bash** (no
Docker, no marker), which is a large part of this task's coverage.

### In scope

- `sandbox/backends/ghjob.py`: `GitHubJobBackend(SandboxBackend)` — `up` (copy
  assets read-only, trivial handle), `run_script` (local `/bin/bash` with
  `SANDBOX_WORKSPACE` set), `down` (best-effort), `with_assets`; inherits
  `materialize` unchanged.
- Backend selection as config: a `BackendKind` + `build_backend` factory,
  exported and wired into `cli/rollout.py` + `cli/eval.py` (`--backend
  host|ghjob`).
- A **container-job** workflow variant (manual `workflow_dispatch`) proving the
  model — the job runs *inside* the instance image and calls
  `python -m swe_lab … --backend ghjob`.
- Real Docker-free tests: backend argv/behaviour + a manager composition run
  end-to-end over `GitHubJobBackend`.

### Out of scope

- Changing the manager/observers/harness/eval-method (they are already
  backend-agnostic — they touch only `$SANDBOX_WORKSPACE` and `spec.workdir`).
- Removing A-host or the `docker run` workflows (10b territory).
- A live green container-job dispatch is **manual** (needs the large per-instance
  image, on Docker Hub); CI proves the backend + composition locally.

## 2. Module layout

```
sandbox/backends/
  ghjob.py            GitHubJobBackend(SandboxBackend)
  __init__.py         + GitHubJobBackend, BackendKind, build_backend
sandbox/__init__.py   + re-exports
cli/rollout.py        + --backend (build_backend)
cli/eval.py           + --backend (build_backend)
.github/workflows/
  rollout-ghjob.yml   container-job variant (manual)
```

Tests: `tests/test_ghjob_backend.py` (real local bash — no Docker),
extend `tests/test_sandbox_manager.py` or a new
`tests/test_ghjob_composition.py` (a manager run over the real backend).

## 3. Key types & signatures

```python
# ─── sandbox/backends/ghjob.py ──────────────────────────────────────────────
@dataclass(frozen=True)
class GitHubJobBackend(SandboxBackend):
  """The job IS the container: exec runs in the job shell; workspace is local.

  No image pull / container create — the job already runs inside the instance
  image. `network`/`pull`/`platform` are Docker concepts and absent here.

  Attributes:
    env: KEY=VALUE variables set on each exec.
    pass_env: Names inherited by reference from the host (value never on argv).
    assets: Read-only resources copied to fixed paths at `up` (kept read-only).
  """
  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Sequence[str] = ()
  assets: Assets = field(default_factory=dict)

  def with_assets(self, assets) -> SandboxBackend:      # merge-and-replace
  def up(self, spec, workspace) -> str:                 # copy assets ro; return str(workspace)
  def run_script(self, handle, script_name, *, timeout, env=None, stream_to=None) -> ExecResult:
      # ws = Path(handle); run ["/bin/bash", ws/script_name] with
      # env = {**os.environ-inherited pass_env, **self.env, **env,
      #        SANDBOX_WORKSPACE: ws}; reuse host.py's stream/capture/124 logic
  def down(self, handle) -> None:                       # best-effort; never raises

# ─── sandbox/backends/__init__.py ───────────────────────────────────────────
class BackendKind(StrEnum): HOST = "host"; GHJOB = "ghjob"

def build_backend(kind, *, network=True, pull=True,
                  env=None, pass_env=()) -> SandboxBackend:
  """One config seam for both CLIs. GHJOB ignores network/pull (Docker-only)."""
```

## 4. What A-ghjob does differently from A-host

| Concern | A-host (`DockerHostBackend`) | A-ghjob (`GitHubJobBackend`) |
|---|---|---|
| bring-up | `docker pull` + `create` + `start`; keep-alive entrypoint | **none** — the job is already the live container; `up` just places assets |
| workspace | bind-mounted at `mount_at` (`/workspace`) | the local dir itself; `SANDBOX_WORKSPACE` = that path |
| `materialize` | inherited default (write into workspace) | inherited default (identical) |
| assets | bind-mount `:ro`; **rejects `Inline`** (`host.py:113`) | `materialize_to` + `chmod a-w` — handles `Inline` too |
| exec | `docker exec … /bin/bash mount_at/script` | `/bin/bash workspace/script` locally, `SANDBOX_WORKSPACE` in `env=` |
| `SANDBOX_WORKSPACE` | `mount_at` | the runner workspace path |
| teardown | `docker rm -f` | best-effort (nothing to remove; restore asset perms) |

The stream-vs-capture branching and `TimeoutExpired → ExecResult(124,…)` mapping
are **identical** to `host.py:194-218`; only the argv prefix differs (`/bin/bash`
locally vs `docker exec … /bin/bash`). The harness invocation script and the
diff-extract script reference only `$SANDBOX_WORKSPACE` and `cd $WORKDIR`, so the
**same script text runs unchanged** — that is the parity guarantee (spec
§backend contract).

## 5. Backend selection is config, one seam

The manager and compositions (`solve.py`, `run_unit_test`) already take
`backend: SandboxBackend` and never name a concrete class, so the *only* change
is at the CLI construction sites. A `build_backend(kind, …)` factory
encapsulates that A-ghjob ignores the Docker-only `network`/`pull`, so both CLIs
gain a single `--backend host|ghjob` option and one factory call — no per-CLI
branching. The one composition-level backend touch,
`backend.with_assets(harness.assets())` in `solve.py:89`, exercises A-ghjob's
asset-copy path (the pinned binary is copied read-only rather than bind-mounted).

## 6. Testing — real bash, no Docker

Because A-ghjob execs locally, its tests are genuine end-to-end runs in CI:

- **`up` places assets read-only** — an `Inline` and a `LocalFile` asset both
  land at their fixed paths and are not writable (proves the A-host `Inline`
  rejection is lifted).
- **`run_script`** runs a real staged script by `$SANDBOX_WORKSPACE` path, sees
  `env`/`pass_env`, returns exit code + output; `stream_to` writes stdout to a
  file; a `sleep` script with a tiny timeout returns `ExecResult(124,
  timed_out=True)`.
- **`down` never raises**.
- **Composition parity** — a `SandboxManager` run with a `RecordingObserver` and
  a trivial main over `GitHubJobBackend` produces the same lifecycle/`RunResult`
  as the `FakeBackend` path, on a real workspace.

The container-job **workflow** is validated by a manual `workflow_dispatch`
(needs the real instance image); CI does not run it.

## 7. Open questions (decided under full-auto; revisit on review)

1. **Asset placement outside a bind mount.** On A-ghjob an asset is copied to
   its fixed absolute path (e.g. `/opt/claude-code/claude`) on the runner, which
   needs write permission to that path. Decision: copy + `chmod a-w`; the
   container-job runs as root in the instance image, so `/opt/...` is writable.
   Recorded as a constraint the workflow documents.
2. **Handle value.** `up` returns `str(workspace)` as the opaque handle (the
   manager only needs it truthy and passes it back to `run_script`), so
   `run_script` recovers the workspace from the handle — no extra state on the
   frozen backend.
3. **Workflow shape.** A single rollout container-job variant
   (`container: image: <instance ref>`), manual dispatch, is enough to prove the
   model for CP3; an eval variant is a trivial fork, deferred until needed.
