# Task 14 — Merged lifecycle-bearing `Sandbox` + up-first lifecycle + transfer seam

> **Status: PLANNED — pre-implementation.** Source of truth:
> [ADR-0003](../../decisions/ADR-0003-remote-sandbox-lifecycle.md) (the decision)
> and the [spec](../spec.md). Grounded in the current engine at the
> post-cutover `main`: `sandbox/{manager,backend,resources,mounts,result}.py`,
> `sandbox/backends/{host,ghjob}.py`, `sandbox/testing.py`, the observers
> (`sandbox/observers/diff_extract.py`, `conversation/observer.py`,
> `evaluation/methods/unit_test/run.py`), and the compositions (`solve.py`,
> `evaluation/methods/unit_test/run.py`). **P0.** This is Phase 1 of ADR-0003
> (host-only, zero functional change); Task 15 (seam proof + guide) and Task 12
> (persistence, rebased) follow.

---

## 1. Purpose & scope

Land the ADR-0003 refactor that lets a company author its **own** `Sandbox`
subclass (import-only): merge `Sandbox` + `SandboxBackend` into **one
lifecycle-bearing `Sandbox`**, make the lifecycle **up-first**, replace the
host-`Path` workspace with the sandbox's own ops, unify `Mount`/`Assets`, and
turn `Resource` into extensible data whose transfer the sandbox decides. **A-host
and A-ghjob behave identically**; the full suite + the flipt `eval`/`rollout`
E2E stay green.

### In scope
- One `Sandbox` ABC (`up`/`mount`/`run_script`/`read`/`write`/`down`); `DockerHostSandbox`
  + `GitHubJobSandbox` as subclasses; `FakeSandbox` for tests.
- Up-first `SandboxManager` lifecycle; `_prepare_workspace` deleted.
- `Mount` gains `read_only`; `Assets`/`with_assets` removed ("asset" = a
  read-only mount).
- `Resource` → data (no `materialize_to`/`local_path`); the sandbox switches on
  it, with a reusable base handler subclasses extend via `super()`.
- Observers + grader ported off `sb.workspace: Path` onto `sb` ops; the
  **artifact-out seam** (`sb.fetch` + a manager collect step, §6.4) — the output
  half of the transfer seam, working for host and remote.
- Backend selection is **open**: the composition takes a `Sandbox` by value
  (programmatic), and the CLI resolves `--backend` through a **name registry**
  (not a closed enum) — see §6.5.

### Out of scope (later ADR-0003 phases)
- A shipped remote backend (Task 15 proves the seam with a `FakeRemoteSandbox`).
- The `Store` (filesystem / R2), tiers, and `PersistObserver` that *consume* the
  collected artifacts, plus the ~20% direct-persist branch (Task 12) — Task 14
  lands only the `fetch` + collect interface they build on.
- New `Resource` kinds (`Url`/`ObjectStore`) — the data shape allows them; no
  built-in impl here.

## 2. Module layout

```
sandbox/
  sandbox.py       NEW home: the `Sandbox` ABC + `SandboxFs` view + `ExecResult`
                   (absorbs today's backend.py; backend.py deleted)
  manager.py       up-first lifecycle; drop `_prepare_workspace`; drive one Sandbox
  mounts.py        `Mount(resource, executable, read_only)`; drop `Assets`
  resources.py     `Resource` = data (Inline.content / LocalFile.path); no behavior
  backends/
    host.py        `DockerHostSandbox(Sandbox)`
    ghjob.py       `GitHubJobSandbox(Sandbox)`
    __init__.py    a sandbox **registry** — `register_sandbox`/`build_sandbox(name,…)`;
                   built-ins register `host`/`ghjob` (an open registry, NOT a closed enum)
  testing.py       `FakeSandbox(Sandbox)`
```

## 3. Key types & signatures

