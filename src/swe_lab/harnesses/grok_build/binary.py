"""Fetch a pinned Grok Build binary, checksum-verified.

Same job as the ``codex`` module of this name, and the same seam: this module
only *gets the bytes*; **where they land is the sandbox's call** (a Docker
sandbox caches one copy on the host; a CI job downloads to the final path; a
remote sandbox declares the mount in its config before it comes up).

Grok Build's distribution is Claude Code's shape, not Codex's (task-29 §2): a
channel pointer resolves to a version, and the versioned artifact is a **bare
binary** — no tarball, nothing to extract, and exactly **one** binary (no
``code-mode-host`` analogue; verified by a live tool-using run).

What it does *not* have is any verification of its own: the official installer
was read in full and checks nothing — no sha256, no signature. So the checksum
is **pinned here, in-repo**: trust-on-first-use when the pin is set, enforced
on every later fetch on every machine, which is the property that protects a
sweep from a silently changed artifact (task-28 §3's answer, unchanged).
"""

from __future__ import annotations

import hashlib
import os
import urllib.request

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

# Cloudflare-fronted primary; the installer's GCS fallback is deliberately not
# encoded here — a fetch that fails should fail loudly rather than silently
# read from a second host the pin was never checked against.
DOWNLOAD_BASE_URL = "https://x.ai/cli"

# Pinned so the agent build is reproducible across a rollout batch. The
# `stable` channel resolved to this on 2026-08-11. Bump deliberately, together
# with the checksum below, and re-run the portability matrix after: a release
# that switched Linux off musl would install fine and then fail inside a
# minimal image.
PINNED_GROK_BUILD_VERSION = "1.0.0"

# The installer's platform key (`<os>-<arch>`); carries no libc triple — Linux
# ships exactly one artifact, which IS the musl build (task-29 §1). The
# rollout container is linux/amd64; `linux-aarch64` exists upstream but is not
# claimed until the matrix has run on it.
LINUX_X64 = "linux-x86_64"

# sha256 of the bare binary (there is no archive), keyed by (version,
# platform). Measured 2026-08-11 for the pin above.
BINARY_SHA256: dict[tuple[str, str], str] = {
    (
        "1.0.0",
        "linux-x86_64",
    ): "28dbc967a5843dae2374b6834dadbab95354e685c7e5c8dc750b92a4e5fc7c3e",
}

_FETCH_TIMEOUT_S = 60.0
_DOWNLOAD_TIMEOUT_S = 600.0
_BIN_SUBDIR = "bin"
_CACHE_NAMESPACE = "grok-build"


def latest_version(channel: str = "stable") -> str:
  """Resolve a channel pointer to a version string.

  Grok Build has a real channel endpoint (Codex does not), so the
  claude_code-style resolve-then-pin flow works here. Resolution is
  informational — the fetch below only ever downloads a version whose checksum
  is pinned.

  Args:
    channel: The channel name (``stable``).

  Returns:
    The version string the channel currently points at.
  """
  raw = _get(f"{DOWNLOAD_BASE_URL}/{channel}", timeout=_FETCH_TIMEOUT_S)
  return raw.decode().strip()


def binary_url(
    *, version: str = PINNED_GROK_BUILD_VERSION, platform: str = LINUX_X64
) -> str:
  """Return the download URL of the bare binary.

  Args:
    version: Grok release to fetch.
    platform: The installer's platform key.

  Returns:
    The artifact URL.
  """
  return f"{DOWNLOAD_BASE_URL}/grok-{version}-{platform}"


def binary_checksum(version: str, platform: str) -> str:
  """Return the pinned sha256 of the binary.

  Args:
    version: Grok release.
    platform: The installer's platform key.

  Returns:
    The expected sha256 hex of the bare binary.

  Raises:
    ValueError: If no checksum is pinned for this pair — a deliberate refusal
      rather than an unverified download, since upstream publishes no checksum
      at all for us to fall back on.
  """
  checksum = BINARY_SHA256.get((version, platform))
  if checksum is None:
    raise ValueError(
        f"no pinned sha256 for grok {version}/{platform}; add one to"
        " BINARY_SHA256 after verifying the download (the official installer"
        " performs no verification of its own)"
    )
  return checksum


def binary_cache_path(
    *,
    version: str = PINNED_GROK_BUILD_VERSION,
    platform: str = LINUX_X64,
    repo_root: epath.PathLike | None = None,
) -> epath.Path:
  """Return the on-disk cache path of the ``version``/``platform`` binary.

  A file, not a directory: grok is one binary with no companion (task-29 §3),
  so the claude_code shape applies rather than codex's directory.

  Args:
    version: Grok release.
    platform: The installer's platform key.
    repo_root: Repository root used to locate the cache; discovered when
      omitted.

  Returns:
    The cache path, whose layout matches the other harnesses' by construction.
  """
  root = repo_root or find_repo_root()
  return (
      cache_root(root)
      / _BIN_SUBDIR
      / _CACHE_NAMESPACE
      / version
      / platform
      / "grok"
  )


def ensure_grok_binary(
    *,
    version: str = PINNED_GROK_BUILD_VERSION,
    platform: str = LINUX_X64,
    dest: epath.PathLike | None = None,
    repo_root: epath.PathLike | None = None,
    refresh: bool = False,
) -> epath.Path:
  """Ensure the pinned Grok binary is at ``dest``, checksum-verified.

  Idempotent: an existing file is reused unless ``refresh`` is set. The
  artifact is a bare binary, so the downloaded bytes are hashed directly and
  written only when they match the pin; the file is made executable so it can
  be copied around and run directly.

  Args:
    version: Grok release to fetch.
    platform: The installer's platform key (the container is always
      linux/amd64).
    dest: Where the binary must end up. Defaults to the host cache, which is
      what a backend handing copies to its sandboxes wants — and what a remote
      sandbox declaring a host-path mount in its config needs, since that
      mount is resolved before the sandbox exists.
    repo_root: Repository root used to locate the default cache; discovered
      when omitted. Unused when ``dest`` is given.
    refresh: If true, re-download even when a binary is already there.

  Returns:
    The path of the verified, executable binary (``dest``).

  Raises:
    ValueError: If no checksum is pinned, or the download does not match it.
  """
  target = (
      epath.Path(dest)
      if dest is not None
      else binary_cache_path(
          version=version, platform=platform, repo_root=repo_root
      )
  )
  if not refresh and target.is_file():
    return target

  expected = binary_checksum(version, platform)
  data = _get(binary_url(version=version, platform=platform))
  actual = hashlib.sha256(data).hexdigest()
  if actual != expected:
    raise ValueError(
        f"checksum mismatch for grok {version}/{platform}: "
        f"expected {expected}, got {actual}"
    )
  target.parent.mkdir(parents=True, exist_ok=True)
  _ = target.write_bytes(data)
  os.chmod(target, 0o755)
  return target


def _get(url: str, *, timeout: float = _DOWNLOAD_TIMEOUT_S) -> bytes:
  """Fetch ``url`` and return its bytes."""
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()
