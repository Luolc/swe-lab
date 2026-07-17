"""Build and run one ``cc-reverse-proxy`` per agent call.

Every headless Claude Code invocation (annotation or aggregation) gets its own
proxy on a distinct port so concurrent/interleaved runs never collide, and each
proxy logs to a per-run path so logs never overwrite each other. The proxy
records every request/response pair — used later to extract the final exchange
and the session-success (``complete``) flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import socket
import subprocess
import time
from types import TracebackType

from ..paths import cache_root, find_repo_root

DEFAULT_BASE_PORT = 20000
_ANTHROPIC_API = "https://api.anthropic.com"
_PROXY_SOURCE = "submodules/cc-reverse-proxy/reverse_proxy.go"


def proxy_binary_path(repo_root: Path | None = None) -> Path:
  return cache_root(repo_root) / "bin" / "cc-reverse-proxy"


def build_proxy(repo_root: Path | None = None, *, force: bool = False) -> Path:
  """Compile the proxy binary into the cache if missing; return its path."""
  root = repo_root or find_repo_root()
  binary = proxy_binary_path(root)
  source = root / _PROXY_SOURCE
  if binary.is_file() and not force:
    return binary
  if not source.is_file():
    raise FileNotFoundError(
        f"Proxy source not found at {source}. Did you init the git submodule?"
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
  """A running proxy instance, managed as a context manager."""

  port: int
  output_path: Path
  binary: Path
  target: str = _ANTHROPIC_API
  startup_timeout_s: float = 15.0

  _process: subprocess.Popen[bytes] | None = None

  @property
  def base_url(self) -> str:
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