```python
# ─── sandbox/sandbox.py ─────────────────────────────────────────────────────
class SandboxFs(ABC):              # the narrow view observers/graders receive
  @property
  def spec(self) -> SandboxSpec: ...
  def read(self, name: str) -> bytes: ...
  def exists(self, name: str) -> bool: ...
  def write(self, name: str, data: bytes, *, executable=False) -> None: ...
  def run_script(self, name, *, timeout, env=None, stream_to=None) -> ExecResult: ...  # <shell> <ws>/<name>
  def run_command(self, command: str, *, timeout, env=None, stream_to=None) -> ExecResult: ...  # <shell> -c <command>
  # NOTE: no `mount` / `fetch` / `up` / `down` here — those are manager ops (below).

class Sandbox(SandboxFs, ABC):     # config + lifecycle + ops, in ONE object
  spec: SandboxSpec
  def up(self) -> None: ...                 # provision (subsumes _prepare_workspace)
  def mount(self, mounts: Mounts) -> None:  # stage declared inputs, AFTER up — MANAGER-only
    for name, m in mounts.items():
      self._mount_one(name, m)              # the sandbox handles it; subclass extends
  def fetch(self, name: str, dest: Path) -> None: ...  # dump one artifact to a host path (§6.4) — MANAGER-only
  def down(self) -> None: ...               # best-effort; never raises
  # read/write/run_script/run_command/exists inherited from SandboxFs

  def _mount_one(self, name: str, m: Mount) -> None:
    """Handle the built-in Resource kinds; a subclass overrides for its own
    kinds and calls super() for the rest (import-only extension)."""
    match m.resource:
      case Inline(content):   self._put_bytes(name, content, m)
      case LocalFile(path):   self._put_file(name, path, m)
      case _: raise SandboxError(f"{type(self).__name__} cannot mount {m.resource!r}")
  # `_put_bytes` / `_put_file` are the sandbox's transfer primitives (§4).
```

**Who does what.** `mount` is **not** on `SandboxFs` and is **not** an observer
capability — it is the declarative staging of the composition's `Mounts`
(prompt, binary, run_script, …), called **once by the manager** after `up`, and
**handled by the sandbox itself** (`_mount_one` dispatch). Observers receive the
narrow `SandboxFs` and only `read` / `write` / `run_script` / `run_command` — a
mid-run observer writes an ad-hoc file (`write`) or runs a command, it does not
re-stage declared mounts. `run_script` executes a **staged file by name** (persisted for
audit, per the workspace-layout principle); `run_command` runs an **inline
command string** for short/diagnostic commands (both backends: a one-line argv
change — `<shell> -c "<cmd>"` vs `<shell> <ws>/<name>`).

**Configurable shell.** Both `run_script` and `run_command` invoke a
**per-sandbox shell**, a construction config **defaulting to `bash`** (e.g.
`DockerHostSandbox(spec, …, shell="sh")`) — some minimal images ship only `sh`,
so hard-coding `/bin/bash` would break them. The shell string is used directly:
`[shell, ws/name]` / `[shell, "-c", command]`.

```python

# ─── sandbox/mounts.py ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Mount:
  resource: Resource
  executable: bool = False
  read_only: bool = False          # NEW — "asset" = a read_only mount
type Mounts = dict[str, Mount]      # target path (workspace-relative or absolute) → mount
# `Assets`, `with_assets` deleted.

# ─── sandbox/resources.py — data only ───────────────────────────────────────
class Resource(ABC): ...                    # marker base; no behavior methods
@dataclass(frozen=True, slots=True)
class Inline(Resource):    content: bytes
@dataclass(frozen=True, slots=True)
class LocalFile(Resource): path: Path
# `materialize_to` / `local_path` removed — the sandbox reads `.content`/`.path`.
```

## 4. The transfer matrix (`Resource` × sandbox) — the crux ADR-0003 deferred here

`mount` dispatches on the `Resource` kind and honors `read_only` / `executable` /
absolute-vs-workspace path. For the two shipped sandboxes:

| `Mount` | `DockerHostSandbox` (self-owned host dir, bind-mounted at `up`) | `GitHubJobSandbox` (local dir) |
|---|---|---|
| `Inline`, workspace-relative | `_put_bytes`: write bytes into the dir; `chmod +x` if `executable` (container sees it via the bind mount) | write bytes into the dir; `chmod +x` if `executable` |
| `LocalFile`, workspace-relative | `_put_file`: copy into the dir (+ mode) | copy into the dir; mirror source mode (`copymode` — the exec-bit fix) |
| `LocalFile`, **`read_only` absolute path** (e.g. the binary at `/opt/claude-code/claude`) | bind-mount `-v host:abs:ro` at `docker create` (today's asset path) | copy to the absolute path, `copymode`, then `chmod a-w` (today's ghjob asset path) |
| `read(name)` | read the dir host-side | read the local dir |

