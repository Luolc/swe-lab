# Task 02 — Engine core: `sandbox/` package + fake backend

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) (§The core model, §Declarative mounts, §Phase→hook
> mapping). Grounded in the current code with `file:line` citations (all
> `src/swe_lab/…` at commit `9667fff`). Open items for the user are in §8.

---

## 1. Purpose & scope

Build the harness-/dataset-/eval-method-agnostic engine: a `SandboxManager`
that owns a container's lifecycle and drives composed `SandboxObserver`s around
one main action, with declarative `Mounts`, a pure-handle `Sandbox`, and an
aggregated `RunResult`. Prove the whole lifecycle — including the failure
matrix — against a **fake backend with zero Docker use**. This is the
foundation every later task plugs into; its interface mistakes are the most
expensive, so this task is interface-first and test-heavy.

### In scope

- The `sandbox/` package: `SandboxSpec`, `Mount`/`Mounts` (+ merge &
  materialize), `Sandbox` (pure handle), `ExecResult`, `RunStatus`/`RunResult`
  /`Contribution`, `SandboxObserver` (five hooks) + `CompositeObserver`,
  `SandboxManager`, `SandboxBackend` protocol.
- `FakeBackend` as a **public** testing double (`sandbox/testing.py`) — later
  tasks (04, 06) reuse it for their own Docker-free tests.
- Lifecycle unit tests: hook ordering, contribution aggregation, mounts, and
  the full failure matrix.

### Out of scope (later tasks / deliberately absent)

- Any real backend (03 A-host, 09 A-ghjob), harness, dataset, or eval-method.
- The on-error *observer* (spec: P1 — the `on_error` **hook** ships here, no
  implementation of a diagnostics observer).
- Persistence (`Store` seam — task 12); metrics/logging observers beyond the
  `Contribution` plumbing they need.
