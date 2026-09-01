# Task 24 — a portable Claude Code bundle (glibc-old-baseline)

Design record. Status lives in [`README.md`](README.md), not here.

Produce a reproducible, self-contained x86_64 Linux artifact that runs the
pinned Claude Code agent on **any** target image — modern glibc, ancient glibc,
musl/Alpine, and distroless — with a repeatable, auditable build.

Everything asserted below about the release channel, the manifest and the
signature chain was **fetched or executed on 2026-08-05**, not assumed; the
commands are inline so a reader can re-run them.

---

## 0. Distribution — internal only

> ### ⚠️ This artifact is internal-use only and must never be published
>
> Not a public GitHub Release, not a public Hugging Face repo, not a
> world-readable bucket, not a public container image layer. Flipping a hosting
> repo to public — or pushing a built image to a public registry — is the one
> change that breaks this rule, so every hosting change gets checked against
> it.

### Hosting — live

**`Luolc/agent-assets-private`** (private), one release per asset per version,
tagged `<product>-v<version>` so the repo can hold Codex, Grok Build and a
standalone `rg` alongside this. Hyphen, not slash: slashes are valid git refs
but need URL-encoding in GitHub API paths and quoting in the shell.

```sh
gh release download claude-code-v2.1.220 \
  -R Luolc/agent-assets-private -p 'claude-*.tar.gz'
tar xzf claude-2.1.220-linux-x64.tar.gz
./claude-2.1.220-linux-x64/claude --version      # -> 2.1.220 (Claude Code)
```

Needs a token with `repo` scope. Each release carries the tarball, its
`.sha256`, and `MANIFEST.txt` uploaded **separately**, so the manifest can be
read without pulling ~96 MB.

Round-trip verified 2026-08-05: downloaded from the release, sha256 matched the
build output byte for byte, and the downloaded artifact ran on `alpine:3.19`.

### Options considered

| Option | Verdict |
|---|---|
| **Private GitHub repo + Release assets** | **Chosen** — see above |
| Private Hugging Face repo | Fine — `hf_hub_download` with `HF_TOKEN` |
| Private object store (R2/S3) | Fine, and R2 is already on this repo's roadmap (task 13) |
| *Any public channel* | **Excluded** |

### Contents

One complete tarball — agent, loader, glibc, NSS modules, `libgcc_s`, `rg`,
launcher, licenses — so a target machine needs **no egress at all** to use it.
Unpack and run, which covers airgapped downstream machines for free.

The runtime layer (everything except `claude.real`) is separable, and the
layout keeps that seam clean (§4) in case a runtime-only artifact is ever
wanted. Not split today: one tarball is what downstream wants.

`LICENSES/` carries the bundled components' license texts, and `MANIFEST.txt`
records each one's version and source package, so the bundle is auditable
without unpacking it.

---

## 1. Version policy

### Resolving

The `stable` channel resolves cleanly — **no scraping, no guessed URL shape**:

```console
$ curl -fsSL https://downloads.claude.ai/claude-code-releases/stable
2.1.220
$ curl -fsSL https://downloads.claude.ai/claude-code-releases/latest
2.1.222
```

Both return a bare version string. `stable` trails `latest` by roughly a week
and skips releases with known major regressions — the right channel for a
harness. `build.sh` resolves `stable`, then **pins** the result into
`VERSION`; a rebuild from a checked-out `VERSION` re-fetches that exact version
and is byte-identical (`./build.sh --pinned`).

`./build.sh --version 2.1.222` overrides, and the script prints loudly which
path it took.

### The floor

`build.sh` refuses to build below **2.1.214**. Below it the `stream-json` exit drain is
capped at ~2 s and can silently truncate the final `result` message on long
runs. We consume that stream as our trace
(`swe_lab.harnesses.claude_code.convert.event_stream_complete` reads exactly
that terminal `result` event), so a truncated tail reads as *"the agent never
finished"* — a silent, run-invalidating failure.

`stable` (2.1.220) is above the floor. The floor check stays anyway: it guards
a `--version` override and any future channel regression.

---

## 2. Why glibc-old-baseline, not the musl build

A `linux-x64-musl` build exists (`platforms` lists it — §3). We are not using
it. Recorded here so it isn't re-litigated.

