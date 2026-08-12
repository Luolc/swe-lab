# Task 28 — Codex provisioning (there is no bundle to build)

> Design only. Sibling of
> [task-24](task-24-claude-code-portable-bundle.md), which built the portable
> **Claude Code** bundle. The headline of this one is that **the equivalent
> artifact does not need to exist for Codex**, and the evidence for that is §1.

## 0. Summary

Codex's Linux binary is a **statically linked musl** executable. It already
runs, unmodified and unwrapped, on every image the Claude Code bundle exists to
support — Alpine, ancient glibc, and shell-less distroless. So the ~90 MB of
loader + glibc + NSS + static ripgrep that dominates
`packaging/claude-code-bundle/` is **dead weight here, not a template**.

What Codex needs is the *other* half of task-24: **fetch, verify, pin, place** —
plus the portability matrix to prove the static claim keeps holding.

The two agents therefore have genuinely different shapes, and this design
**declines to force them into one**:

| | Claude Code | Codex |
|---|---|---|
| Upstream Linux artifact | dynamically linked (glibc) | **static musl** |
| Our packaging job | **build** a portable bundle | **fetch** and verify |
| Needs a launcher / loader indirection | yes | **no** |
| Needs a bundled ripgrep | yes (spawned as a child) | no |
| Needs a bundled sandbox helper (`bwrap`) | n/a | **no** — see §5 |
| arm64 available upstream | no (x86_64 only) | **yes** |

## 1. Evidence — measured, 2026-08-07, `rust-v0.147.0`

```
$ ldd codex-x86_64-unknown-linux-musl
        statically linked
```

Bare binary, no bundle, `--version` on the three images that motivated the
Claude Code bundle:

| Image | Why it is the hard case | Result |
|---|---|---|
| `alpine:3.19` | musl, no glibc at all | `codex-cli 0.147.0` |
| `debian:10-slim` | glibc 2.28 — older than our 2.31 baseline | `codex-cli 0.147.0` |
| `gcr.io/distroless/static-debian12` | no shell, no libc, no package manager | `codex-cli 0.147.0` |

The release matrix corroborates it: `.github/workflows/rust-release.yml` builds
Linux for **`x86_64-unknown-linux-musl` and `aarch64-unknown-linux-musl` only**
— there is no glibc Linux target — and `codex-cli/bin/codex.js` maps
`linux/x64 → @openai/codex-linux-x64`, the musl build. Distroless passing is the
strongest single data point: it has no interpreter for a dynamic binary to find.

Sizes: `codex-x86_64-unknown-linux-musl.tar.gz` is **99 MB** (258 MB extracted),
against 90 MB for the Claude Code bundle tarball. Comparable cost, none of the
construction.

## 2. What upstream ships, and which asset we want

`rust-v0.147.0` publishes several shapes. Naming them because picking the wrong
one silently drags in a Python runtime and a packaged zsh:

| Asset | Size | Contents | Ours? |
|---|---|---|---|
| `codex-x86_64-unknown-linux-musl.tar.gz` | 99 MB | just the `codex` binary | ✅ **this one** |
| `codex-x86_64-unknown-linux-musl-bundle.tar.zst` | — | `codex` + `codex-code-mode-host` + `codex-resources/bwrap` | ❌ §5 |
| `codex-package-x86_64-unknown-linux-musl.tar.gz` | 119 MB | the above + Python runtime + `codex-zsh` | ❌ |
| `codex-app-server-*` | 82–103 MB | the app-server build | ❌ |
| `codex-symbols-*` | 226 MB | debug symbols | ❌ |

## 3. Verification — the one real gap

Claude Code publishes `{version}/manifest.json` with a per-platform sha256, and
`binary.py` checks it. **Codex has no equivalent for the asset we want:**

- `codex-package_SHA256SUMS` exists but covers only the **package** archives
  (`codex-package-*`, `codex-app-server-package-*`) — verified: it contains
  **zero** entries matching `codex-x86_64-unknown-linux-musl.tar.gz`.
- The bare binary ships a **`.sigstore`** bundle (cosign) instead.

Three options, and the recommendation is to combine two:

1. **Pin the sha256 in-repo** (lockfile discipline): record it next to the
   pinned version, verify every fetch against it, and make a mismatch a hard
   failure. Trust-on-first-use, but every *subsequent* fetch on every machine
   is pinned — which is the property that actually protects a sweep. This
   mirrors what `build.sh` already does by emitting a `.sha256` beside its
   tarball.
