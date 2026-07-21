"""A sandbox backend that drives Docker from the host.

The host runs ``docker create`` → ``start`` → one or more ``exec`` → ``rm``
against one persistent container per sandbox, with the workspace directory
bind-mounted in. A persistent container (rather than one throwaway
``docker run`` per action) is what lets setup, the main action, and any
on-error probe run as separate steps against the *same* live container.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path
import subprocess

from ..backend import ExecResult, WORKSPACE_ENV
from ..errors import SandboxError
from ..spec import SandboxSpec

_logger = logging.getLogger(__name__)

# The instance images are linux/amd64; on Apple Silicon they run emulated.
DEFAULT_PLATFORM = "linux/amd64"
# Pulls can be slow on a cold runner; create/start/rm are quick.
_PULL_TIMEOUT_S = 3600.0
_DOCKER_TIMEOUT_S = 120.0

# Every container this backend creates carries these labels, so a container
# that ever escapes teardown is findable and removable in one command:
#   docker rm -f $(docker ps -aq --filter label=swe-lab)
_OWNER_LABEL = "swe-lab"
_INSTANCE_LABEL = "swe-lab-instance"

# The keep-alive the container runs so it stays up between execs. bash is the
# one interpreter the instance images are guaranteed to have (their test
# harness is bash), whereas a bare `sleep`/`tail` entrypoint binary is not
# guaranteed on a minimal image — so we invoke it through bash.
_KEEP_ALIVE = ("--entrypoint", "/bin/bash")
_KEEP_ALIVE_CMD = ("-c", "sleep infinity")


@dataclass(frozen=True)
class DockerHostBackend:
  """Run sandboxes as persistent Docker containers driven from the host.

  One backend instance can serve many sandboxes; all per-sandbox state lives
  in the opaque handle it returns (the container id), never on the backend,
  so the backend is safe to share and is frozen.

  The network/env settings are construction-time because they are properties
  of the composition using the backend: a solving run needs the network and a
  credential, a grading run needs neither.

  Attributes:
    platform: The ``--platform`` value for pull and create.
    network: Whether the container gets network access (``--network none``
      when False).
    pull: Whether to pull the image before creating the container.
    mount_at: The path the workspace is bind-mounted to inside the container;
      also the value of the ``SANDBOX_WORKSPACE`` variable every exec sees.
    env: Variables set in the container as ``KEY=VALUE`` at create time.
    pass_env: Names of variables inherited by reference from the host process
      (``-e NAME`` with no value), so a secret's value never appears in the
      ``docker`` command line, process list, or logs.
  """

  platform: str = DEFAULT_PLATFORM
  network: bool = True
  pull: bool = True
  mount_at: str = "/workspace"
  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Sequence[str] = ()

  def up(self, spec: SandboxSpec, workspace: Path) -> str:
    """Pull, create, and start the container; return its id.

    Cleans up its own partial state: if ``start`` fails after ``create``
    succeeded, the created container is removed before raising.

    Args:
      spec: The run context (image and instance id) to realize.
      workspace: The host directory bind-mounted into the container.

    Returns:
      The container id, used as the sandbox handle.

    Raises:
      SandboxError: If the Docker CLI is missing, or pull/create/start fail.
    """
    if self.pull:
      self._pull(spec.image_ref)
    create_args = ["create", "--platform", self.platform]
    if not self.network:
      create_args += ["--network", "none"]
    create_args += ["-v", f"{workspace}:{self.mount_at}"]
    for key, value in self.env.items():
      create_args += ["-e", f"{key}={value}"]
    for key in self.pass_env:
      create_args += ["-e", key]
    create_args += [
        "--label",
        f"{_OWNER_LABEL}=1",
        "--label",
        f"{_INSTANCE_LABEL}={spec.instance_id}",
        *_KEEP_ALIVE,
        spec.image_ref,
        *_KEEP_ALIVE_CMD,
    ]
    created = self._docker(create_args, timeout=_DOCKER_TIMEOUT_S)
    if created.returncode != 0:
      raise SandboxError(
          f"docker create for {spec.image_ref} failed:\n"
          f"{created.stderr[-2000:]}"
      )
    handle = created.stdout.strip()
    started = self._docker(["start", handle], timeout=_DOCKER_TIMEOUT_S)
    if started.returncode != 0:
      self.down(handle)  # remove our own partial state before surfacing
      raise SandboxError(
          f"docker start for {spec.image_ref} failed:\n"
          f"{started.stderr[-2000:]}"
      )
    return handle

  def run_script(
      self,
      handle: str,
      script_name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
      stream_to: Path | None = None,
  ) -> ExecResult:
    """Run a workspace script (by name) inside the live container.

    Runs the file at ``$SANDBOX_WORKSPACE/<script_name>`` (a mount, or one an
    observer wrote there) with ``SANDBOX_WORKSPACE`` set — a persisted file on
    disk, not stdin, so the exact script survives for audit and scripts
    reference workspace files only through the variable, never a hardcoded
    path.

    Args:
      handle: The container id returned by ``up``.
      script_name: The script's workspace-relative filename.
      timeout: Seconds before the exec process is killed (the container
        stays up so later runs remain possible).
      env: Extra ``KEY=VALUE`` variables for this run only.
      stream_to: When set, stdout is written here as it arrives instead of
        being captured in memory.

    Returns:
      The script's exit status and output; exit code 124 on timeout.

    Raises:
      SandboxError: If the Docker CLI is missing.
    """
    args = ["exec", "-e", f"{WORKSPACE_ENV}={self.mount_at}"]
    for key, value in (env or {}).items():
      args += ["-e", f"{key}={value}"]
    args += [handle, "/bin/bash", f"{self.mount_at}/{script_name}"]
    try:
      if stream_to is not None:
        with stream_to.open("w", encoding="utf-8") as out:
          done = subprocess.run(
              ["docker", *args],
              stdout=out,
              stderr=subprocess.PIPE,
              text=True,
              timeout=timeout,
              check=False,
          )
        return ExecResult(done.returncode, "", done.stderr)
      done = subprocess.run(
          ["docker", *args],
          capture_output=True,
          text=True,
          timeout=timeout,
          check=False,
      )
      return ExecResult(done.returncode, done.stdout, done.stderr)
    except subprocess.TimeoutExpired as exc:
      err = exc.stderr if isinstance(exc.stderr, str) else ""
      return ExecResult(124, "", err, timed_out=True)
    except FileNotFoundError as exc:
      raise SandboxError("docker CLI not found on PATH") from exc

  def down(self, handle: str) -> None:
    """Remove the container, best-effort; never raises.

    Args:
      handle: The container id returned by ``up``.
    """
    try:
      removed = self._docker(["rm", "-f", handle], timeout=_DOCKER_TIMEOUT_S)
    except SandboxError as exc:
      _logger.warning("docker teardown failed (swallowed): %s", exc)
      return
    if removed.returncode != 0:
      _logger.warning(
          "docker teardown failed (swallowed): %s", removed.stderr.strip()
      )

  def _pull(self, image_ref: str) -> None:
    pulled = self._docker(
        ["pull", "--platform", self.platform, image_ref],
        timeout=_PULL_TIMEOUT_S,
    )
    if pulled.returncode != 0:
      raise SandboxError(
          f"docker pull {image_ref} failed:\n{pulled.stderr[-2000:]}"
      )

  def _docker(
      self, args: list[str], *, timeout: float
  ) -> subprocess.CompletedProcess[str]:
    """Run one ``docker`` subcommand, capturing output.

    Args:
      args: The ``docker`` arguments (without the leading ``docker``).
      timeout: Seconds before the command is killed.

    Returns:
      The completed process.

    Raises:
      SandboxError: If the Docker CLI is missing or the command times out.
    """
    try:
      return subprocess.run(
          ["docker", *args],
          capture_output=True,
          text=True,
          timeout=timeout,
          check=False,
      )
    except FileNotFoundError as exc:
      raise SandboxError("docker CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
      raise SandboxError(
          f"docker {args[0]} timed out after {timeout}s"
      ) from exc