So the current **asset** behavior (bind-mount `:ro` for A-host; copy read-only
for A-ghjob) is now just the `read_only` branch of `mount`; the pinned binary is
a `Mount(LocalFile(binary), executable=True, read_only=True)` at `BINARY_AT`. A
company sandbox overrides `_mount_one` to add its own `Resource` kinds and calls
`super()._mount_one` for `Inline`/`LocalFile` — **import-only extension**.

## 5. Up-first lifecycle (manager)

`SandboxManager.sandbox()` (today `manager.py:163-199`) reorders to:

```python
sb = self._sandbox(spec, config)          # construct the chosen Sandbox subclass (not live)
try:
  merged = merge_mounts(dict(self.mounts), *(o.mounts() for o in observers))
  for o in observers: o.before_create(sb) # sb not live yet; run_script/read/mount fail
  sb.up()                                  # provision (was backend.up; subsumes _prepare_workspace)
  sb.mount(merged)                         # AFTER up
  for o in observers: o.after_create(sb)
  yield sb                                 # body
finally:
  for o in observers: contributions += [o.before_destroy(sb)]  # produce + register artifact NAMES
  self._collect(sb, contributions, staging) # sb.fetch(name, staging/name) for each — WHILE LIVE
  sb.down()                                  # then tear down
```

- `Sandbox.__init__` sets state; `up()` marks it live (a `_live` flag replaces
  the `handle == ""` guard at `manager.py:83-84`). `run_script`/`read`/`mount` raise
  `SandboxError` before `up()`.
- `_prepare_workspace` (`manager.py:226-237`) moves **into each sandbox's `up`**
  (host backends `mkdir` + the empty-check; ghjob provisions its dir).
- **Artifacts are collected before `down`** (§6.4): `before_destroy` registers
  names, the manager `fetch`es them to a host staging dir *while the sandbox is
  still live*, then `down`. So `RunResult.artifacts` are host paths for host and
  remote alike. (`on_error` teardown paths collect too, where the sandbox is live.)
- The frozen `Sandbox` + `replace(handle=…)` gymnastics (`manager.py:158-174`)
  are gone; the sandbox is a normal mutable object.

## 6. Observer / composition / artifact migration

### 6.1 Observers use `sb` ops, not `sb.workspace`
- `diff_extract.py`: `(sb.workspace / "extract.sh").write_text(script)` →
  `sb.write("extract.sh", script.encode(), executable=True)`; `sb.run_script("extract.sh")`
  unchanged; `_read_patch(sb.workspace / "patch.raw.diff")` →
  `_read_patch(sb.read("patch.raw.diff"))`; write `patch.diff` → `sb.write(...)`.
- `conversation/observer.py`: `to_conversation(sb.workspace)` →
  `to_conversation(sb)` (the converter reads via `sb.read`); write
  `conversation.json` → `sb.write`; `native_outputs` existence → `sb.exists`.
- The `Harness.to_conversation(workspace: Path)` and `native_outputs()` seam
  changes to take the reader; `event_stream_to_conversation` reads via `sb.read`.
- Both observers now register artifacts by **name** (canonical name →
  in-sandbox filename), not by host `Path` — the manager collect step (§6.4)
  fetches them out.

### 6.2 Grader takes the reader, not a `Path`
`Grader.grade(workspace: Path)` → `grade(reader: SandboxFs)`;
`SweBenchProGrader` reads `output.json` + `required_tests.json` via
`reader.read(...)`. The `output_state` logic is unchanged.

### 6.3 Observers get the narrow `SandboxFs` view
Hooks are typed `(sb: SandboxFs)` so an observer **cannot** call `up`/`down`
(capability narrowing, ADR-0003 §2). The manager holds the full `Sandbox`.

### 6.4 Artifacts are the OUTPUT half of the transfer seam — `fetch` / dump-to-host
Artifacts are files a run **produces inside the sandbox** (patch.diff,
conversation.json, output.json, …) that we ultimately **persist**. Getting them
**out** is symmetric to getting inputs **in** (`mount`), and is likewise
**receiver-decides**: ~80% the sandbox **dumps to the host** and persistence
happens host-side; ~20% the sandbox persists directly (same-cloud Store). We
design the interface now — not punt it.

- **`Sandbox.fetch(name, dest: Path) -> None`** — dump one produced artifact to a
  host path. A host sandbox **copies (or hard-links) from its own dir** (its
  files are already host-visible — near-zero cost); a remote **downloads** to
  `dest`. Symmetric to `_mount_one`'s per-`Resource` transfer.
