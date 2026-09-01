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

from swe_lab.harnesses.claude_code.proxy import ensure_proxy_binary, HOST_BUILD
from swe_lab.process_group import end_process_group

DEFAULT_BASE_PORT = 20000
_ANTHROPIC_API = "https://api.anthropic.com"


def build_proxy(repo_root: epath.PathLike | None = None) -> epath.Path:
  """Build (or reuse) the host-native proxy binary; return its path.

  A thin call into :func:`ensure_proxy_binary`, the one implementation of
  "compile this Go source and cache the result"; this module supplies only the
  host-native target.

  Propagates ``FileNotFoundError`` when the Go source is not where we looked
  and ``RuntimeError`` when the toolchain is missing or the build fails; both
  say how to fix themselves.

  Args:
    repo_root: Repo root used to locate the source and the cache; discovered
      when omitted.

  Returns:
    The path of the executable host-native binary. It moves whenever the
    sibling checkout's source changes, so a build of an earlier revision is
    never served as the current one.
  """
  return ensure_proxy_binary(repo_root=repo_root, build=HOST_BUILD)


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
        # The proxy is the leader of its own process group, so `__exit__` can
        # end everything it started rather than the one pid we hold.
        start_new_session=True,
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
    end_process_group(self._process)
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