2. **Verify the `.sigstore` bundle when `cosign` is available**, as a
   best-effort provenance check on the pin-setting fetch. Not a hard
   requirement — requiring cosign on every machine would be a new dependency
   for little marginal safety once (1) is in place.
3. ~~Switch to `codex-package-*.tar.gz` to get SHA256SUMS coverage~~ — rejected:
   20 MB larger and drags in a Python runtime and zsh we do not run.

**Recommendation: (1), with (2) as an opt-in check when setting a new pin.**

## 4. Version policy

Same discipline as task-24 §1, minus the channel indirection (Codex has no
`stable` channel endpoint — it has GitHub releases):

- A pinned version in a `VERSION` file, bumped deliberately.
- `--pinned` rebuilds/refetches exactly what `VERSION` says; an explicit
  `--version` overrides; the default resolves the newest release and then pins
  it, printing loudly which path was taken.
- **No version floor yet.** Task-24's floor exists because below 2.1.214 the
  stream-json exit drain truncates the final `result` message, silently
  invalidating our trace. Whether `codex exec --json` has an analogous defect is
  **unknown and untested** — do not invent a floor without measuring one.

## 5. Why no `bwrap`, and what runs it headless

Upstream's `-bundle.tar.zst` carries `codex-resources/bwrap` for Codex's own
Linux sandbox. We do not need it: `codex exec` takes
`--dangerously-bypass-approvals-and-sandbox`, whose own help text reads
*"Intended solely for running in environments that are externally sandboxed"* —
which is exactly our throwaway container, the same argument that justifies
`--dangerously-skip-permissions` for Claude Code.

That also avoids a real hazard: `bwrap` needs user namespaces, which are
commonly unavailable or restricted inside a container, so shipping it would add
a dependency that fails in precisely our environment.

The headless surface a later harness task will use (from `codex exec --help`):

| Flag | Role |
|---|---|
| `--json` | JSONL event stream on stdout — the STREAM-capture analogue |
| `-m, --model` | model selection |
| `-C, --cd <DIR>` | the repo to work in |
| `--skip-git-repo-check` | needed where the workspace is not a git repo |
| `-o, --output-last-message <FILE>` | final message to a file |
| `--output-schema <FILE>` | structured final response |
| `--dangerously-bypass-approvals-and-sandbox` | unattended execution |

**Not designed here.** Mapping `--json` events onto `Conversation` and onto
`AgentOutcome` ([ADR-0011](../../decisions/ADR-0011-fair-retry.md)) is the
harness task, and it needs its own study of the event schema — including which
endings Codex can report at all, since an agent that cannot distinguish its
budget endings must report a **non-retryable** outcome rather than guess.

## 6. File organization

### 6a. `packaging/`

The two agents' packaging jobs are different in kind, so the layout names that
rather than hiding it, and shares only what is genuinely common:

```
packaging/
  README.md              # the map: which agent needs what, and WHY they differ
  lib/
    common.sh            # die(), version_ge(), the resolve/pin/--pinned block
    smoke-matrix.sh      # the portability matrix, parameterized by how to invoke
  claude-code-bundle/    # BUILD a portable bundle  (unchanged in substance)
  codex/                 # FETCH + verify a static binary; no build step
    fetch.sh  VERSION  SHA256  dist/
```

`packaging/lib/` is the reuse. `smoke-matrix.sh` is the highest-value piece:
for Codex the matrix **is** the whole verification story (§8), and task-24's
existing 200-line `smoke-test.sh` already encodes the image list and the
result-table reporting.

**`claude-code-bundle/` keeps its name.** Renaming it to `claude-code/` for
symmetry would be churn for a symmetry that does not exist — one directory
builds a bundle and the other does not.

### 6b. `src/swe_lab/`

```
src/swe_lab/harnesses/
  base.py  registry.py  observer.py
  assets.py            # NEW — shared: cache path, fetch, sha256 verify, atomic place
  claude_code/binary.py   # keeps its manifest.json scheme, uses assets.py helpers
  codex/binary.py         # NEW — GitHub-release scheme, same helpers
```