- **A collect step in teardown.** Observers register artifacts by **name** (not
  host `Path` — meaningless for a remote sandbox). The manager, **after
  `before_destroy` and while the sandbox is still live** (before `down`),
  `fetch`es each registered artifact into a host staging dir, so
  `RunResult.artifacts: dict[str, Path]` holds **host paths for host *and*
  remote** runs. Symmetric to `mount` (manager stages inputs after `up`; manager
  collects outputs before `down`).
- **Types:** `Contribution.artifacts` becomes canonical-name → in-sandbox
  filename (`dict[str, str]`); `RunResult.artifacts` stays `dict[str, Path]`
  (the host staging copies).
- The **`Store`** (filesystem / R2, tiers) that consumes the host copies — and
  the ~20% *direct-persist* branch — are **Task 12**; Task 14 lands `fetch` +
  the collect step so both host and remote runs already yield host-side
  artifacts. `sb.host_path` is dropped (`fetch` subsumes it).

### 6.5 Compositions & backend selection (open, not an enum)
- `solve.py`: drop `backend.with_assets(harness.assets())`; the harness's binary
  becomes a `read_only` executable `Mount` at `BINARY_AT` in the merged mounts.
  `run_rollout`/`RolloutOutcome` otherwise unchanged (`event_stream_complete`
  reads via `sb`). **The composition/manager takes a `Sandbox` (or a factory) by
  value** — a company passes `AcmeSandbox(spec, …)` programmatically, fully open,
  import-only, no enum.
- **Backend selection is a name registry, not a closed enum.** Today's
  `BackendKind` enum + `build_backend` re-close extensibility (a company can't add
  an enum member). Replace with `register_sandbox(name, factory)` +
  `build_sandbox(name, spec, **cfg)`; built-ins register `host` / `ghjob` at
  import. `--backend` becomes a **`str` validated against the registered names**
  (not a Typer enum), so a company's `register_sandbox("acme", AcmeSandbox)` (in
  their wrapper / an entry point) makes `--backend acme` work with **no swe-lab
  edit**. `--capture proxy` wiring in `solve.py` unchanged.

## 7. Testing
- `FakeSandbox(Sandbox)` (in-memory dir) replaces `FakeBackend`; the 8 engine
  tests that drive `FakeBackend` migrate to it. Add a tiny out-of-tree-style
  subclass with its own `Resource` kind + `_mount_one` override to prove
  import-only extension (foreshadows Task 15's seam proof).
- `test_host_backend.py` / `test_ghjob_backend.py`: assert the same argv /
  read-only / exec-bit behaviour through `up`/`mount` instead of
  `up`/`materialize`/`with_assets`.
- Full suite green; the flipt `eval --gold` and `rollout --grade` E2E produce
  identical verdicts (host behaviour unchanged).

## 8. Dependencies & sequencing
Depends on nothing new; it is a refactor of the shipped engine. **Precedes
Task 12** (persistence rebases onto §6.4). Task 15 (seam proof + author guide)
follows. No runtime deps added.

## 9. Open questions

**Settled in review:** the observer view is named **`SandboxFs`**
(`read`/`write`/`exists`/`run_script`/`run_command`) — **not** `mount` / `fetch` /
`up` / `down`, which are the manager's staging/collect/lifecycle ops on the full
`Sandbox`. `run_command` uses a per-sandbox shell (default `bash`, configurable
for `sh`-only images). Artifacts are collected **out** via `sb.fetch(name, dest)`
+ a manager collect step (§6.4) — the output half of the transfer seam — so the
earlier `host_path` escape hatch is dropped.

**Still open:**
1. **`fetch` vs `read` for artifacts** — `read(name) -> bytes` (in-memory, small)
   vs `fetch(name, dest) -> None` (streamed to a host file, large). Lean: keep
   both — the collect step uses `fetch` (files, no buffering); observers use
   `read` for content they process inline. Confirm the collect step lives in the
   manager (symmetric to `mount`) vs a shared `CollectObserver`.
2. **`SandboxFs.write` breadth** — `write` stays on the view (diff-extract
   legitimately stages `extract.sh`); only `up`/`down`/`mount` are withheld.
   Flag if a stricter read-only observer view is wanted.
