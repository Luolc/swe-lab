"""Fetch a pinned native Claude Code binary, checksum-verified.

``rollout`` runs a headless coding agent *inside* each instance's prebuilt
image. Rather than bake Claude Code into ~731 images (npm-in-a-wrapper), we
download a **single pinned native binary** and put it in the sandbox at run
time.

This module only *gets the bytes*; **where they land is the sandbox's call**,
because the answer differs per backend: a Docker sandbox caches one copy on the
host (gitignored, never committed) and copies it into each container, while a CI
job — whose filesystem already *is* the sandbox — downloads straight to the
final path. Each backend's own observer does that, so nothing here, and nothing
in the harness, has to choose for all of them.

The download scheme is Anthropic's official one (from ``claude.ai/install.sh`` →
``downloads.claude.ai/claude-code-releases/bootstrap.sh``):

- ``{BASE}/latest`` → the latest version string (e.g. ``2.1.212``);
- ``{BASE}/{version}/manifest.json`` → ``.platforms[<platform>].checksum`` (a
  64-char sha256 hex per platform);
- ``{BASE}/{version}/{platform}/claude`` → the single self-contained binary.

We pin a version so a rollout can't silently pick up a new agent build mid-run;
bump :data:`PINNED_CLAUDE_CODE_VERSION` deliberately. The container is
``linux/amd64``, so the platform we fetch is always :data:`LINUX_X64`,
regardless of the host we download from (the bytes are host-agnostic; we only
run them in the container).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

DOWNLOAD_BASE_URL = "https://downloads.claude.ai/claude-code-releases"
# Pinned so the agent build is reproducible across a rollout batch. This was the
# latest release when rollout was built (2026-07-16); bump deliberately, and
# only after confirming the new binary still runs headless as rollout wants.
PINNED_CLAUDE_CODE_VERSION = "2.1.212"
# The rollout container is linux/amd64, so this is the only platform we fetch.
LINUX_X64 = "linux-x64"

_FETCH_TIMEOUT_S = 60.0
_DOWNLOAD_TIMEOUT_S = 600.0
_BIN_SUBDIR = "bin"
_CACHE_NAMESPACE = "claude-code"


def binary_cache_path(
    *,
    version: str = PINNED_CLAUDE_CODE_VERSION,
    platform: str = LINUX_X64,
    repo_root: epath.PathLike | None = None,
) -> epath.Path:
  """Return the on-disk cache path of the ``version``/``platform`` binary."""
  root = repo_root or find_repo_root()
  return (
      cache_root(root)
      / _BIN_SUBDIR
      / _CACHE_NAMESPACE
      / version
      / platform
      / "claude"
  )


def latest_version() -> str:
  """Resolve the current ``latest`` Claude Code version string."""
  return _get(f"{DOWNLOAD_BASE_URL}/latest").decode().strip()


def manifest_checksum(version: str, platform: str) -> str:
  """Return the expected sha256 hex of the ``version``/``platform`` binary."""
  raw = _get(f"{DOWNLOAD_BASE_URL}/{version}/manifest.json")
  manifest = json.loads(raw)
  platforms = (
      manifest.get("platforms", {}) if isinstance(manifest, dict) else {}
  )
  entry = platforms.get(platform, {}) if isinstance(platforms, dict) else {}
  checksum = entry.get("checksum") if isinstance(entry, dict) else None
  if not isinstance(checksum, str) or not checksum:
    raise ValueError(
        f"no checksum for platform {platform!r} in the {version} manifest"
    )
  return checksum


def ensure_claude_binary(
    *,
    version: str = PINNED_CLAUDE_CODE_VERSION,
    platform: str = LINUX_X64,
    dest: epath.PathLike | None = None,
    repo_root: epath.PathLike | None = None,
    refresh: bool = False,
) -> epath.Path:
  """Ensure the pinned native binary is at ``dest``, checksum-verified.

  Idempotent: a binary already there whose sha256 matches the release manifest
  is reused; otherwise it is (re)downloaded and verified. The file is made
  executable so it can be copied around and run directly.

  Args:
    version: Claude Code release to fetch.
    platform: Release platform key (the container is always linux/amd64).
    dest: Where the binary must end up. Defaults to the host cache, which is
      what a backend that hands copies to its sandboxes wants; a sandbox that
      *is* the local filesystem passes the final in-sandbox path instead and
      skips the extra copy.
    repo_root: Repository root used to locate the default cache; discovered
      when omitted. Unused when ``dest`` is given.
    refresh: If true, re-download even when a valid binary is already there.

  Returns:
    The path of the verified, executable binary (``dest``).

  Raises:
    ValueError: If the downloaded bytes do not match the manifest checksum
      (a corrupt or tampered download is never silently used).
  """
  target = (
      epath.Path(dest)
      if dest is not None
      else binary_cache_path(
          version=version, platform=platform, repo_root=repo_root
      )
  )
  expected = manifest_checksum(version, platform)
  if not refresh and target.is_file() and _sha256(target) == expected:
    return target

  target.parent.mkdir(parents=True, exist_ok=True)
  data = _get(
      f"{DOWNLOAD_BASE_URL}/{version}/{platform}/claude",
      timeout=_DOWNLOAD_TIMEOUT_S,
  )
  actual = hashlib.sha256(data).hexdigest()
  if actual != expected:
    raise ValueError(
        f"checksum mismatch for claude {version}/{platform}: "
        f"expected {expected}, got {actual}"
    )
  _ = target.write_bytes(data)
  os.chmod(target, 0o755)
  return target


def _get(url: str, *, timeout: float = _FETCH_TIMEOUT_S) -> bytes:
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()


def _sha256(path: epath.PathLike) -> str:
  digest = hashlib.sha256()
  with open(path, "rb") as handle:
    for chunk in iter(lambda: handle.read(1 << 20), b""):
      digest.update(chunk)
  return digest.hexdigest()
