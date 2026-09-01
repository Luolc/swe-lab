"""Build the ``cc-reverse-proxy`` binary that records a run's API traffic.

Proxy capture used to run the proxy on the **host**: one process per run, bound
to a host port, dialled back from the container over the Docker host gateway.
That made a *required* component depend on three fragile things at once — a
host firewall rule opening the Docker bridge, an unbounded port derived from a
dataset index (and a second, independent base for the aggregator), and a
listener on every interface, which on a tailnet host means every node on the
tailnet.

The proxy now runs **inside the sandbox**, which removes all three: a container
has its own network namespace, so the port is a fixed constant that cannot
collide with anything; the agent dials the sandbox's own loopback, so there is
no firewall rule left to need; and the host binds nothing. It is also the only
shape that works on a backend where the host has no foothold at all — a
``GitHubJobSandbox`` is handed a job that is already running, and a host-side
process has nowhere to live in it.

So this module does not *run* anything anymore. It only produces the bytes,
exactly as :mod:`~swe_lab.harnesses.claude_code.binary` does for the agent
itself, and the sandbox's own asset observer places them at
:data:`~swe_lab.harnesses.claude_code.constants.PROXY_BINARY_AT`.

The Go source lives OUTSIDE this repo — a standalone project, not a vendored
or submoduled file. By default we look for a sibling checkout next to the repo
(``../cc-reverse-proxy/reverse_proxy.go``); set :data:`PROXY_SOURCE_ENV` to an
explicit ``reverse_proxy.go`` path to override.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

PROXY_SOURCE_ENV = "CC_REVERSE_PROXY_SRC"
_SIBLING_SOURCE = epath.Path("cc-reverse-proxy") / "reverse_proxy.go"

_BIN_SUBDIR = "bin"
_CACHE_NAMESPACE = "cc-reverse-proxy"
_BINARY_NAME = "cc-reverse-proxy"

# The sandbox is linux/amd64, so that is the only build we ever want — the host
# doing the building may be anything (a Mac laptop), and a host-native binary
# would be an "exec format error" in the container. cc-reverse-proxy imports
# nothing outside the standard library, so ``CGO_ENABLED=0`` costs nothing and
# buys a static binary that runs in an image with any libc.
_GO_ENV = {"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0"}
SANDBOX_PLATFORM = "linux-amd64"
_BUILD_TIMEOUT_S = 300.0


def proxy_source_path(repo_root: epath.PathLike | None = None) -> epath.Path:
  """Return the cc-reverse-proxy Go source path (env override, else sibling).

  Args:
    repo_root: The repo root; used only to locate the default sibling checkout.

  Returns:
    ``CC_REVERSE_PROXY_SRC`` if set, else ``<repo_root>/../cc-reverse-proxy/
    reverse_proxy.go`` (a sibling checkout of the standalone project).
  """
  override = os.environ.get(PROXY_SOURCE_ENV)
  if override:
    return epath.Path(override)
  root = epath.Path(repo_root or find_repo_root())
  return root.parent / _SIBLING_SOURCE


def proxy_source_version(repo_root: epath.PathLike | None = None) -> str:
  """Return the proxy's pinned "release": the sha256 of its Go source.

  cc-reverse-proxy is a single unversioned file in a sibling checkout, so there
  is no release string to pin — its content hash is the honest equivalent, and
  it buys the thing a version is actually *for* here: an edited source resolves
  to a different cache path, so a stale binary can never be silently reused.
  The old fixed cache path did exactly that.

  Args:
    repo_root: The repo root; used only to locate the default sibling checkout.

  Returns:
    The hex sha256 of the source file.

  Raises:
    FileNotFoundError: If the source is not where we looked, said with the two
      ways to fix it.
  """
  source = proxy_source_path(repo_root)
  if not source.is_file():
    raise FileNotFoundError(
        f"cc-reverse-proxy source not found at {source}. Clone the standalone"
        f" project beside this repo, or set {PROXY_SOURCE_ENV} to its"
        " reverse_proxy.go path."
    )
  return hashlib.sha256(source.read_bytes()).hexdigest()


def proxy_binary_path(
    version: str, *, repo_root: epath.PathLike | None = None
) -> epath.Path:
  """Return the host cache path of the built proxy binary for ``version``."""
  return (
      cache_root(repo_root or find_repo_root())
      / _BIN_SUBDIR
      / _CACHE_NAMESPACE
      / version
      / SANDBOX_PLATFORM
      / _BINARY_NAME
  )


def ensure_proxy_binary(
    *,
    dest: epath.PathLike | None = None,
    repo_root: epath.PathLike | None = None,
) -> epath.Path:
  """Ensure a linux/amd64 proxy binary exists, and return where it landed.

  Satisfies the ``Materializer`` contract the asset seam expects (see
  :mod:`swe_lab.sandbox.assets`): ``dest=None`` caches on the host and returns
  the cache path; a ``dest`` puts it exactly there. Idempotent — a build
  already in the cache for this source is reused, so declaring the asset twice
  costs nothing.

  Propagates ``FileNotFoundError`` when the Go source is not where we looked
  (from :func:`proxy_source_version`) and ``RuntimeError`` when the toolchain
  is missing or the build fails; both say how to fix themselves.

  Args:
    dest: Where the binary must end up; ``None`` for the host cache.
    repo_root: Repo root used to locate the source and the cache; discovered
      when omitted.

  Returns:
    The path of the executable binary.
  """
  root = repo_root or find_repo_root()
  version = proxy_source_version(root)
  cached = proxy_binary_path(version, repo_root=root)
  if not cached.is_file():
    _build(proxy_source_path(root), cached)
  if dest is None:
    return cached
  target = epath.Path(dest)
  target.parent.mkdir(parents=True, exist_ok=True)
  _ = cached.copy(target, overwrite=True)
  os.chmod(target, 0o755)
  return target


def _build(source: epath.Path, binary: epath.Path) -> None:
  """Cross-compile ``source`` to ``binary``, atomically.

  Built to a temporary sibling and renamed, so two runs building concurrently
  (or one that dies mid-build) can never leave a half-written file at the path
  a later run treats as a finished binary.
  """
  binary.parent.mkdir(parents=True, exist_ok=True)
  staged = binary.parent / f"{binary.name}.{os.getpid()}.tmp"
  try:
    result = subprocess.run(
        [
            "go",
            "build",
            "-ldflags=-s -w",
            "-trimpath",
            "-o",
            str(staged),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=_BUILD_TIMEOUT_S,
        env=os.environ | _GO_ENV,
    )
    if result.returncode != 0:
      raise RuntimeError(f"failed to build {source}:\n{result.stderr.strip()}")
    os.chmod(staged, 0o755)
    os.replace(staged, binary)
  except FileNotFoundError as exc:
    raise RuntimeError(
        "the Go toolchain is required to build cc-reverse-proxy for"
        " proxy capture, and `go` was not found on PATH"
    ) from exc
  except subprocess.TimeoutExpired as exc:
    raise RuntimeError(
        f"building {source} timed out after {_BUILD_TIMEOUT_S}s"
    ) from exc
  finally:
    epath.Path(staged).unlink(missing_ok=True)