| | **glibc old baseline** (chosen) | **musl build** |
|---|---|---|
| Runs on glibc targets | ✅ forward compat — an old glibc runs under a newer kernel/host fine | ✅ |
| Runs on musl/Alpine | ✅ nothing comes from the host | ✅ natively |
| Bundle size | ~275 MB agent + ~12 MB runtime | ~275 MB, no runtime needed |
| Allocator under load | glibc `malloc` — arena-per-thread, what the Bun/JSC runtime is tuned against | musl's allocator is simple and contended; a poor fit for a ~275 MB JS runtime under parallel batch load |
| DNS resolver | glibc: honors `search` / `ndots` from `resolv.conf` | musl differs here — a known source of Kubernetes failures |
| Upstream traffic | the well-travelled target | the less-travelled one |
| Complexity | loader indirection + dlopen'd libs to get right (§5) | none |

The resolver point is not academic **for us**: the proxy-capture path points the
agent at `ANTHROPIC_BASE_URL` by **hostname**
(`CONTAINER_PROXY_HOST = "host.docker.internal"` in the harness constants), and
`host.docker.internal` is exactly the kind of name whose resolution depends on
`search`/`ndots` handling. A resolver difference there fails the capture path,
not the version check.

The musl build's honest advantage is simplicity. We are paying complexity once,
in a build script, to avoid an allocator and a resolver we have not
characterized under our own batch load.

**Baseline: `debian:11-slim` (glibc 2.31), pinned by digest.** Verified
2026-08-05 at `sha256:4a2e40d0…baf490`:

```console
$ ldd --version | head -1
ldd (Debian GLIBC 2.31-13+deb11u14) 2.31
$ ls /lib/x86_64-linux-gnu/ | grep -E '^(libpthread|librt|libdl)\.so'
libdl.so.2   libpthread.so.0   librt.so.1
```

Sanity check that the baseline is old enough: glibc **2.34** merged
`libpthread`, `librt` and `libdl` into `libc.so.6`. A correct old-baseline
bundle therefore contains **separate** `libpthread.so.0`, `librt.so.1`,
`libdl.so.2` next to `libc.so.6`. If those three are missing, the base is 2.34+
and too new — the build stops.

---

## 3. Obtaining and verifying the agent

The full chain was executed on 2026-08-05 and **passes**:

```console
$ B=https://downloads.claude.ai/claude-code-releases; V=2.1.220
$ curl -fsSLO "$B/$V/manifest.json"
$ curl -fsSLO "$B/$V/manifest.json.sig"
$ curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o key.asc
$ gpg --import key.asc && gpg --verify manifest.json.sig manifest.json
gpg: Signature made Fri Jul 24 15:39:53 2026 PDT
gpg:                using RSA key 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
gpg: Good signature from "Anthropic Claude Code Release Signing <security@anthropic.com>"
```

The fingerprint matches the expected
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE` exactly. The build **pins that
fingerprint** and fails hard on mismatch — `gpg --verify` alone is not enough,
since any key in the local keyring would satisfy it.

`gpg` reports *"key is not certified with a trusted signature"*. That is
expected and not a failure: we trust the key by pinned fingerprint, not by web
of trust. The build must assert the fingerprint explicitly rather than grepping
for `Good signature`.

### Manifest shape — and the one deviation from the brief

```console
$ jq 'keys' manifest.json
["buildDate","commit","platforms","sdkCompat","version"]
$ jq '.platforms["linux-x64"]' manifest.json
{ "binary": "claude",
  "checksum": "674f61f20ff306f3100cf9200e4c36c4b70278b5bef2884549819b942a89c863",
  "size": 275012592 }
$ jq -r '.platforms | keys[]' manifest.json
darwin-arm64  darwin-x64  linux-arm64  linux-arm64-musl
linux-x64     linux-x64-musl  win32-arm64  win32-x64
```

**The manifest contains no URL or path field.** It gives a *filename*
(`binary: "claude"`), a checksum and a size. The brief said to stop rather than
invent a path if this happened, so: flagging it.

The URL is **composed**, not guessed:

```
{BASE}/{version}/{platform}/{binary}
```

This is not speculation — it is the layout this repo already downloads from
successfully today (`swe_lab/harnesses/claude_code/binary.py` composes exactly
`f"{DOWNLOAD_BASE_URL}/{version}/{platform}/claude"` and has been fetching real
binaries with matching checksums since task 06). The manifest supplies the
final component; the rest is the established, in-production layout. If a future
manifest gains an explicit URL field, prefer it.

Size note: the brief expected "roughly 265 MB"; the manifest says **275 012 592
bytes (~262 MiB)**. Same artifact, different unit convention — not a red flag.

Verify the downloaded binary's sha256 against `platforms.linux-x64.checksum`.
Verifying the manifest's signature transitively verifies every binary it lists.

---

## 4. Bundle anatomy

The agent binary must **not** sit in a directory that lands on `PATH` — if the
raw binary were reachable as `claude`, anything resolving it by name would
execute it directly, bypassing the bundled loader and falling back to host
glibc.

```
claude-<version>-linux-x64/
  claude          # launcher script — the entrypoint
  claude.real     # the agent binary (shipped; see §0 — private channels only)
  lib/            # loader + glibc + libgcc_s + NSS modules
  tools/          # vendored rg — on PATH
  VERSION
  MANIFEST.txt    # every file, its sha256, and the package/image it came from
  LICENSES/       # glibc (LGPL-2.1+), ripgrep (MIT/Unlicense)
