"""Run one host-side ``cc-reverse-proxy`` per agent call (W1 annotation only).

This is the **host** proxy, and it stays host-side because the thing it records
is host-side too: the annotation pipeline runs its headless agent as a plain
subprocess in a checkout on this machine, not in a sandbox. Agent and proxy
share one loopback interface, so there is nothing to move and no container
boundary to cross.

The engine's rollout path is the opposite case and no longer lives here: its
agent runs in a container, so its proxy runs in that container too (see
:mod:`swe_lab.harnesses.claude_code.proxy`). These functions moved out of the
harness package when that happened, because a harness that never starts a
process has no business owning process machinery.

Every call gets its own port so concurrent/interleaved runs never collide, and
each proxy logs to a per-run path so logs never overwrite each other. The proxy
records every request/response pair — used later to extract the final exchange
and the session-success (``complete``) flag.
"""

from __future__ import annotations

from dataclasses import dataclass
import socket
import subprocess
import time
from types import TracebackType

from etils import epath

from swe_lab.harnesses.claude_code.proxy import (
    PROXY_SOURCE_ENV,
    proxy_source_path,
)
from swe_lab.paths import cache_root, find_repo_root

DEFAULT_BASE_PORT = 20000
_ANTHROPIC_API = "https://api.anthropic.com"


def proxy_binary_path(repo_root: epath.PathLike | None = None) -> epath.Path:
  """Return the cache path of the host-native ``cc-reverse-proxy`` binary."""
  return cache_root(repo_root) / "bin" / "cc-reverse-proxy"


def build_proxy(
    repo_root: epath.PathLike | None = None, *, force: bool = False
) -> epath.Path:
  """Compile the proxy binary into the cache if missing; return its path.

  Host-native (no ``GOOS``/``GOARCH``), because this proxy runs on the machine
  that builds it.
  """
  root = repo_root or find_repo_root()
  binary = proxy_binary_path(root)
  source = proxy_source_path(root)
  # A *directory* here is residue from the in-sandbox proxy cache, which for a
  # while nested its versioned tree under this exact path. This function only
  # ever writes a file here, and nothing else in the repo names this path, so a
  # directory can only have that one origin -- and it is a regenerable build
  # cache, not anyone's working file.
  #
  # It has to go before the build rather than being reported, because `go build
  # -o <dir>` does not fail: it writes *into* the directory and reports success,
  # so this function would hand back a path that is still a directory and the
  # error would surface much later, as PermissionError, when the proxy is
  # spawned.
  # `missing_ok` because two pipeline runs can reach this together: both see
  # the directory, one removes it, and the loser must not fail for having been
  # beaten to a result it wanted. What is asserted on the next line is the
  # state, not who achieved it.
  if binary.is_dir():
    binary.rmtree(missing_ok=True)
  if binary.is_file() and not force:
    return binary
  if not source.is_file():
    raise FileNotFoundError(
        f"cc-reverse-proxy source not found at {source}. Clone the standalone"
        f" project beside this repo, or set {PROXY_SOURCE_ENV} to its"
        " reverse_proxy.go path."
    )
  binary.parent.mkdir(parents=True, exist_ok=True)
  result = subprocess.run(
      ["go", "build", "-o", str(binary), str(source)],
      capture_output=True,
      text=True,
      check=False,
  )
  if result.returncode != 0:
    raise RuntimeError(f"Failed to build proxy:\n{result.stderr.strip()}")
  return binary


def port_for_index(index: int, *, base_port: int = DEFAULT_BASE_PORT) -> int:
  """Derive an instance's proxy port from its stable dataset index."""
  return base_port + index


@dataclass
class ReverseProxy:
  """A running ``cc-reverse-proxy`` process, managed as a context manager.

  Entering the context starts the proxy and blocks until it accepts
  connections; exiting terminates the process (killing it if it does not
  stop promptly).

  Attributes:
    port: Local port the proxy listens on.
    output_path: File the proxy appends request/response records to.
    binary: Path of the built proxy executable.
    target: Upstream API base URL requests are forwarded to.
    startup_timeout_s: Seconds to wait for the proxy to start listening.
  """

  port: int
  output_path: epath.Path
  binary: epath.Path
  target: str = _ANTHROPIC_API
  startup_timeout_s: float = 15.0

  _process: subprocess.Popen[bytes] | None = None

  @property
  def base_url(self) -> str:
    """The local URL agent calls should use as their API base."""
    return f"http://127.0.0.1:{self.port}"

  def __enter__(self) -> ReverseProxy:
    self.output_path.parent.mkdir(parents=True, exist_ok=True)
    self._process = subprocess.Popen(
        [
            str(self.binary),
            "--port",
            str(self.port),
            "--target",
            self.target,
            "--output",
            str(self.output_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    self._wait_until_listening()
    return self

  def __exit__(
      self,
      exc_type: type[BaseException] | None,
      exc: BaseException | None,
      tb: TracebackType | None,
  ) -> None:
    if self._process is None:
      return
    self._process.terminate()
    try:
      _ = self._process.wait(timeout=5)
    except subprocess.TimeoutExpired:
      self._process.kill()
      _ = self._process.wait(timeout=5)
    self._process = None

  def _wait_until_listening(self) -> None:
    deadline = time.monotonic() + self.startup_timeout_s
    while time.monotonic() < deadline:
      if self._process is not None and self._process.poll() is not None:
        raise RuntimeError(
            f"Proxy exited early (code {self._process.returncode}) on port"
            f" {self.port}."
        )
      try:
        with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
          return
      except OSError:
        time.sleep(0.1)
    raise TimeoutError(
        f"Proxy did not start listening on port {self.port} within"
        f" {self.startup_timeout_s}s."
    )