`binary.py`'s `_get`, `_sha256` and the `cache_root/bin/<namespace>/<version>/
<platform>/` layout are already agent-agnostic; only the *download scheme*
differs (Anthropic manifest vs GitHub release asset).

## 7. The seam that actually needs generalizing

`sandbox/backends/host.py:434` and `ghjob.py:262` both import
`ensure_claude_binary` **by name**. So a backend knows about one specific
harness, and adding Codex means editing every backend — two edits become four,
and a downstream backend cannot provision an agent swe-lab has never heard of.

The shape to move to: **the harness declares the asset it needs; each backend
knows only how to materialize an arbitrary asset at a path.** The Docker backend
mounts a host-cached copy, the GH-job backend downloads in place — exactly what
they do now, minus the hardcoded identity. Adding a third agent then costs zero
backend edits, which is the same open-registry argument as ADR-0003 §6.5.

This is the main piece of genuine reuse, and it is **why this task is worth
doing before a Codex harness rather than after** — the alternative is a second
hardcoded path that then has to be unpicked.

## 8. Verification plan

For Codex the matrix is not a nice-to-have; it is the entire proof, because the
"it is static, it just runs" claim is the design:

- the §1 three images, plus task-24's full list (debian 10/12, ubuntu 20.04/
  22.04, alpine 3.19, distroless) — 21 checks in the existing harness;
- `--version` on each, then a real `codex exec` check gated on a token (the
  live-agent half, which task-24's matrix still reports as SKIP);
- re-run on every version bump: a future release switching Linux to glibc, or
  adding a dynamic dependency, must fail here rather than in a sweep.

## 9. Out of scope

- **The Claude Code divergence.** `packaging/claude-code-bundle/` builds 2.1.220
  but is **not wired in** (task-24 §9 is still open), while
  `harnesses/claude_code/binary.py` fetches the *bare* 2.1.212 binary — so the
  live path is the non-portable one and there are two pinned versions.
  **Deliberately not touched here** (decided 2026-08-07). Recorded because §6b
  and §7 must not make it worse: the shared helpers are extracted *under* both,
  and the Claude Code fetch scheme keeps working exactly as today.
- **A `CodexHarness`.** Separate task; §5 lists the surface it will need.
- **arm64.** Upstream publishes `aarch64-unknown-linux-musl`, so this is
  cheaper than it was for Claude Code — parameterize the platform key, do not
  claim the target until the matrix has run on it.
- **Auth / credential handling**, and CA certificates (task-24 §10 applies
  unchanged).

## 10. Open questions

1. **Where does the pinned sha256 live** — a `SHA256` file beside `VERSION`, or
   in the Python module next to the pinned version? The latter keeps the fetch
   self-contained for a consumer who never runs `packaging/`.
2. **Does `codex exec --json` have a truncation defect** analogous to the one
   behind task-24's version floor? Needs measuring before any floor is claimed.
3. **Do we host a mirrored copy** in `Luolc/agent-assets-private` as task-24
   §0 does for the bundle? Codex's release assets are public and stable, so the
   argument is weaker — but a sweep that depends on a live GitHub release is a
   new external dependency at run time.

## 11. Decisions taken

1. **No bundle for Codex** — the static musl binary is the artifact (§1).
2. **Fetch the bare `codex-<target>.tar.gz`**, not the `-bundle` or `-package`
   variants (§2).
3. **No `bwrap`** — `--dangerously-bypass-approvals-and-sandbox` covers the
   externally-sandboxed case and avoids a user-namespace dependency (§5).
4. **Pinned sha256 in-repo is the verification**, since upstream's SHA256SUMS
   does not cover this asset; cosign/sigstore is an opt-in extra (§3).
5. **The two packaging directories stay asymmetric**; only `packaging/lib/` is
   shared (§6a).
6. **Scope is design only** for now, and the Claude Code wiring divergence is
   left alone (§9) — both decided with the user on 2026-08-07.

---

## Result — 2026-08-08 (provisioning + `CodexHarness` landed together)

Implemented and exercised end to end on real Docker containers, including a
real SWE-Bench Pro instance. **Three of the decisions above were wrong**, and
each was caught by running the thing rather than by reading more source. They
are corrected here rather than edited in place, so the reasoning that produced
the error stays visible.

### C1. Two binaries, not one — §2 and decision 2 were wrong

`codex` alone is **not** a working agent. It spawns `codex-code-mode-host` to
execute commands and apply patches, and derives that helper's path as a
*sibling* of its own binary. With the helper absent the run still starts,
authenticates, answers the prompt, and **exits 0** — having been unable to run
a single command or edit a file ("the workspace execution host is disabled").

That is the worst failure shape there is: a green rollout with an empty patch,
indistinguishable from an agent that simply failed the task. Provisioning
therefore places a **directory of two binaries**, and `ensure_codex_binaries`
is named in the plural for that reason.

What §2 got right is that we still do **not** want the `-bundle` / `-package`
archives: they add `bwrap`, a Python runtime and a packaged zsh. We fetch the
two bare per-binary assets instead.

### C2. CA certificates are required, and their absence is not obvious

Measured on `debian:stable-slim` (no `ca-certificates`): every request fails
with `invalid peer certificate: UnknownIssuer`, and the agent then burns its
retries reconnecting. Codex uses the system trust store — it does **not**
bundle roots. Task-24 §10's "documented, not bundled" rule therefore applies
to Codex unchanged, and a minimal instance image needs a CA bundle mounted and
`SSL_CERT_FILE` pointed at it.

### C3. There is no safe default model — §4 did not consider this

The set of models a Codex install may use depends on the **account** behind
it. A ChatGPT login rejects an API-tier model outright (HTTP 400, "The
'<model>' model is not supported when using Codex with a ChatGPT account") and
the whole run fails before the first turn. `CodexHarness.model` is therefore
`None` by default — no `--model` flag — and a sweep that needs reproducibility
pins one and owns its validity there.

### C4. Credentials are a *file*, so they arrive by mount

A ChatGPT login lives in `auth.json` under `CODEX_HOME`, which `pass_env`
cannot carry. `CodexAuthObserver` stages it as a mount — not read-only, since
Codex refreshes the token and writes it back, and the container's copy is
discarded with the container.

### C5. An item-level error is not a run failure

A live 0.147.0 run emits an `item.completed` of type `error` (a degraded
optional feature) on a turn that then **completes perfectly**. Classifying the
outcome from items would report that healthy run as failed and, since
`EXECUTION_ERROR` is retryable under [ADR-0011](../../decisions/ADR-0011-fair-retry.md),
spend budget re-running it. `event_stream_outcome` reads turn-level events
only; the notice still reaches the conversation so it is not silently lost.
A named test guards this.

### Confirmed as designed

- **Static musl** (§1) held all the way through the production path — no
  bundle, no launcher, no bundled libraries.
- **No `bwrap`** (§5, decision 3): `--dangerously-bypass-approvals-and-sandbox`
  is what an externally-sandboxed container wants, and it avoids the
  user-namespace dependency.
- **Pinned in-repo sha256** (§3, decision 4) is the verification, now for both
  archives.

### §7 (the provisioning seam) is still open, and now has evidence

Not done. `HostCodexBinaryObserver` exists but is **opt-in** — a caller composes
it through `extra_observers` — because the backend cannot see which agent a run
uses, and adding it to the default set would make every Claude Code run also
fetch ~300 MB of Codex. So the repo now has exactly the asymmetry §7 predicts:
one agent provisioned by default, another by hand, both enumerated inside the
backend. That is the argument for doing §7 next.

### Verified by running it

| Case | Result |
|---|---|
| Trivial prompt, `python:3.12-slim` | `FINISHED`, 1-message trace, empty patch |
| File edit + verification command | `FINISHED`, 12-message trace with matched `tool_use`/`tool_result` pairs, failed commands flagged `is_error` |
| **Real SWE-Bench Pro instance** (flipt, real image + `base_commit`, history purge and result verifier on) | `FINISHED`, **non-empty patch extracted** against the real base commit |
| Model pinned to an account-invalid value | `turn.failed` → `EXECUTION_ERROR`, correctly classified as retryable |

A run whose prompt named a file absent from the checkout produced an empty
patch and an honest refusal — the harness reporting the truth, not a defect.

### Still not verified

- **GitHub-job backend**: no provisioning path written (out of scope for now).
- **The portability matrix** (§8) has not been run against the pinned build;
  §1's three-image check was done on the bare binary before this landed.
- **arm64**, still unclaimed.
- No shipped workflow definition registers Codex yet — it is selectable by
  name (`--rollout.harness=codex`), but no built-in definition uses it.

---

## §7 Result — 2026-08-12 (the seam landed)

Built as described, and the shape held: **a harness declares the assets it
needs; a backend knows only how to materialize an arbitrary one.**

- `AgentAsset` — an absolute in-sandbox path plus a *materializer*. The
  materializer contract (`dest=None` caches and returns the host path; a path
  installs there) is the one **every** `ensure_*` function already satisfied,
  which is why the seam needed no new fetching code.
- `Sandbox.asset_observer(assets)` is where a backend answers *how*: the
  Docker backend inherits the mount answer (a container cannot fetch its own
  bytes), the GH-job backend overrides with the install answer (its filesystem
  *is* the sandbox, so nothing should travel).
- `Harness.assets()` / `Task.assets()` / `CodingAgentTask.assets()` — the
  declaration, and the one line that joins the halves.

**Deleted**: `HostClaudeCodeBinaryObserver`, `HostCodexBinaryObserver`,
`HostGrokBinaryObserver`, `GitHubJobClaudeCodeBinaryObserver`. No backend
imports a harness by name any more.

Three things this fixed that were not the stated goal:

1. **codex and grok became usable on the plain path.** They were opt-in
   because the backend could not see which agent a run used, so
   `--rollout.harness=codex` selected an agent whose binary never arrived —
   every e2e in tasks 28 and 29 had to register a throwaway backend to get
   around it. Verified: all three agents now provision through
   `Task.execute` with **no `extra_observers` at all**, each reporting its own
   version from inside the container.
2. **A grading container stops carrying an agent.** The Docker backend used to
   mount the Claude Code binary unconditionally, so every `unit_test` and
   `git_integrity` container was handed ~100 MB it never execed.
   `UnitTestTask.assets()` is empty, so now it gets nothing.
3. **The missing combinations stopped being missing.** It was 2 backends × 3
   agents with 4 of 6 written by hand; there is now nothing to write.

The negative property — neither side enumerates the other — is pinned by a
test that places an asset named for an agent swe-lab has never heard of.

### §7 correction — 2026-08-12: resolution is not two-valued

The first cut of the seam asserted, in the module docstring and in the type,
that **"there are exactly two ways to materialize an asset"** — transfer a
host copy, or fetch to the final path — and made the fetch closure a
**required** field of `AgentAsset`.

That was an assumption, not a finding, and it was wrong in a way that had
already been called out: a downstream **remote sandbox maintains its own
private artifact store**. It neither downloads nor is handed a host copy. It
resolves an artifact **by identity**, writes the resulting store path into the
sandbox's own construction parameters, and does so **before the sandbox
exists** — that declaration is part of how the sandbox gets built, which is
the whole reason provisioning was moved to the sandbox layer in the first
place.

Two concrete breaks followed from the assumption:

1. **An asset carried no identity.** It was a path plus a closure, so there
   was nothing for a store to look anything up *by*.
2. **`fetch` was mandatory**, so a harness had to hand a store-resolving
   backend a downloader it must never call.

Corrected, and the correction went through two rounds because the first one
over-corrected: an asset now declares **a release and a destination path, and
nothing else.**

```python
AgentAsset(path="/opt/codex/codex", version="0.147.0", fetch=<optional>)
```

- `fetch` is **optional** — a store-resolving backend never calls it, and
  requiring it forced every harness to hand such a backend a downloader it
  must not use.
- There is **no `platform` field**. The first correction added one, which was
  the same mistake again one level down: a sandbox knows what it runs on, a
  harness does not, and choosing the build — or bundling it, or how it travels
  — is the sandbox's call.
- The pinned version is a **harness field** with a default, not a constant.
  The default is the release the harness was verified against, because a sweep
  whose agent build floats is not reproducible; overriding it is a run-level
  decision (`--rollout.harness.version=…`), and an unpinned version fails
  loudly at the checksum rather than downloading unverified bytes.

**Assets reach the sandbox at two moments, and both are load-bearing.**

- **Configuration time** — the runner fills `SandboxConfig.assets` from the
  task *before* calling the factory. A sandbox backed by its own artifact
  store has to know what it will carry in order to be built at all: it
  resolves each release to a store path and names it in its own construction
  parameters, and there is no later moment at which it could.
- **Run time** — `Sandbox.asset_observer` still runs, and resolving early does
  **not** make it redundant. An artifact that arrives as an archive (the
  Claude Code bundle is a tarball) still has to be unpacked, moved into place
  and made executable, and `after_create` is the only place that can happen —
  whoever brought the bytes in. Returning `None` is correct only when there is
  genuinely nothing left to do.

Tests cover both: one resolves all three shipped agents with no bytes moved
and no sandbox in existence; another drives a store-backed sandbox that
resolves at configuration time *and* returns an unpacking observer, asserting
the run-time half ran while nothing was fetched.