```

**Permissions.** After assembly the build runs `chmod -R a+rX` over the bundle
and `chmod a+rx` on `claude`, `claude.real`, `lib/ld-linux-x86-64.so.2` and
`tools/rg`. The tarball is unpacked in images that run as **non-root** and by
users other than the one that built it; a mode that happens to work for the
builder is not a mode that works in the field. `a+rX` (capital X) is the right
tool: world-readable everywhere, directories traversable, and the execute bit
added only where one already exists. Preserve it through tar with `--mode`
left alone (tar records the mode) and verify after unpack in the smoke test.

### Libraries to include

**(a) Everything in `NEEDED`** — enumerated at build time, never hardcoded, as
it is version-dependent:

```sh
objdump -p claude.real | awk '/NEEDED/{print $2}'
```

The output goes into the build log **and** into `MANIFEST.txt`.

**(b) The loader** — `ld-linux-x86-64.so.2` (aarch64: `ld-linux-aarch64.so.1`).

**(c) The dlopen'd libraries that never appear in `NEEDED`.** These are what
hand-built bundles forget, and each fails in a way a naive smoke test passes:

| Library | Why it is dlopen'd | Symptom if missing |
|---|---|---|
| `libnss_dns.so.2`, `libnss_files.so.2` | glibc's `getaddrinfo` dlopens per `/etc/nsswitch.conf` | **Partial** DNS: `/etc/hosts` lookups succeed, real DNS fails. The single most likely field failure, and it passes `claude --version` |
| `libgcc_s.so.1` | stack unwinding, `pthread_cancel` | Latent crash under error paths — not a startup error |
| gconv modules | `iconv` charset conversion | Charset errors only. **Not bundled by default**; documented with the `GCONV_PATH` fix |

**(d) Same-image rule.** The loader, glibc, NSS modules and `libgcc_s` all come
from the **same builder image** as the glibc being bundled. Mixing versions
across images defeats the entire exercise.

### ripgrep is not optional

Claude Code extracts its built-in ripgrep and spawns it as a **child process**.
Children do **not** inherit `--library-path`, so an extracted `rg` links against
**host** glibc and breaks on old or musl images — while `claude --version` still
looks perfectly healthy.

So: vendor `rg` into `tools/`, put `tools/` on `PATH`, and set
`USE_BUILTIN_RIPGREP=0`. Prefer a statically linked upstream release, verified
with `file` reporting `statically linked`; otherwise take the distro package and
confirm its own `NEEDED` set is satisfied by `lib/`.

---

## 5. The launcher, line by line

POSIX `sh`, not bash — the target may have no bash.

```sh
#!/bin/sh
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
export USE_BUILTIN_RIPGREP=0
export PATH="${script_dir}/tools:$PATH"
export DISABLE_AUTOUPDATER=1
export DISABLE_UPDATES=1
exec "${script_dir}/lib/ld-linux-x86-64.so.2" \
  --library-path "${script_dir}/lib" \
  "${script_dir}/claude.real" "$@"
