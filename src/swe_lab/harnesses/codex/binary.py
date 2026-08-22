"""Fetch a pinned Codex binary, checksum-verified.

Same job as the ``claude_code`` module of this name, and the same seam: this
module only *gets the bytes*; **where they land is the sandbox's call**,
because the answer differs per backend (a Docker sandbox caches one copy on the
host and hands it to each container; a CI job whose filesystem already *is* the
sandbox downloads straight to the final path; a remote sandbox declares the
mount in its config **before** it comes up, so the file has to exist on the host
first). Nothing here, and nothing in the harness, chooses for all of them.

Two things differ from Claude Code's scheme, both measured (task-28):

- **The download is a GitHub release asset**, not a versioned CDN path, so the
  URL carries the ``rust-v`` tag and the payload is a one-entry ``.tar.gz``
  rather than a bare binary.
- **The checksum is pinned here, in-repo.** Upstream publishes
  ``codex-package_SHA256SUMS``, but it covers only the *package* archives —
  verified: it has zero entries for the asset we want — and the bare binary
  ships a cosign ``.sigstore`` bundle instead. Trust-on-first-use when the pin
  is set, pinned for every fetch afterwards on every machine, which is the
  property that actually protects a sweep from a silently changed artifact.

The Linux build is **statically linked musl**, so it runs unmodified on musl,
ancient glibc and distroless images alike; there is no bundle to build and no
launcher to invoke it through (task-28 §1).
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import io
import os
import tarfile
import urllib.request

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

RELEASES_BASE_URL = "https://github.com/openai/codex/releases/download"

# The two binaries a working run needs, as release-asset stems. The second is
# **not optional**, and the cost of learning that is recorded here so nobody
# trims it: with `codex-code-mode-host` absent, `codex` starts, authenticates
# and answers — but every attempt to run a command or edit a file fails, and
# the agent reports "the workspace execution host is disabled" while the run
# still exits 0. A rollout would look healthy and produce an empty patch.
# Measured 2026-08-08 on 0.147.0; the host is spawned from a path derived as a
# *sibling* of the codex binary, so both must land in one directory.
CODEX_STEM = "codex"
CODE_MODE_HOST_STEM = "codex-code-mode-host"
BINARY_STEMS = (CODEX_STEM, CODE_MODE_HOST_STEM)

# Pinned so the agent build is reproducible across a rollout batch. The latest
# *stable* release as of 2026-08-22 (the newer `0.149.0-alpha.*` and
# `0.150.0-alpha.*` tags are prereleases and deliberately not used). Bump
# deliberately, together with the checksum below, and re-run the portability
# matrix after: a release that switched Linux off musl would still install fine
# and then fail inside a minimal image. Re-run for this pin on 2026-08-22 —
# still static musl, see the checksums below.
PINNED_CODEX_VERSION = "0.149.0"

# The Rust target triple, which is also the release asset's platform key. The
# rollout container is linux/amd64; `aarch64-unknown-linux-musl` is published
# upstream too but is not claimed until the matrix has run on it.
LINUX_X64 = "x86_64-unknown-linux-musl"

# sha256 of each release *tarball* (not the extracted binary), keyed by
# (stem, version, target). Measured on the date each entry names.
#
# Superseded versions are **kept**, not deleted: `version` is a harness field,
# so a run may deliberately pin an older build, and an unpinned version is
# refused rather than fetched unverified. Dropping the old rows would turn a
# verified downgrade into an error.
ARCHIVE_SHA256: dict[tuple[str, str, str], str] = {
    # 0.149.0 — measured 2026-08-22. Portability re-verified the same day:
    # `codex --version` reports 0.149.0 on `alpine:3.19` (musl),
    # `gcr.io/distroless/static-debian12` (no libc at all, which is what
    # proves the link is static) and `debian:10` (glibc 2.28).
    (
        "codex",
        "0.149.0",
        "x86_64-unknown-linux-musl",
    ): "7368b2055ed02157fea2695bb9f5af3ee7b0e40c5a3bebc81dfc596704244cfd",
    (
        "codex-code-mode-host",
        "0.149.0",
        "x86_64-unknown-linux-musl",
    ): "3600a45ac2b09fe3c995f4f49860131fea388b46c409c82a0266fc4d0342a04c",
    # 0.147.0 — measured 2026-08-08, the previous pin.
    (
        "codex",
        "0.147.0",
        "x86_64-unknown-linux-musl",
    ): "0246e2e773834e07f0fb5249ed6ebad12e4591e608f8c7bb97dd6a9690544c36",
    (
        "codex-code-mode-host",
        "0.147.0",
        "x86_64-unknown-linux-musl",
    ): "0146adfaac8363ec9fcdb5895f7624db5b2e8617a283887938b7fb97a1dd4356",
}

_DOWNLOAD_TIMEOUT_S = 600.0
_BIN_SUBDIR = "bin"
_CACHE_NAMESPACE = "codex"


def release_tag(version: str = PINNED_CODEX_VERSION) -> str:
  """Return the GitHub release tag for a Codex version.

  Args:
    version: The Codex version (e.g. ``0.147.0``).

  Returns:
    The tag the release assets live under (e.g. ``rust-v0.147.0``).
  """
  return f"rust-v{version}"


def archive_url(
    stem: str,
    *,
    version: str = PINNED_CODEX_VERSION,
    platform: str = LINUX_X64,
) -> str:
  """Return the download URL of one binary's release archive.

  Deliberately the per-binary ``<stem>-<target>.tar.gz`` assets rather than the
  ``-bundle`` or ``-package`` variants: those also carry ``bwrap`` (Codex's own
  sandbox helper, which needs user namespaces that are commonly unavailable
  inside a container, and which we do not want since the container *is* the
  sandbox), a Python runtime and a packaged zsh — none of which this harness
  runs.

  Args:
    stem: The binary's release-asset stem (see :data:`BINARY_STEMS`).
    version: Codex release to fetch.
    platform: Rust target triple of the asset.

  Returns:
    The asset URL.
  """
  return f"{RELEASES_BASE_URL}/{release_tag(version)}/{stem}-{platform}.tar.gz"


def archive_checksum(stem: str, version: str, platform: str) -> str:
  """Return the pinned sha256 of one release archive.

  Args:
    stem: The binary's release-asset stem.
    version: Codex release.
    platform: Rust target triple.

  Returns:
    The expected sha256 hex of the ``.tar.gz``.

  Raises:
    ValueError: If no checksum is pinned for this triple — a deliberate refusal
      rather than an unverified download, since upstream publishes no checksum
      covering these assets for us to fall back on.
  """
  checksum = ARCHIVE_SHA256.get((stem, version, platform))
  if checksum is None:
    raise ValueError(
        f"no pinned sha256 for {stem} {version}/{platform}; add one to"
        " ARCHIVE_SHA256 after verifying the download (upstream's"
        " codex-package_SHA256SUMS does not cover these assets)"
    )
  return checksum


def binary_cache_dir(
    *,
    version: str = PINNED_CODEX_VERSION,
    platform: str = LINUX_X64,
    repo_root: epath.PathLike | None = None,
) -> epath.Path:
  """Return the on-disk cache **directory** holding this version's binaries.

  A directory rather than a file, unlike the Claude Code equivalent: the
  code-mode host is spawned from a path derived as a sibling of the ``codex``
  binary, so the two only work when they are placed together.

  Args:
    version: Codex release.
    platform: Rust target triple.
    repo_root: Repository root used to locate the cache; discovered when
      omitted.

  Returns:
    The cache directory, whose layout matches the other harnesses' by
    construction.
  """
  root = repo_root or find_repo_root()
  return cache_root(root) / _BIN_SUBDIR / _CACHE_NAMESPACE / version / platform


def ensure_codex_binaries(
    *,
    version: str = PINNED_CODEX_VERSION,
    platform: str = LINUX_X64,
    dest: epath.PathLike | None = None,
    repo_root: epath.PathLike | None = None,
    refresh: bool = False,
) -> epath.Path:
  """Ensure the pinned Codex binaries are in ``dest``, checksum-verified.

  Places **both** :data:`BINARY_STEMS` in one directory, because a `codex` on
  its own is a working-looking run that can neither execute a command nor edit
  a file (see the constant's note). Idempotent: existing files are reused
  unless ``refresh`` is set. Each checksum is verified against the **archive**,
  before extraction, so tampered bytes are never unpacked, and each file is
  made executable so it can be copied around and run directly.

  Args:
    version: Codex release to fetch.
    platform: Rust target triple (the container is always linux/amd64).
    dest: The directory both binaries must end up in. Defaults to the host
      cache, which is what a backend handing copies to its sandboxes wants —
      and what a remote sandbox declaring a host-path mount in its config
      needs, since that mount is resolved before the sandbox exists.
    repo_root: Repository root used to locate the default cache; discovered
      when omitted. Unused when ``dest`` is given.
    refresh: If true, re-download even when the binaries are already there.

  Returns:
    The directory holding the verified, executable binaries (``dest``).

  Raises:
    ValueError: If a checksum is not pinned, if a download does not match it,
      or if an archive does not contain the expected binary.
  """
  target_dir = (
      epath.Path(dest)
      if dest is not None
      else binary_cache_dir(
          version=version, platform=platform, repo_root=repo_root
      )
  )
  target_dir.mkdir(parents=True, exist_ok=True)
  for stem in BINARY_STEMS:
    target = target_dir / stem
    if not refresh and target.is_file():
      continue
    expected = archive_checksum(stem, version, platform)
    data = _get(archive_url(stem, version=version, platform=platform))
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
      raise ValueError(
          f"checksum mismatch for {stem} {version}/{platform}: "
          f"expected {expected}, got {actual}"
      )
    _ = target.write_bytes(_extract(data, member=f"{stem}-{platform}"))
    os.chmod(target, 0o755)
  return target_dir


def _extract(archive: bytes, *, member: str) -> bytes:
  """Return one named file's bytes from a ``.tar.gz`` in memory.

  Args:
    archive: The downloaded archive.
    member: The entry to read — named rather than "the only one", so a release
      that starts shipping extra entries is a loud failure instead of a
      silently different binary.

  Returns:
    The member's contents.

  Raises:
    ValueError: If the member is absent or is not a regular file.
  """
  with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
    try:
      info = tar.getmember(member)
    except KeyError as error:
      raise ValueError(
          f"{member!r} not found in the codex archive; entries:"
          f" {tar.getnames()[:10]}"
      ) from error
    if not info.isfile():
      raise ValueError(f"{member!r} in the codex archive is not a regular file")
    handle = tar.extractfile(info)
    if handle is None:
      raise ValueError(f"could not read {member!r} from the codex archive")
    with handle:
      return handle.read()


def _get(url: str, *, timeout: float = _DOWNLOAD_TIMEOUT_S) -> bytes:
  """Fetch ``url`` and return its bytes."""
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()


def asset_materializer(
    stem: str, version: str = PINNED_CODEX_VERSION
) -> Callable[[epath.Path | None], epath.Path]:
  """Return a materializer for one of the two binaries.

  The provisioning seam wants a per-*file* materializer, while Codex's fetch
  is per-*directory* — the two binaries must land together, since the host is
  spawned from a path derived as a sibling. So a destination is read as "put
  the pair in this file's directory", and the requested one is returned.

  Calling this for both stems runs the fetch twice, which costs nothing:
  ``ensure_codex_binaries`` reuses files already present.

  Args:
    stem: Which binary this materializer produces (see :data:`BINARY_STEMS`).
    version: The release to fetch.

  Returns:
    A materializer honoring the seam's contract (``None`` caches, a path
    installs).
  """

  def materialize(dest: epath.Path | None) -> epath.Path:
    directory = ensure_codex_binaries(
        version=version, dest=dest.parent if dest else None
    )
    return directory / stem

  return materialize
