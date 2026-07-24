# Task 03 — A-host backend: `docker create/start/exec/rm`

> **Status: DONE (PR #27), amended 2026-07-21.**
> Class shipped as **`DockerHostBackend`**. §8 open questions resolved as
> recommended (bash keep-alive; `@pytest.mark.docker` auto-skip; hardening
> deferred). Two amendments settled with the user:
> 1. ✅ **exec → `run_script`: runs a persisted workspace file, not stdin**
>    (reverses the §5.6 stdin delta). `Sandbox.run(name)` runs a workspace
>    script by its `$SANDBOX_WORKSPACE` path (`docker exec … bash
>    $mount_at/<name>`); scripts are materialized as mounts or written there by
>    the generating observer, so the exact script survives for audit and
>    A-ghjob needs no stdin plumbing. See
>    [`workspace-layout.md`](../workspace-layout.md).
> 2. ⬜ **assets** (pending, lands with task 06 which needs it) — a
>    construction-time `assets` field (`dict[container_path, Resource]`) placing
>    **read-only** resources at fixed container paths (`docker create
>    -v host:container:ro` for a `LocalFile`; A-ghjob `cp` kept read-only), so the
>    pinned agent binary lands at `/opt/claude-code/claude` (invoked by absolute
>    path) *outside* the read/write workspace. An asset is read-only
>    infrastructure the run must never mutate — that, not its size, is why it is an
>    asset (spec §Assets vs. mounts).
> 3. ⬜ **`Resource` + a per-backend `materialize` seam** (pending, lands with
>    task 06) — introduce a **`Resource`** abstraction (the shared content-source:
>    `Inline` / `LocalFile` now; `Url` / object-store later) that **both mounts and
>    assets wrap** (a `Mount` is `Resource` + workspace target + `executable`; an
>    asset is a `Resource` at a fixed read-only path — source kinds defined once).
>    `materialize(mounts, workspace)` moves **onto the backend** (from the free
>    `sandbox/mounts.py` function the manager calls today), with a **default** for
>    the simple Resource kinds (`Inline` → write, `LocalFile` → copy); a backend
>    may **override per kind** — a remote/object-store backend fetches a `Url`/ref
>    natively rather than routing bytes through the host. The current brute
>    `shutil.copyfile` stays only as the default `LocalFile` path (spec
>    §Materialization is a per-backend seam).

---

## 1. Purpose & scope

Implement the first real `SandboxBackend`: a **host-orchestrated persistent
container** (`docker create` → `start` → N× `exec` → `rm`), replacing the
one-shot `docker run` model for engine compositions. The old `docker run` is
the degenerate "everything in one exec" case (spec assumption 3) — the
persistent form is what lets setup, main, and on-error probes run as separate
`exec`s against one live container.

### In scope

- `sandbox/backends/host.py`: `HostBackend` implementing
  `up`/`exec`/`down`, plus image pull.
- Timeout, network toggle, env pass-through (explicit and by-reference),
  `stream_to` streaming, teardown guarantees.
- An integration smoke test against a small public image (skippable when
  Docker is absent) + failure-injection teardown tests.

### Out of scope

- A-ghjob backend (task 09).
- Resource/capability hardening (`--pids-limit`, `--memory`, `--cap-drop`) —
  audit P1-4; worth doing, but it changes runtime behavior for existing flows
  and deserves its own decision at cutover (noted in §8 Q3).
- Deleting `core/docker/provider.py` — old flows keep using it until 10b.

## 2. Module layout

```
sandbox/
  backends/
    __init__.py
    host.py       HostBackend (this task)
    # ghjob.py    (task 09)
```

Tests: `tests/test_host_backend.py` — unit tests around argv construction
(subprocess mocked) + integration tests marked `@pytest.mark.docker`
(deselected by default locally via `-m "not docker"` in CI-less runs; see §8
Q2).

## 3. Key types & signatures

```python
# ─── backends/host.py ───────────────────────────────────────────────────────
DEFAULT_PLATFORM = "linux/amd64"      # as today: provider.py:11
_PULL_TIMEOUT_S = 3600.0              # as today: provider.py:13

@dataclass(frozen=True)
class HostBackend:
  """Persistent-container backend over the docker CLI.

  One backend instance may serve many sandboxes; per-sandbox state lives in
  the opaque handle (the container id), never on the backend.
  """

  platform: str = DEFAULT_PLATFORM
  network: bool = True                # --network none when False
  pull: bool = True                   # pull image_ref before create
  mount_at: str = "/workspace"        # in-container workspace path
  env: Mapping[str, str] = field(default_factory=dict)      # -e KEY=VALUE
  pass_env: Sequence[str] = ()        # -e KEY (inherit by reference — secrets)

  def up(self, spec: SandboxSpec, workspace: Path) -> str: ...
  def exec(self, handle, script, *, timeout, env=None, stream_to=None)
      -> ExecResult: ...
  def down(self, handle: str) -> None: ...
```

Backend-level `env`/`pass_env`/`network` are **construction-time** (they are
composition properties — rollout needs network+token, grading needs neither),
while per-`exec` `env` adds run-step variables. This mirrors today's split
where `run_script` takes them per-call (`provider.py:73-77`) but every caller
passes fixed values (`grading.py:188-195`, `runner.py:127-135`).

## 4. Lifecycle — the docker command sequence

1. **pull** (when `pull=True`): `docker pull --platform <p> <image_ref>` —
   verbatim port of `provider.py:55-63` incl. the stderr-tail error message.
2. **create**:
   `docker create --platform <p> [--network none] -v <workspace>:<mount_at>
   [-e K=V…] [-e K…] --label swe-lab=1 --label swe-lab-instance=<id>
   --entrypoint /bin/bash <image_ref> -c 'sleep infinity'` → container id.
   - `bash -c 'sleep infinity'` as the keep-alive: every jefzda instance image
     has bash on PATH (the harness requires it — `constants.py:BASH`,
     `run_script.sh` is bash), whereas `tail`/`sleep` as *entrypoint binaries*
     are not guaranteed on minimal images. `--entrypoint` override for the
     same reason as today (`provider.py:80-82`).
   - Labels make orphans findable: `docker ps -f label=swe-lab` (§5.3).
3. **start**: `docker start <cid>`.
4. **exec** (per call): write `script` to
   `<workspace>/.sandbox/exec-<n>.sh`, then
   `docker exec -e SANDBOX_WORKSPACE=<mount_at> [-e K=V…] <cid>
   /bin/bash <mount_at>/.sandbox/exec-<n>.sh`.
   - stdout → `stream_to` file when given, else captured (task 02 §5.7).
   - `subprocess.TimeoutExpired` → `ExecResult(124, …, timed_out=True)`,
     matching today's timeout contract (`provider.py:139-142`); the timed-out
     *exec* process is killed but the container stays up — teardown is
     `down`'s job, which the manager always calls (task 02 §4 row 9).
5. **down**: `docker rm -f <cid>` — best-effort, never raises (task 02
   `SandboxBackend.down` contract); failure is logged to stderr. `-f` also
   kills the keep-alive, so no separate `docker stop`.

### Edge-case ledger

| Condition | Behavior |
|---|---|
| docker CLI missing | `SandboxError("docker CLI not found on PATH")` from `up` (as `provider.py:143-144`) |
| pull fails | `SandboxError` with stderr tail (as `provider.py:60-63`) |
| create/start fails | `SandboxError`; nothing to rm (no cid yet) — matrix row "up raises" |
| exec on a dead container | nonzero `ExecResult` (docker exec's own failure), not a raise — the caller's script contract decides what's fatal |
| exec timeout | `ExecResult(timed_out=True)`; container still up; later execs remain possible (on_error probes) |
| down on already-removed cid | `rm -f` exits nonzero → swallowed + logged |
| `.sandbox/` scratch dir | created by first exec; never listed as an artifact |

## 5. Design decisions

### 5.1 Persistent container, not one-shot `docker run`
The engine's whole point: setup (`after_create`), main, and on-error probes
are separate execs against one live container (spec §The core model). The
one-shot `run_script` (`provider.py:65-120`) cannot express "exec into the
still-live sandbox" (spec §Resolved #4) at all. Runtime cost is nil — same
kernel, one extra `create`/`start` round-trip.

### 5.2 Reuse the CLI-subprocess idiom, not docker-py
Same trade as today (`provider.py:130-145`): the docker CLI is the only
runtime dependency, argv-list subprocess calls (no `shell=True`), text capture
with timeout. Adding docker-py would be a new runtime dep ("ask first"
boundary) for zero capability we need.

### 5.3 Owned labels for orphan hygiene
A persistent container that escapes `down` (SIGKILL of the python process)
would linger forever — the one-shot `--rm` (`provider.py:104`) never had this
problem. Mitigations: (a) `down` in the manager's `finally` (task 02 matrix),
(b) every container carries `--label swe-lab=1` so a sweep is one command,
documented in conventions Hazards at cutover, (c) the integration suite
asserts zero `label=swe-lab` containers after each test.

### 5.4 Secrets stay by-reference
`-e NAME` (no value) at create-time inherits from the host process env exactly
like today's `pass_env` (`provider.py:95-99` — value never in argv/ps/logs).
The rollout composition keeps using it for `CLAUDE_CODE_OAUTH_TOKEN`
(`runner.py:134` / `rollout/constants.py:OAUTH_TOKEN_ENV`). The audit-P0-1
question (token in the container at all) is explicitly *not* re-decided here —
it needs its own ADR (plan §Out of scope).

### 5.5 `SANDBOX_WORKSPACE` set by the backend
Companion of task 02 §5.5: A-host sets it to `mount_at` (`/workspace`);
A-ghjob will set it to the local workspace path. Axis-generated scripts
reference workspace files only through it, which is what makes one script text
run on both backends (spec Success #4).

### 5.6 Exec feeds the script on stdin, not a scratch file *(implementation delta)*
The plan (§4 step 4) wrote each script to `<workspace>/.sandbox/exec-<n>.sh`
and ran it by path. But the `SandboxBackend.exec` signature only carries the
handle, **not** the workspace path — and the backend is a frozen, stateless
dataclass, so it cannot map handle→workspace to locate that file. Rather than
widen the interface or encode the path into the handle, exec runs
`docker exec -i <cid> /bin/bash -s` and feeds the script text on **stdin**.
This is stateless, needs no scratch dir (the `.sandbox/` ledger entry is
dropped), and keeps `SANDBOX_WORKSPACE` (a construction-time backend field) as
the only path handshake. Trade-off: the script is not left on disk for
post-hoc inspection — if a composition wants an entryscript persisted for
audit, it can add it as a `Mount`, which is orthogonal.

## 6. Tests

**Unit (subprocess mocked, no Docker):** argv construction for
pull/create/start/exec/down (network off, env forms, labels, entrypoint);
timeout → `ExecResult(124, timed_out=True)`; missing-CLI → `SandboxError`;
down never raises.

**Integration (`@pytest.mark.docker`, small public bash-capable image, e.g.
`debian:stable-slim`):**
- up → exec writes a file → visible in the host workspace; exec reads a
  materialized mount.
- nonzero-exit script → `ExecResult.exit_code` faithful; two sequential execs
  share container state (touch in #1, stat in #2 — the persistence property).
- `stream_to` streams a multi-line stdout to the file.
- failure injection: body raises inside `manager.sandbox()` → container is
  gone afterwards (`docker ps -a -f label=swe-lab` empty) — the teardown
  guarantee end-to-end with the real backend.
- suite teardown asserts no orphan `label=swe-lab` containers.

## 7. Dependencies

None new (docker CLI, stdlib). CI: integration tests run on ubuntu-latest
(native amd64, as the CI workflows do); locally they run under emulation or
are deselected.

## 8. Open questions (need user confirmation)

1. **Keep-alive form** — `--entrypoint /bin/bash … -c 'sleep infinity'`:
   fine, or prefer `tail -f /dev/null`? (bash is the only binary the harness
   already guarantees; I recommend bash.)
2. **Test marker** — `@pytest.mark.docker` with integration tests *included*
   in CI's `uv run pytest` but skipped locally when the docker CLI is absent
   (auto-skip, no flag). OK?
3. **Sandbox hardening flags** (audit P1-4: `--pids-limit`, `--memory`,
   `--cap-drop`, grading default `network=False`) — adopt here while the
   backend is fresh, or defer to a dedicated hardening task after cutover? My
   recommendation: **defer** — parity first (CP1 compares old vs new on
   identical runtime conditions), harden as its own small task right after
   10b, so a behavior change is never entangled with the port.
