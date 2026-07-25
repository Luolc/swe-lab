"""The A-ghjob backend: the GitHub job itself is the sandbox container.

Unlike the host-orchestrated Docker backend, there is no ``docker
create/start/exec/rm``: the CI job already runs *inside* the instance's image,
so the workspace is a plain local directory and every script runs in the job's
own shell. ``up`` only places read-only assets; ``run_script`` execs
``/bin/bash`` locally with ``SANDBOX_WORKSPACE`` set. Because the manager,
observers, and every generated script reference staged files only through
``$SANDBOX_WORKSPACE`` (and the repo through ``spec.workdir``), the exact same
composition runs unchanged on either backend (spec §"Two backends, one
interface").
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
import logging
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import override

from ..backend import ExecResult, SandboxBackend, WORKSPACE_ENV
from ..errors import SandboxError
from ..mounts import Assets
from ..spec import SandboxSpec

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitHubJobBackend(SandboxBackend):
  """Run sandboxes directly in the job shell (the job *is* the container).

  There is no image pull or container lifecycle — the job already runs inside
  the instance image — so ``network`` / ``pull`` / ``platform`` (Docker
  concepts) are absent. One instance can serve one job's run; all per-run state
  is the workspace directory, recovered from the handle.

  Attributes:
    env: Variables set on each exec as ``KEY=VALUE``.
    pass_env: Names of variables inherited by reference from the job's own
      environment (e.g. a token), so a secret's value is read from the ambient
      process, never rebuilt onto a command line.
    assets: Read-only resources copied to fixed paths at ``up`` and kept
      read-only — the pinned agent binary and the like. Unlike the A-host
      bind-mount, a copy supports inline (non-file-backed) assets too.
  """

  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Sequence[str] = ()
  assets: Assets = field(default_factory=dict)

  @override
  def with_assets(self, assets: Assets) -> SandboxBackend:
    """Return a copy carrying the merged read-only assets."""
    return replace(self, assets={**self.assets, **assets})

  @override
  def up(self, spec: SandboxSpec, workspace: Path) -> str:
    """Place the read-only assets; return the workspace path as the handle.

    No container is created — the job is already the live sandbox. Each asset
    is copied to its fixed path and made read-only (a copy, not a bind-mount,
    so inline assets work too). A file-backed asset's **source permissions are
    mirrored** first, so an executable asset (the pinned agent binary) stays
    executable — matching the A-host bind-mount, which preserves the source mode
    (a plain content copy would drop the ``+x`` bit and the binary would fail
    with ``Permission denied``).

    Args:
      spec: The run context (unused here — the image is already the job).
      workspace: The local directory that is the sandbox workspace.

    Returns:
      The workspace path (as a string), used as the opaque sandbox handle.

    Raises:
      SandboxError: If an asset cannot be placed at its fixed path.
    """
    del spec
    for container_path, resource in self.assets.items():
      dest = Path(container_path)
      try:
        resource.materialize_to(dest)
        source = resource.local_path()
        if source is not None:
          shutil.copymode(source, dest)  # carry the source's execute bits
      except OSError as exc:
        raise SandboxError(
            f"could not place asset {container_path!r}: {exc}"
        ) from exc
      _make_read_only(dest)
    return str(workspace)

  @override
  def run_script(
      self,
      handle: str,
      script_name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
      stream_to: Path | None = None,
  ) -> ExecResult:
    """Run a workspace script (by name) in the job's own shell.

    Runs ``$SANDBOX_WORKSPACE/<script_name>`` — a persisted file, not stdin —
    with ``SANDBOX_WORKSPACE`` set to the local workspace, so the same script
    text runs here as on the Docker backend.

    Args:
      handle: The workspace path returned by ``up``.
      script_name: The script's workspace-relative filename.
      timeout: Seconds before the process is killed.
      env: Extra ``KEY=VALUE`` variables for this run only.
      stream_to: When set, stdout is written here as it arrives instead of
        being captured in memory.

    Returns:
      The script's exit status and output; exit code 124 on timeout.
    """
    workspace = Path(handle)
    run_env = self._exec_env(workspace, env)
    argv = ["/bin/bash", str(workspace / script_name)]
    try:
      if stream_to is not None:
        with stream_to.open("w", encoding="utf-8") as out:
          done = subprocess.run(
              argv,
              stdout=out,
              stderr=subprocess.PIPE,
              text=True,
              timeout=timeout,
              env=run_env,
              check=False,
          )
        return ExecResult(done.returncode, "", done.stderr)
      done = subprocess.run(
          argv,
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

  @override
  def down(self, handle: str) -> None:
    """No container to remove; best-effort, never raises.

    Args:
      handle: The workspace path returned by ``up`` (unused).
    """
    del handle

  def _exec_env(
      self, workspace: Path, extra: Mapping[str, str] | None
  ) -> dict[str, str]:
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
    run_env[WORKSPACE_ENV] = str(workspace)
    return run_env


def _make_read_only(path: Path) -> None:
  """Strip write bits from ``path`` (an asset is never mutated by a run)."""
  mode = path.stat().st_mode
  path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
