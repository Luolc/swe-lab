"""Record the agent's API traffic for the length of one run.

``PROXY`` capture routes the agent through a host-side recording proxy and
reads the recording back as the trace. That is a **lifecycle** concern, not
something to wrap around the main action: the proxy has to be listening before
the agent starts and closed before anything reads its log, which is exactly
what an observer's hooks are for.

Doing it as an observer buys three things the old context-manager field could
not: the harness composes it itself (so nothing above knows a proxy exists),
a fresh one exists per execution (so a task stays a re-executable declaration),
and the recording is handed to the run *through the sandbox*, so it needs no
host path and works on a backend with no bind mount at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import tempfile
from typing import override

from etils import epath

from swe_lab.sandbox import Contribution, SandboxFs, SandboxObserver

from .constants import PROXY_LOG_NAME
from .proxy import build_proxy, ReverseProxy


@dataclass
class ProxyRecorder(SandboxObserver):
  """Run a recording proxy for the run's lifetime; land its log in the run.

  Single-run, like every stateful observer: the harness builds a fresh one per
  execution.

  Attributes:
    port: Host port the proxy listens on. Two runs on one host must not share
      it — a sweep gives each worker its own
      (``--rollout.harness.proxy_port=…``).
    log_name: The workspace file the recording is written to before teardown;
      the harness's own converter reads it from there.
    repo_root: Where the proxy binary is built from; ``None`` uses the
      packaged default.
  """

  port: int
  log_name: str = PROXY_LOG_NAME
  repo_root: epath.PathLike | None = None
  _proxy: ReverseProxy | None = field(default=None, init=False, repr=False)
  _log: epath.Path | None = field(default=None, init=False, repr=False)

  @override
  def before_create(self, sb: SandboxFs) -> None:
    """Start the proxy before anything can call the API through it.

    Args:
      sb: Unused — the proxy is a host-side process, not a sandbox one.
    """
    del sb
    self._log = epath.Path(tempfile.mkdtemp(prefix="swe-lab-proxy-")) / "log"
    # Built here rather than at composition: `observers()` is also called to
    # read output schemas, and reading a schema must not compile anything.
    self._proxy = ReverseProxy(
        self.port, self._log, build_proxy(self.repo_root)
    )
    _ = self._proxy.__enter__()

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Stop the proxy, then land its recording in the workspace.

    Ordering is the whole point of doing this here: the harness composes this
    observer *first*, so the recording is complete and in place by the time
    the trace converter (a later observer) reads it.

    Args:
      sb: The still-live sandbox the recording is written into.

    Returns:
      Nothing: the log is now an ordinary workspace file, and the harness's
      own outcome observer registers it as the artifact it declared.
    """
    if self._proxy is not None:
      _ = self._proxy.__exit__(None, None, None)
      self._proxy = None
    if self._log is not None and self._log.is_file():
      sb.write(self.log_name, self._log.read_bytes())
    return None