- Retry (a cross-cutting observer/wrapper later if needed; current retry logic
  lives in W1's `invoke_with_retries` and is untouched).

## 2. Module layout (`src/swe_lab/sandbox/`)

Small, one-concern modules per the repo's code style (spec §Code Style):

```
sandbox/
  __init__.py    Curated exports (the package's public API).
  errors.py      SandboxError (own module: mounts needs it, backend doesn't
                 own it — avoids a mounts→backend dependency).
  spec.py        SandboxSpec.
  mounts.py      Mount, Mounts, merge_mounts(), materialize().
  result.py      RunStatus, Contribution, RunResult, merge_contributions().
  observer.py    SandboxObserver (five no-op hooks), CompositeObserver.
  backend.py     SandboxBackend protocol, ExecResult, WORKSPACE_ENV.
  manager.py     Sandbox + SandboxManager (the lifecycle driver).
  testing.py     FakeBackend + RecordingObserver (public test doubles).
```

Tests: `tests/test_sandbox_mounts.py`, `tests/test_sandbox_manager.py`
(lifecycle + failure matrix), `tests/test_sandbox_observer.py`.

## 3. Key types & signatures

Concretized from the spec sketch (spec §The core model), with two deltas the
sketch left open — each justified in §5 and flagged in §8: `exec` taking
script *text* (§5.5) and `SandboxError` as the engine's error type (§5.6).
Typed per-axis results (e.g. a verdict) live on **stateful observers**, not on
`RunResult` — see §5.4.

```python
# ─── spec.py ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SandboxSpec:
  """What it takes to bring one instance's sandbox up (the run context)."""

  instance_id: str
  image_ref: str
  workdir: str          # where the repo lives inside the image (e.g. /app)
  base_commit: str

# ─── mounts.py ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Mount:
  """One file to materialize into the workspace before the sandbox comes up.

  Exactly one of ``content``/``source`` is set (validated in __post_init__).
  """

  content: bytes | None = None   # small, runtime-generated (scripts)
  source: Path | None = None     # large, host-cached (the ~100MB agent binary)
  executable: bool = False

type Mounts = dict[str, Mount]   # key = workspace-relative target path

def merge_mounts(*contributions: Mounts) -> Mounts: ...
    # duplicate target path → SandboxError (loud, never a silent overwrite)

def materialize(mounts: Mounts, workspace: Path) -> None: ...
    # writes/copies each entry; chmod +x when executable; parents created

# ─── backend.py ─────────────────────────────────────────────────────────────
class SandboxError(RuntimeError):
  """The engine failed to drive the sandbox lifecycle."""

@dataclass(frozen=True)
class ExecResult:               # shape matches today's ContainerRun
  exit_code: int                #   (core/docker/provider.py:34-46)
  stdout: str
  stderr: str
  timed_out: bool = False

  @property
  def ok(self) -> bool: ...

class SandboxBackend(Protocol):
  """One way to realize a live sandbox (A-host now, A-ghjob later)."""

  def up(self, spec: SandboxSpec, workspace: Path) -> str: ...
      # returns an opaque backend handle (e.g. container id)
  def exec(
      self,
      handle: str,
      script: str,                       # bash text, not a path — see §5.5
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
      stream_to: Path | None = None,     # stream stdout to a file — see §5.7
  ) -> ExecResult: ...
  def down(self, handle: str) -> None: ...   # never raises; best-effort rm

# ─── result.py ──────────────────────────────────────────────────────────────
class RunStatus(StrEnum):
  SUCCESS = "success"
  SETUP_ERROR = "setup_error"   # before_create/mounts/up/after_create failed
  RUN_ERROR = "run_error"       # body or a before_destroy observer failed

@dataclass(frozen=True)
class Contribution:
  """What one observer hook hands back for the manager to aggregate.

  Only the engine-generic shapes live here (see §5.4): artifact *references*
  into the workspace and scalar metrics. Typed per-axis results (a verdict)
  live on the stateful observer that produced them instead.
  """

  artifacts: dict[str, Path] = field(default_factory=dict)
  metrics: dict[str, float] = field(default_factory=dict)

@dataclass(frozen=True)
class RunResult:
  """The engine-level outcome, assembled ONCE at teardown (§4 step 10) —
  never incrementally updated during the run."""

  label: str
  status: RunStatus
  artifacts: dict[str, Path]
  metrics: dict[str, float]
  error: BaseException | None = None

# ─── observer.py ────────────────────────────────────────────────────────────
class SandboxObserver:
  """All hooks no-op by default; override what you need (spec §The observer)."""

  def mounts(self) -> Mounts: ...                                # default {}
  def before_create(self, sb: Sandbox) -> None: ...
  def after_create(self, sb: Sandbox) -> None: ...               # SETUP here
  def before_destroy(self, sb: Sandbox) -> Contribution | None: ...
  def after_destroy(self, sb: Sandbox) -> None: ...
  def on_error(self, sb: Sandbox, error: BaseException) -> Contribution | None: ...

class CompositeObserver(SandboxObserver):
  """Fan out to a list in registration order; merge Mounts contributions."""

# ─── manager.py ─────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Sandbox:
  """Pure handle to the live container — nothing mutable accumulates on it."""

  label: str
  spec: SandboxSpec
  workspace: Path

  def exec(self, script, *, timeout, env=None, stream_to=None) -> ExecResult:
    ...                                    # delegates to the bound backend

@dataclass
class SandboxManager:
  """Configuration fields + one private state slot — a dataclass, not a long
  hand-written __init__ (repo style: dataclass wherever the class is
  field-shaped)."""

  spec: SandboxSpec
  backend: SandboxBackend
  workspace: Path
  observers: Sequence[SandboxObserver] = ()
  mounts: Mounts = field(default_factory=dict)   # composition-level extras
  label: str = ""                     # "" → spec.instance_id (__post_init__)
  _result: RunResult | None = field(default=None, init=False, repr=False)

  @contextmanager
  def sandbox(self) -> Iterator[Sandbox]: ...

  @property
  def result(self) -> RunResult: ...
      # A guarded READ, not a live view: the RunResult is built exactly once,
      # in the finally of sandbox() (§4 step 10). Accessing it before then
      # raises SandboxError. It is a property (rather than a return value)
      # only because `with` blocks cannot return one — __exit__'s return
      # value already means exception suppression.
```

## 4. Lifecycle — step by step, and the failure matrix

`manager.sandbox()` drives (spec §The manager yields the sandbox):

1. Merge mounts: `merge_mounts(manager.mounts, *[o.mounts() for o in observers])`.
2. `before_create` hooks, in order.
3. `materialize(mounts, workspace)` — **before** `backend.up`, so A-host's
   bind-mount sees the files the moment the container starts.
4. `backend.up(spec, workspace)` → handle; build the `Sandbox`.
5. `after_create` hooks (dataset setup runs here; a raise aborts the run).
6. **yield** — the caller's body (the ONE main action).
7. On any error in 4–6: run `on_error` hooks (each caught; the sandbox is
   still live, so a hook *may* `exec` into it), remember the primary error.
8. `finally`: `before_destroy` hooks **always** run, in order, each
   individually caught (§5.3); contributions aggregate into the `RunResult`.
9. `finally`: `backend.down(handle)` — always, even when up/hooks failed.
10. `after_destroy` hooks; assemble `RunResult` (status per the matrix below).

### Failure matrix (each row is a dedicated test)

| Failing step | on_error runs? | before_destroy runs? | down runs? | status | error |
|---|---|---|---|---|---|
| `before_create` hook raises | no (no live sb) | no | no | SETUP_ERROR | hook's |
| `merge_mounts` duplicate target | no | no | no | SETUP_ERROR | SandboxError |
| `materialize` (missing source) | no | no | no | SETUP_ERROR | SandboxError |
| `backend.up` raises | no | no | backend-internal (up must clean its own partial state — the manager has no handle) | SETUP_ERROR | backend's |
| `after_create` hook raises | yes | yes | yes | SETUP_ERROR | hook's |
| body raises | yes | yes | yes | RUN_ERROR | body's |
| `before_destroy` hook raises (clean body) | no | rest still run | yes | RUN_ERROR | hook's |
| `before_destroy` hook raises (after body error) | already ran | rest still run | yes | RUN_ERROR | body's (primary) |
| `backend.down` raises | — | — | swallowed+logged | unchanged | unchanged |
| everything clean | no | yes | yes | SUCCESS | None |

Edge ledger beyond the matrix: `result` accessed before/inside the `with` →
`SandboxError`; observer contribution key collisions (two observers claim
`artifacts["patch.diff"]`) → `SandboxError` (no silent last-writer-wins, per
the spec's no-silent-loss boundary); `on_error` hook raising → caught + logged,
never masks the primary error (mirrors the discipline the spec set for the
future diagnostics observer).

## 5. Design decisions

### 5.1 Workspace is manager input, not backend output
Today both flows compute their workspace themselves and duplicate the staging
dance — `evaluate` at `core/datasets/swebench_pro/grading.py:163-186`, rollout
at `rollout/runner.py:107-126` (mkdir → clear stale files → write scripts).
In the engine the *caller/composition* picks the workspace path (CLI keeps the
per-instance cache-dir convention) and the engine owns mkdir + stale-file
hygiene via mounts materialization. Why not backend-owned: A-ghjob has no say
in it (workspace is just a local dir), and callers need the path for artifacts
after the run.

### 5.2 Mounts replace both staging copies
`evaluate` writes `patch.diff`/`run_script.sh`/`parser.py`/`entryscript.sh` by
hand (`grading.py:170-186`); rollout writes `prompt.txt`/`entryscript.sh` and
bind-mounts the binary (`runner.py:117-141`). Both become `Mounts`
contributions (spec §Declarative mounts). The stale-artifact clearing
(`grading.py:167-169`, `runner.py:113-115`) becomes engine policy: the
manager **refuses a non-empty workspace unless `reuse=True`** (settled, §8
Q3) — a fresh dir per run beats today's delete-known-names lists, which
silently miss any artifact name added later. Name collision note: today's `Mount` in `core/docker/provider.py:20-31`
is a *bind* mount (host→container path); the new `sandbox.mounts.Mount` is a
*materialized file*. They coexist until 10b deletes the old provider; the
A-host backend keeps bind-mount logic internal (task 03 §5).

### 5.3 `before_destroy` hooks are individually caught
Post-processing always runs (spec: "the `finally` semantics"). One extractor
failing must not cost us the persist/trace contributions of the others — the
failed hook's error is recorded (primary error wins if the body already
failed), the rest still run. This is the engine-level version of the lesson in
`core/agent/trace.py:120-124` (an unguarded parse of the last proxy line could
discard a whole successful run — audit P2 finding).

### 5.4 Typed results live on stateful observers; `Contribution` stays generic
(Settled with the user 2026-07-18, replacing an earlier `data: dict[str,
object]` draft.) Task 04's eval-parse observer produces a *typed verdict* —
but the caller constructed that observer and holds the reference, so the
observer simply keeps it in its own field (`verdict: V | None = None`, set in
`before_destroy`) and the caller reads it back fully typed. This is **not**
the "shared bag" the spec forbids — that rule targets mutating the shared
`Sandbox`/manager state; an observer mutating *itself* is visible, local, and
type-safe. `Contribution` therefore stays exactly the spec sketch —
`artifacts` (references into the workspace; the persist observer pushes T1
from this index rather than crawling the directory) and `metrics` (scalars) —
the two shapes whose **cross-observer union** only the manager can build and
that generic consumers use without knowing any observer's type. Consequence:
observers with result fields are single-run objects; reusing one across runs
is a bug (fresh observer per composition — the compositions in tasks 04/06
construct them per call).

### 5.5 `exec` takes script *text*; workspace paths resolve via `SANDBOX_WORKSPACE`

**The problem.** Script text is generated at *compile time* (dataset/eval-
method builds `eval_script`), but the workspace's in-container path is a
*backend, exec-time* fact: A-host bind-mounts it at `/workspace`; under
A-ghjob the job *is* the container and the workspace is an arbitrary local
path known only at runtime. Today a shared constant bridges the gap —
`MOUNT_AT = "/workspace"`, with comments insisting builder and runner use the
same constant (`swebench_pro/constants.py`, `rollout/constants.py`;
`grading.py:118-125` builds `/workspace/...` paths from it). One backend, so
it works; A-ghjob kills the constant.

**Alternatives weighed (settled with the user 2026-07-18):**

1. *Parameterize the generator* — pass the workspace path into the compile
   step. Feasible, but then `eval_script` can't be fixed at compile time:
   either `compile_unit_test` grows a backend-coupled argument (the dataset
   compile step now knows backend facts — the axis boundary leaks), or
   `UnitTestSpec.eval_script` becomes a `(workspace: str) -> str` callable
   (more machinery). Worse, the script stops being a **byte-stable artifact**:
   task-04's pinned old-builder parity fixture and any persisted/reproducible
   script record depend on "same compile → same bytes".
2. *Backend-side token substitution* (`{{WORKSPACE}}` replaced at exec time) —
   fragile: every substitution site must get quoting/escaping right (a path
   with spaces breaks it), plus accidental-token risk.
3. **Env var (chosen)** — scripts write `"$SANDBOX_WORKSPACE"/patch.diff`; the
   backend injects the value at exec (`docker exec -e` / job env). One quoting
   discipline handles all escaping; the script text is backend-independent
   and byte-stable; compile stays pure.

This is also the established solution to exactly this problem: GitHub Actions
sets `$GITHUB_WORKSPACE` rather than templating user scripts (likewise
`CI_PROJECT_DIR`, `BUILD_SOURCESDIRECTORY` …) — script author and execution
environment decouple by handshaking on a stable *name*. Bonus: `docker exec`
into a live container and script fragments run by hand, because the var is
just there.

**Known costs + mitigations:** one level of indirection (the reader must know
who sets it — this section is that record), and an unset var fails silently —
mitigated by the backend contract (both backends always set it; unit-tested),
with `set -u` in generated scripts as an optional hardening *after* CP1 (it
would break byte-parity with the pinned old-builder fixture, so not in the
port itself).

Passing script *text* (rather than a workspace path to a pre-staged file) is
the enabling half: the backend writes the text under the workspace and invokes
it by its own notion of the path — keeping `MOUNT_AT` out of every axis.

### 5.6 One engine error type: `SandboxError(RuntimeError)`
Follows the audit's base-exception finding (P2) *locally*: engine failures are
one catchable type. A repo-wide `SweLabError` reparenting is out of scope here
(it touches W1) — deferred to 10b.

### 5.7 `stream_to` survives into the interface
Streaming subprocess stdout to a file (not `capture_output=True`) is
load-bearing under memory pressure — an early annotation run hung 13h without
it (`docs/conventions.md` Hazards). The agent-run exec (task 06) needs it;
it's an interface property, so it lands now, and `FakeBackend` honors it.

### 5.8 Sync, contextmanager, no conditional teardown
Straight from the settled spec (assumption 5; §The manager yields the
sandbox): sync matches the current code; batch concurrency stays at the
job/matrix level; no main→teardown signal until a real case demands one.

## 6. Tests (all Docker-free, against `FakeBackend`)

- **Ordering:** `RecordingObserver` asserts the exact hook sequence for a
  clean run, incl. mounts-merge before materialize before `up`.
- **Failure matrix:** one test per row of §4's table (FakeBackend scripted to
  raise at each step; observers scripted to raise per-hook).
