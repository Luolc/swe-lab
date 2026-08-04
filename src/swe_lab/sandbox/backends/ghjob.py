"""The A-ghjob sandbox: the GitHub job itself is the container.

Unlike the host-orchestrated Docker sandbox, there is no ``docker
create/start/exec/rm``: the CI job already runs *inside* the instance's image,
so the workspace is a plain local directory and every script runs in the job's
own shell. ``up`` only prepares the workspace; ``mount`` writes files locally
(and makes read-only mounts read-only); ``run_script`` execs the shell locally
with ``SANDBOX_WORKSPACE`` set. Because the manager, observers, and every
generated script reference staged files only through ``$SANDBOX_WORKSPACE`` (and
the repo through ``spec.workdir``), the exact same composition runs unchanged on
either backend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
import os
import subprocess
from typing import override

from etils import epath

from ..errors import SandboxError
from ..mounts import Mount
from ..observer import SandboxObserver
from ..sandbox import ExecResult, Sandbox, SandboxFs, WORKSPACE_ENV
from ..spec import SandboxSpec

_logger = logging.getLogger(__name__)


@dataclass
class GitHubJobSandbox(Sandbox):
  """Run the sandbox directly in the job shell (the job *is* the container).

  There is no image pull or container lifecycle — the job already runs inside
  the instance image — so ``network`` / ``pull`` / ``platform`` (Docker
  concepts) are absent. Single-run; construct a fresh one per job.

  Attributes:
    spec: The run context (the image is already the job).
    workspace: The local directory that is the sandbox workspace.
    shell: The interpreter each ``run_script`` / ``run_command`` uses (default
      ``/bin/bash``; set to ``/bin/sh`` for an image without bash).
    env: Variables set on each exec as ``KEY=VALUE``.
    pass_env: Names of variables inherited by reference from the job's own
      environment (e.g. a token), so a secret's value is read from the ambient
      process, never rebuilt onto a command line.
    reuse: Allow ``up`` to run in a non-empty workspace.
  """

  spec: SandboxSpec
  workspace: epath.Path
  shell: str = "/bin/bash"
  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Sequence[str] = ()
  reuse: bool = False

  # --- lifecycle -----------------------------------------------------------

  @override
  def up(self) -> None:
    """Prepare the workspace; there is no container to create.

    Raises:
      SandboxError: On a non-empty workspace (without ``reuse``).
    """
    self.workspace.mkdir(parents=True, exist_ok=True)
    if not self.reuse and any(self.workspace.iterdir()):
      raise SandboxError(
          f"workspace {self.workspace} is not empty; pass reuse=True to run "
          "in it anyway"
      )

  @override
  def down(self) -> None:
    """No container to remove; best-effort, never raises."""

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Contribute this backend's own observers (ADR-0007 §3, backend source).

    Just the agent binary: there is no container lifecycle to measure here (the
    job *is* the container, and whoever started it owns its metrics).
    """
    return (GitHubJobClaudeCodeBinaryObserver(),)

  @override
  def fetch(self, name: str, dest: epath.PathLike) -> None:
    """Copy a produced workspace file out to a host path.

    Args:
      name: The artifact's workspace-relative filename.
      dest: The host path to write it to (parents created).
    """
    src = self.workspace / name
    dest = epath.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
      _ = epath.Path(src).copy(dest, overwrite=True)

  # --- files (SandboxFs) ---------------------------------------------------

  @override
  def read(self, name: str) -> bytes:
    """Read a workspace file's bytes."""
    return (self.workspace / name).read_bytes()

  @override
  def exists(self, name: str) -> bool:
    """Whether a workspace file exists."""
    return (self.workspace / name).is_file()

  @override
  def write(self, name: str, data: bytes, *, executable: bool = False) -> None:
    """Write an ad-hoc workspace file."""
    dest = self.workspace / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(data)
    if executable:
      os.chmod(dest, 0o755)

  # --- mounts (transfer) ---------------------------------------------------

  @override
  def _put_bytes(self, target: str, data: bytes, mount: Mount) -> None:
    dest = self._dest(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(data)
    os.chmod(dest, mount.mode)

  @override
  def _put_file(self, target: str, src: epath.PathLike, mount: Mount) -> None:
    dest = self._dest(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = epath.Path(src).copy(dest, overwrite=True)
    os.chmod(dest, mount.mode)

  def _dest(self, target: str) -> epath.Path:
    """Resolve a mount target: absolute as-is, else workspace-relative."""
    return (
        epath.Path(target)
        if target.startswith("/")
        else self.workspace / target
    )

  # --- exec ----------------------------------------------------------------

  @override
  def run_script(
      self,
      name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run ``$SANDBOX_WORKSPACE/<name>`` under the shell in the job.

    A persisted file, not stdin, so the same script text runs here as on the
    Docker backend.

    Args:
      name: The script's workspace-relative filename.
      timeout: Seconds before the process is killed.
      env: Extra ``KEY=VALUE`` variables for this run only.

    Returns:
      The script's exit status and output; exit code 124 on timeout.
    """
    return self._run(
        [self.shell, str(self.workspace / name)],
        timeout=timeout,
        env=env,
    )

  @override
  def run_command(
      self,
      command: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run an inline command (``<shell> -c command``) in the job."""
    return self._run(
        [self.shell, "-c", command],
        timeout=timeout,
        env=env,
    )

  def _run(
      self,
      argv: Sequence[str],
      *,
      timeout: float,
      env: Mapping[str, str] | None,
  ) -> ExecResult:
    run_env = self._exec_env(env)
    try:
      done = subprocess.run(
          list(argv),
          capture_output=True,
          text=True,
          timeout=timeout,
          env=run_env,
          check=False,
      )
      return ExecResult(done.returncode, done.stdout, done.stderr)
    except subprocess.TimeoutExpired as exc:
      err = exc.stderr if isinstance(exc.stderr, str) else ""
      return ExecResult(124, "", err, timed_out=True)

  def _exec_env(self, extra: Mapping[str, str] | None) -> dict[str, str]:
    """Build the exec environment: inherit the job's, then layer our own."""
    run_env = dict(os.environ)
    for key in self.pass_env:
      value = os.environ.get(key)
      if value is None:
        _logger.warning("pass_env variable %s is not set in the job", key)
      else:
        run_env[key] = value
    run_env.update(self.env)
    run_env.update(extra or {})
    run_env[WORKSPACE_ENV] = str(self.workspace)
    return run_env


class GitHubJobClaudeCodeBinaryObserver(SandboxObserver):
  """Download the pinned Claude Code binary in the job, to its final path.

  Here the sandbox filesystem *is* the job's own, so there is nobody to be
  handed a copy by: the job has the network, and the shortest path to a
  runnable agent is to fetch it straight to ``BINARY_AT``. That is the case a
  mount cannot express — a mount hands bytes over, and the whole point here is
  that no bytes should travel (compare
  :class:`~swe_lab.sandbox.backends.host.HostClaudeCodeBinaryObserver`, which
  does hand a container a copy, because a container has no other way).

  The download is in-process rather than a shelled-out ``curl``: the job runs
  inside an arbitrary instance image and plenty of them ship no ``curl``, while
  the interpreter running this is guaranteed to be there.

  Idempotent, because ``ensure_claude_binary`` is: a job that already installed
  the pinned binary — a workflow step, a warmed image — only re-verifies it
  against the release checksum instead of downloading it again.
  """

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Fetch the binary to ``BINARY_AT`` before anything runs against it.

    Args:
      sb: Unused — this writes to the job's own filesystem, which *is* the
        filesystem the sandbox reads and execs in.
    """
    del sb
    # Imported here, not at module scope: the harness package imports this one
    # (a harness is written against the sandbox engine), so a top-level import
    # would close the cycle. This one reach upward is the point of the class.
    from swe_lab.harnesses.claude_code.binary import ensure_claude_binary
    from swe_lab.harnesses.claude_code.constants import BINARY_AT

    _ = ensure_claude_binary(dest=BINARY_AT)
