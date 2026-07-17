"""General Docker execution: pull an image, run a script inside a container."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess

# Images are linux/amd64; on Apple Silicon this runs under emulation.
DEFAULT_PLATFORM = "linux/amd64"
DEFAULT_TIMEOUT_S = 1800.0
_PULL_TIMEOUT_S = 3600.0


class DockerError(RuntimeError):
  """A ``docker`` CLI invocation failed to start or errored fatally."""


@dataclass(frozen=True)
class Mount:
  """One extra bind mount for a container run (host path → container path)."""

  host: Path
  container: str
  read_only: bool = False

  def as_arg(self) -> str:
    spec = f"{self.host}:{self.container}"
    return f"{spec}:ro" if self.read_only else spec


@dataclass(frozen=True)
class ContainerRun:
  """Result of one ``docker run`` (the container's own stdout/stderr)."""

  exit_code: int
  stdout: str
  stderr: str
  timed_out: bool = False

  @property
  def ok(self) -> bool:
    return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True)
class DockerProvider:
  """Thin wrapper over the ``docker`` CLI: pull, run a script in a container."""

  platform: str = DEFAULT_PLATFORM

  def pull(self, image_ref: str, *, timeout: float = _PULL_TIMEOUT_S) -> None:
    run = self._docker(
        ["pull", "--platform", self.platform, image_ref], timeout=timeout
    )
    if not run.ok:
      raise DockerError(
          f"docker pull {image_ref} failed:\n{run.stderr[-2000:]}"
      )

  def run_script(
      self,
      image_ref: str,
      workspace: Path,
      script_name: str,
      *,
      mount_at: str = "/workspace",
      timeout: float = DEFAULT_TIMEOUT_S,
      network: bool = True,
      shell: str = "/bin/bash",
      extra_mounts: Sequence[Mount] | None = None,
      env: Mapping[str, str] | None = None,
      pass_env: Sequence[str] | None = None,
  ) -> ContainerRun:
    """Bind-mount ``workspace`` at ``mount_at`` and run ``script_name`` in it.

    The image's entrypoint is overridden with ``shell`` so the script runs
    regardless of what the image's default entrypoint is.

    ``extra_mounts`` bind-mounts additional host paths (e.g. rollout's pinned
    Claude Code binary). ``env`` sets explicit ``KEY=VALUE`` variables in the
    container. ``pass_env`` names variables to **inherit** from this process's
    environment (``docker run -e NAME`` with no value) — use it for secrets like
    ``CLAUDE_CODE_OAUTH_TOKEN`` so the value is passed by reference and never
    appears in the ``docker`` argv (and thus not in ``ps`` or logs).
    """
    args = ["run", "--rm", "--platform", self.platform]
    if not network:
      args += ["--network", "none"]
    args += ["-v", f"{workspace}:{mount_at}"]
    for mount in extra_mounts or ():
      args += ["-v", mount.as_arg()]
    for key, value in (env or {}).items():
      args += ["-e", f"{key}={value}"]
    for key in pass_env or ():
      args += ["-e", key]
    args += [
        "--entrypoint",
        shell,
        image_ref,
        f"{mount_at}/{script_name}",
    ]
    return self._docker(args, timeout=timeout)

  def remove_image(self, image_ref: str, *, timeout: float = 120.0) -> None:
    """Best-effort ``docker rmi`` to reclaim disk (e.g. between eval runs).

    Never raises on a normal failure (image absent or still in use): pruning is
    an optimization, not a correctness step.
    """
    _ = self._docker(["rmi", "-f", image_ref], timeout=timeout)

  def _docker(self, args: list[str], *, timeout: float) -> ContainerRun:
    try:
      result = subprocess.run(
          ["docker", *args],
          capture_output=True,
          text=True,
          timeout=timeout,
          check=False,
      )
    except subprocess.TimeoutExpired as exc:
      out = exc.stdout if isinstance(exc.stdout, str) else ""
      err = exc.stderr if isinstance(exc.stderr, str) else ""
      return ContainerRun(124, out, err, timed_out=True)
    except FileNotFoundError as exc:
      raise DockerError("docker CLI not found on PATH") from exc
    return ContainerRun(result.returncode, result.stdout, result.stderr)
