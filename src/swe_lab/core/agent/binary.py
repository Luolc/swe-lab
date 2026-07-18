"""Provision a pinned native Claude Code binary for the rollout container.

``rollout`` runs a headless coding agent *inside* each instance's prebuilt
image. Rather than bake Claude Code into ~731 images (npm-in-a-wrapper), we
download a **single pinned native binary** once, cache it (gitignored, never
committed), and bind-mount it into the container at run time.

The download scheme is Anthropic's official one (from ``claude.ai/install.sh`` →
``downloads.claude.ai/claude-code-releases/bootstrap.sh``):

- ``{BASE}/latest`` → the latest version string (e.g. ``2.1.212``);
- ``{BASE}/{version}/manifest.json`` → ``.platforms[<platform>].checksum`` (a
  64-char sha256 hex per platform);
- ``{BASE}/{version}/{platform}/claude`` → the single self-contained binary.

We pin a version so a rollout can't silently pick up a new agent build mid-run;
bump :data:`PINNED_CLAUDE_CODE_VERSION` deliberately. The container is
``linux/amd64``, so the platform we mount is always :data:`LINUX_X64`,
regardless of the host we download from (the bytes are host-agnostic; we only
run them in the container).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import urllib.request

from ..paths import cache_root, find_repo_root

DOWNLOAD_BASE_URL = "https://downloads.claude.ai/claude-code-releases"
# Pinned so the agent build is reproducible across a rollout batch. This was the
# latest release when rollout was built (2026-07-16); bump deliberately, and
# only after confirming the new binary still runs headless as rollout wants.
PINNED_CLAUDE_CODE_VERSION = "2.1.212"
# The rollout container is linux/amd64, so this is the only platform we mount.
LINUX_X64 = "linux-x64"

_FETCH_TIMEOUT_S = 60.0
_DOWNLOAD_TIMEOUT_S = 600.0
_BIN_SUBDIR = "bin"
_CACHE_NAMESPACE = "claude-code"


def binary_cache_path(
    *,
    version: str = PINNED_CLAUDE_CODE_VERSION,
    platform: str = LINUX_X64,
    repo_root: Path | None = None,
) -> Path:
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
    repo_root: Path | None = None,
    refresh: bool = False,
) -> Path:
  """Ensure the pinned native binary is cached and checksum-verified.

  Idempotent: a cached binary whose sha256 matches the release manifest is
  reused; otherwise it is (re)downloaded and verified. The file is made
  executable so it can be mounted and run directly in the container.

  Args:
    version: Claude Code release to fetch.
    platform: Release platform key (the container is always linux/amd64).
    repo_root: Repository root used to locate the cache; discovered when
      omitted.
    refresh: If true, re-download even when a valid cached binary exists.

  Returns:
    The path of the verified, executable binary.

  Raises:
    ValueError: If the downloaded bytes do not match the manifest checksum
      (a corrupt or tampered download is never silently used).
  """
  dest = binary_cache_path(
      version=version, platform=platform, repo_root=repo_root
  )
  expected = manifest_checksum(version, platform)
  if not refresh and dest.is_file() and _sha256(dest) == expected:
    return dest

  dest.parent.mkdir(parents=True, exist_ok=True)
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
  _ = dest.write_bytes(data)
  dest.chmod(0o755)
  return dest


def _get(url: str, *, timeout: float = _FETCH_TIMEOUT_S) -> bytes:
  with urllib.request.urlopen(url, timeout=timeout) as response:
    return response.read()


def _sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1 << 20), b""):
      digest.update(chunk)
  return digest.hexdigest()