- **Mounts:** content/source exclusivity, executable bit, parent creation,
  duplicate-target error, missing-source error, merge across observers +
  composition-level mounts.
- **Aggregation:** artifacts/metrics merge across observers; collision →
  error; `None` contributions skipped; a stateful observer's own field
  survives the run and is readable by the caller (the §5.4 pattern).
- **Exec plumbing:** `Sandbox.exec` forwards env/timeout/stream_to;
  `stream_to` writes the scripted stdout to the file.
- **Result gating:** `.result` raises before completion; correct
  status/error afterward.

## 7. Dependencies

None new. Standard library + existing dev toolchain only. New code lands
Google-docstring'd (task-01 P4 guardrail — gates are live).

## 8. Open questions (need user confirmation)

1. ~~`Contribution.data`~~ — **resolved 2026-07-18**: no generic data slot;
   typed results live on stateful observers (§5.4), `Contribution` stays
   artifacts+metrics per the spec sketch.
2. ~~`exec(script_text)` + `SANDBOX_WORKSPACE`~~ — **resolved 2026-07-18**:
   confirmed; the full reasoning (alternatives, byte-stability, CI precedent,
   mitigations) is recorded in §5.5.
3. ~~Per-run workspace hygiene~~ — **resolved 2026-07-18**: the manager
   refuses a non-empty workspace unless `reuse=True` (no silent
   delete-known-names lists).
4. ~~Naming~~ — **resolved 2026-07-18**:
   `RunStatus.SUCCESS / SETUP_ERROR / RUN_ERROR` (symmetric noun forms,
   replacing the earlier OK/SETUP_FAILED/ERRORED draft); `testing.py` stands.

**All open questions resolved — the design is cleared for implementation.**