```

Every line is load-bearing:

- **Never `export LD_LIBRARY_PATH`.** Claude Code spawns host `bash`, `git`,
  and whatever `Bash(...)` the prompt allows. A global `LD_LIBRARY_PATH`
  pointing at our 2.31 libc makes those host binaries load it and die with
  `version GLIBC_2.34 not found`. `--library-path` applies to **this exec
  only** and does not propagate to children. The failure is intermittent and
  tool-specific, so a version check will never reveal it.
- **`CDPATH=`** stops a `CDPATH` in the environment from making `cd` write the
  resolved path to stdout and land somewhere else.
- **`--`** guards a path beginning with a dash.
- **`|| exit 1`** — without it a failed `cd` leaves `script_dir` empty and the
  `exec` silently becomes the **host** loader with host libs. Silent fallback
  to the exact thing this bundle exists to avoid.
- **`PATH` gets `tools/`, never the directory holding `claude.real`** (§4).
- **Both `DISABLE_AUTOUPDATER` and `DISABLE_UPDATES`** — the first stops only
  the background check, the second blocks every update path including manual.
  This is a pinned artifact; silent version drift invalidates version-specific
  downstream behavior. (From v2.1.207 the auto-updater no longer overwrites a
  custom launcher; don't rely on it.)
- **`$0`-based resolution breaks under symlink invocation.** Documented limit —
  always call the launcher by absolute path.

---

## 6. Deliverables

| Path | What |
|---|---|
| `packaging/claude-code-bundle/Dockerfile.bundle` | hermetic builder, base pinned **by digest** |
| `packaging/claude-code-bundle/build.sh` | host driver: resolve version → docker build → extract tarball |
| `packaging/claude-code-bundle/launcher.sh` | §5, copied into the bundle |
| `packaging/claude-code-bundle/smoke-test.sh` | the §7 matrix |
| `packaging/claude-code-bundle/VERSION` | the pinned resolved version |
| `MANIFEST.txt` | generated **into the bundle**: every file, sha256, source package/image |
| `packaging/claude-code-bundle/dist/` | build output — **gitignored**, never committed |

---

## 7. Smoke test

Unpack into each target and report a pass/fail table:

`debian:12` · `ubuntu:22.04` · `ubuntu:20.04` · `alpine:3.19` ·
`gcr.io/distroless/base-debian12` · one deliberately ancient glibc image

Distroless has no shell — unpack at image build time in a throwaway Dockerfile
rather than `sh -c`.

Every target must pass **all** of:

1. `claude --version` prints exactly `VERSION`.
2. **DNS via a hostname** — point `ANTHROPIC_BASE_URL` at a hostname served by
   a throwaway local listener and confirm the request arrives. This is the NSS
   check and the one that matters. A ping or an IP-based check does **not**
   exercise the dlopen path and must not be substituted.
3. **ripgrep** — a `claude -p` run whose prompt forces a content search, or
   `tools/rg` directly under the same conditions.
4. **Subprocess sanity** — `claude -p` with `--allowedTools "Bash(echo *)"`,
   proving host binaries were not poisoned (the `LD_LIBRARY_PATH` trap).
5. **No host leakage** — `/proc/<pid>/maps` shows **our** `lib/`, not the
   host's.
6. **Stream integrity** — `claude -p --output-format stream-json --verbose`
   with a large response; the final line must parse as a `result` message. The
   regression guard for the pre-2.1.214 truncation bug. Keep it even though the
   pin is above the floor.
7. **Permissions after unpack** — `claude`, `claude.real`, the loader and
   `tools/rg` are `a+rx` when unpacked by a non-root user (§4).

Non-zero exit on any failure, naming the check and the image.

### Result — 2026-08-05, `claude-2.1.220-linux-x64.tar.gz` (96 MB)

```
pass=21 fail=0 skip=10
```

Green on every image, **including `alpine:3.19` (musl) and `debian:10-slim`
(glibc 2.28)** — the two targets the whole exercise exists for — and on
distroless. The 10 skips are the live-agent checks (4 and 6), which need
`CLAUDE_CODE_OAUTH_TOKEN`; they report as SKIP, never as a silent pass.

The first run failed 6 checks, and both causes are worth keeping:

**1. `no-host-leakage` was wrong, not the bundle.** It backgrounded the process
and read `/proc/<pid>/maps` after 1 s — but `--version` exits immediately, so
maps was always empty and every image reported a false failure. Replaced with
the loader's own `--list`, which is deterministic and needs no live process:

```console
$ ld-linux-x86-64.so.2 --library-path <bundle>/lib --list <bundle>/claude.real   # on alpine
  libc.so.6 => /w/claude-2.1.220-linux-x64/lib/libc.so.6
  libpthread.so.0 => /w/claude-2.1.220-linux-x64/lib/libpthread.so.0
  …6/6 from the bundle
