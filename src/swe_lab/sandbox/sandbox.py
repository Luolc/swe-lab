"""The sandbox: one live, lifecycle-bearing object — config + ops in one.

Per ADR-0003, ``Sandbox`` and the old ``SandboxBackend`` are **merged**: a
sandbox has a lifecycle (``up``/``down``) and internal state, and its concrete
subclasses *are* the backends (``DockerHostSandbox``, ``GitHubJobSandbox``, and
a consuming company's own). The manager drives the lifecycle on one object;
observers receive the narrow :class:`SandboxFs` view (read/write/exec, no
lifecycle). Every generated script references staged files only through the
``SANDBOX_WORKSPACE`` env var, so one script text runs on any sandbox.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from etils import epath

from .errors import SandboxError
from .mounts import Mount, Mounts
from .resources import Inline, LocalFile
from .spec import SandboxSpec

# The env var every sandbox sets on each exec: the workspace path as seen from
# inside the sandbox. Generated scripts reference staged files only through it,
# which is what makes one script text run on every sandbox.
WORKSPACE_ENV = "SANDBOX_WORKSPACE"


@dataclass(frozen=True, slots=True)
class ExecResult:
  """Outcome of one execution inside the sandbox.

  Attributes:
    exit_code: The exit code (124 on timeout, per ``timeout``'s convention).
    stdout: Captured stdout; empty when it was streamed to a file instead.
    stderr: Captured stderr.
    timed_out: Whether the execution hit its time limit.
  """

  exit_code: int
  stdout: str
  stderr: str
  timed_out: bool = False

  @property
  def ok(self) -> bool:
    """Whether it exited zero without timing out."""
    return self.exit_code == 0 and not self.timed_out


class SandboxFs(ABC):
  """The narrow view observers and graders receive: files + exec, no lifecycle.

  Withholds ``up``/``down``/``mount``/``fetch`` (the manager's ops), so an
  observer cannot bring the sandbox up or tear it down.
  """

  spec: SandboxSpec

  @abstractmethod
  def read(self, name: str) -> bytes:
    """Read a workspace file's bytes."""
    ...

  @abstractmethod
  def exists(self, name: str) -> bool:
    """Whether a workspace file exists."""
    ...

  @abstractmethod
  def write(self, name: str, data: bytes, *, executable: bool = False) -> None:
    """Write an ad-hoc workspace file (e.g. a generated script)."""
    ...

  @abstractmethod
  def run_script(
      self,
      name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run a staged workspace file by name (``<shell> $WORKSPACE/name``).

    A persisted file, not stdin — the exact script survives for audit.

    Args:
      name: The script's workspace-relative filename.
      timeout: Seconds before the execution is killed.
      env: Extra variables set for this execution only.

    Returns:
      The exit status and output.
    """
    ...

  @abstractmethod
  def run_command(
      self,
      command: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run an inline command string (``<shell> -c command``).

    For short / diagnostic commands that need no persisted script.

    Args:
      command: The shell command to run.
      timeout: Seconds before the execution is killed.
      env: Extra variables set for this execution only.

    Returns:
      The exit status and output.
    """
    ...


class Sandbox(SandboxFs, ABC):
  """A live sandbox: config + lifecycle + ops. Subclasses are the backends.

  A behavior interface (ABC, per ADR-0002). ``up`` must clean up its own partial
  state on failure; ``down`` never raises — teardown failure is logged, never
  propagated, so it can never mask the run's real error.
  """

  @abstractmethod
  def up(self) -> None:
    """Provision the sandbox (subsumes the old ``_prepare_workspace``)."""
    ...

  @abstractmethod
  def down(self) -> None:
    """Tear the sandbox down, best-effort; never raises."""
    ...

  @abstractmethod
  def fetch(self, name: str, dest: epath.PathLike) -> None:
    """Dump one produced artifact to a host path (the collect step, ADR-0003).

    A host sandbox copies from its own dir (near-zero); a remote downloads.

    Args:
      name: The artifact's workspace-relative filename.
      dest: The host path to write it to (parents created).
    """
    ...

  def mount(self, mounts: Mounts) -> None:
    """Stage the declared mounts into the live sandbox (after ``up``).

    Args:
      mounts: The merged staging set (target path → mount).
    """
    for target, mount in mounts.items():
      self._mount_one(target, mount)

  def _mount_one(self, target: str, mount: Mount) -> None:
    """Transfer one mount's resource into the sandbox by its kind.

    A subclass adds its own ``Resource`` kinds here and calls ``super()`` for
    the built-ins (import-only extension).

    Args:
      target: The in-sandbox path to place the resource at.
      mount: The mount (resource + executable / read_only flags).

    Raises:
      SandboxError: If the resource kind is not handled by this sandbox.
    """
    resource = mount.resource
    if isinstance(resource, Inline):
      self._put_bytes(target, resource.content, mount)
    elif isinstance(resource, LocalFile):
      self._put_file(target, resource.path, mount)
    else:
      raise SandboxError(
          f"{type(self).__name__} cannot mount {type(resource).__name__}"
      )

  @abstractmethod
  def _put_bytes(self, target: str, data: bytes, mount: Mount) -> None:
    """Place inline bytes at ``target`` (honoring executable / read_only)."""
    ...

  @abstractmethod
  def _put_file(self, target: str, src: epath.PathLike, mount: Mount) -> None:
    """Place a host file at ``target`` (honoring executable / read_only)."""
    ...
