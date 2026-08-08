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