```

Every library — `libc.so.6` included — resolving into the bundle **on a system
with no glibc at all** is the clearest possible statement that nothing comes
from the host.

**2. The `rg` staticness check tested the wrong property.** It asserted
`file` reports `statically linked`; upstream ships a **static-PIE** binary,
which `file` calls `dynamically linked` because it is `ET_DYN`. The check
rejected a perfectly good binary. It now asserts what actually matters — zero
`NEEDED` entries and no program interpreter — which was confirmed by running
that exact `rg` unchanged on alpine, debian:10-slim and distroless.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `version GLIBC_2.34 not found` from `git`/`bash`, not from `claude` | `LD_LIBRARY_PATH` leaked to children | Use `--library-path`; never export `LD_LIBRARY_PATH` |
| `/etc/hosts` names resolve, real DNS does not | `libnss_dns.so.2` / `libnss_files.so.2` missing | Bundle both from the builder image |
| Search silently returns nothing | host `rg` picked up, or `USE_BUILTIN_RIPGREP` unset | Vendor `rg` in `tools/`, set the env var |
| Crash on an error path, clean startup | `libgcc_s.so.1` missing | Bundle it |
| `iconv`/charset errors | gconv modules absent | Bundle them and set `GCONV_PATH` |
| Version drifts after deploy | updates not fully disabled | Set **both** disable vars |
| Trace truncated, run reads as unfinished | agent below 2.1.214 | Rebuild at ≥ the floor |
| Works as root, `permission denied` as app user | mode lost on unpack | `chmod -R a+rX`; verify in smoke test |
| TLS failures on a minimal image | no CA store (out of scope, §10) | Caller ships certs, sets `SSL_CERT_FILE` |
| `exec …/claude: no such file or directory` on a shell-less image, and the file plainly exists | The launcher is `#!/bin/sh`; the message names the missing **interpreter**, not the script | Invoke the loader directly and set the launcher's env vars yourself (§11) |
| `file` says `rg` is "dynamically linked" | static-PIE is `ET_DYN`, and `file` misreports it | Check `objdump -p` NEEDED (0) and the absence of a program interpreter instead |

---

## 9. How this lands in swe-lab

The bundle replaces the *contents* of what provisioning installs, not the seam.
Today `binary.py` downloads one binary and the two backend observers place it
at `BINARY_AT = /opt/claude-code/claude`
(`HostClaudeCodeBinaryObserver` mounts a host-cached copy;
`GitHubJobClaudeCodeBinaryObserver` downloads in the job).

The change is small: `ensure_claude_binary` gains a sibling that materializes
the **bundle** rather than a bare binary, and `BINARY_AT` points at the
bundle's `claude` **launcher**. Both observers keep their shape — one mounts a
host-cached bundle directory, the other fetches and unpacks the tarball in the
job.

This is also the concrete motivation. The `ghjob` backend runs **inside
arbitrary instance images**; the repo has already been bitten once by assuming
what those images ship (`rollout-ghjob.yml`: *"plenty of them ship no curl"*).
A musl-based instance image cannot run today's glibc-linked binary at all.

Sequenced separately from the build work — the build lands first and is proven
by its own smoke matrix.

---

## 10. Out of scope

- **arm64** — parameterize the platform key only; do not build or claim it.
- **Wall-clock timeouts** — owned by the invocation harness.
- **CA certificates** — *documented, not bundled*: minimal images have no CA
  store; the caller ships one and points `SSL_CERT_FILE` at it. Needed anyway
  for the recording proxy's certificate.
- **Auth / credential handling.**

## 11. Known limits

- x86_64 only.
- Symlink invocation of the launcher is unsupported (`$0` resolution, §5).
- **The `claude` launcher needs `/bin/sh`.** On a shell-less image (distroless)
  it cannot be the entrypoint — exec fails with `no such file or directory`,
  which names the missing interpreter, not the script. Invoke the loader
  directly and set what the launcher would have exported. Verified working:

  ```dockerfile
  ENV USE_BUILTIN_RIPGREP=0 DISABLE_AUTOUPDATER=1 DISABLE_UPDATES=1 \
      PATH=/w/<bundle>/tools:/usr/bin:/bin
  ENTRYPOINT ["/w/<bundle>/lib/ld-linux-x86-64.so.2", \
              "--library-path", "/w/<bundle>/lib", \
              "/w/<bundle>/claude.real"]
  ```
- CA certificates are the caller's responsibility (§10).
- **Internal use only — the artifact must never be published** (§0).

---

## 12. Decisions taken

1. **Hosting** — `Luolc/agent-assets-private`, tag `claude-code-v<version>`
   (§0). Live and round-trip verified.
2. **Builder base** — `debian:11-slim` @ `sha256:4a2e40d0…baf490`, glibc
   2.31-13+deb11u14, verified pre-2.34 (§2).
3. **ADR** — deferred. The glibc-over-musl reasoning lives in §2 for now.
4. **Location** — `packaging/claude-code-bundle/`. Not `build/`: that path is
   already in `.gitignore` (standard Python build dir), so committed sources
   under it would be silently ignored.

### Still open

- **Wiring into swe-lab** (§9) is designed but not built — sequenced after this.
- **Live-agent smoke checks** (4 and 6) have never run green, only SKIP: they
  need `SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN` in the environment (the script renames
  it for the container; the host-side name is never `CLAUDE_CODE_OAUTH_TOKEN` —
  see [conventions → Hazards](../../conventions.md#hazards-learned-the-hard-way)).
  Run once with a token before trusting the bundle in a real rollout.
