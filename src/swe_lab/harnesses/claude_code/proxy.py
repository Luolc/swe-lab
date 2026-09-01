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

from collections.abc import Mapping
import dataclasses
import hashlib
import os
import subprocess

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

PROXY_SOURCE_ENV = "CC_REVERSE_PROXY_SRC"
_SIBLING_SOURCE = epath.Path("cc-reverse-proxy") / "reverse_proxy.go"

_BIN_SUBDIR = "bin"
_BINARY_NAME = "cc-reverse-proxy"

SANDBOX_PLATFORM = "linux-amd64"
_BUILD_TIMEOUT_S = 300.0


@dataclasses.dataclass(frozen=True)
class ProxyBuild:
  """One compilation target for the proxy, and the cache subtree it owns.

  A target is *only* these two differences — the Go environment it compiles
  under, and the namespace it caches into — so every consumer of the proxy
  shares one builder and one cache key.

  Attributes:
    namespace: The ``<cache>/bin/<namespace>`` subtree this target owns.
      Distinct per target so two targets' builds of one source never share a
      path.
    platform: The platform component of the cache path, so a cache carried
      between machines cannot serve one platform's binary to another.
    go_env: Environment overrides for ``go build``; empty means host-native.
  """

  namespace: str
  platform: str
  go_env: Mapping[str, str]


# The sandbox is linux/amd64, so that is the only build we ever want for it —
# the host doing the building may be anything (a Mac laptop), and a host-native
# binary would be an "exec format error" in the container. cc-reverse-proxy
# imports nothing outside the standard library, so ``CGO_ENABLED=0`` costs
# nothing and buys a static binary that runs in an image with any libc.
SANDBOX_BUILD = ProxyBuild(
    namespace="cc-reverse-proxy-sandbox",
    platform=SANDBOX_PLATFORM,
    go_env={"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0"},
)

# W1's annotation pipeline runs its agent as a host subprocess, so its proxy is
# a host process and its binary must be native to whatever built it.
HOST_BUILD = ProxyBuild(
    namespace="cc-reverse-proxy-host",
    platform="host-native",
    go_env={},
)


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
    version: str,
    *,
    repo_root: epath.PathLike | None = None,
    build: ProxyBuild = SANDBOX_BUILD,
) -> epath.Path:
  """Return the host cache path of ``build``'s proxy binary for ``version``."""
  return (
      cache_root(repo_root or find_repo_root())
      / _BIN_SUBDIR
      / build.namespace
      / version
      / build.platform
      / _BINARY_NAME
  )


def ensure_proxy_binary(
    *,
    dest: epath.PathLike | None = None,
    repo_root: epath.PathLike | None = None,
    build: ProxyBuild = SANDBOX_BUILD,
) -> epath.Path:
  """Ensure ``build``'s proxy binary exists, and return where it landed.

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
    build: Which target to compile and cache for; the sandbox's cross-compiled
      build by default, :data:`HOST_BUILD` for a host-native one.

  Returns:
    The path of the executable binary.
  """
  root = repo_root or find_repo_root()
  version = proxy_source_version(root)
  cached = proxy_binary_path(version, repo_root=root, build=build)
  if not cached.is_file():
    _build(proxy_source_path(root), cached, build.go_env)
  if dest is None:
    return cached
  target = epath.Path(dest)
  target.parent.mkdir(parents=True, exist_ok=True)
  _ = cached.copy(target, overwrite=True)
  os.chmod(target, 0o755)
  return target


def _build(
    source: epath.Path, binary: epath.Path, go_env: Mapping[str, str]
) -> None:
  """Compile ``source`` to ``binary`` under ``go_env``, atomically.

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
        env=os.environ | dict(go_env),
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
