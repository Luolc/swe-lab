"""The backend seam: one way to realize a live sandbox.

Two implementations are planned. A **host-orchestrated Docker backend**: the
host drives ``docker create/start/exec/rm`` and the workspace directory is
bind-mounted into the container. A **GitHub-Actions container-job backend**:
the CI job itself runs inside the instance's image, so the workspace is just
a local directory and ``exec`` runs in the job's own shell.

The manager and observers are backend-agnostic. Because the workspace lands
at a different in-sandbox path per backend (a fixed mount point vs. an
arbitrary runner directory), generated scripts never hardcode it — they
reference workspace files only through the ``SANDBOX_WORKSPACE`` environment
variable, which every backend sets on each exec. That is the whole contract
that lets one script text run unchanged on any backend (the same pattern as
GitHub Actions' own ``GITHUB_WORKSPACE``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .spec import SandboxSpec

# The env var every backend sets on each exec: the workspace's path as seen
# from inside the sandbox. Generated scripts reference staged files only
# through it, which is what makes one script text run on every backend.
WORKSPACE_ENV = "SANDBOX_WORKSPACE"


@dataclass(frozen=True, slots=True)
class ExecResult:
  """Outcome of one script execution inside the sandbox.

  Attributes:
    exit_code: The script's exit code (124 on timeout, matching ``timeout``'s
      convention).
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
    """Whether the script exited zero without timing out."""
    return self.exit_code == 0 and not self.timed_out


class SandboxBackend(Protocol):
  """Realize, drive, and tear down one live sandbox.

  Contract notes: ``up`` must clean up its own partial state when it fails
  (the manager has no handle to pass to ``down`` in that case), and ``down``
  never raises — teardown failure is logged, not propagated, so it can never
  mask the run's real error.
  """

  def up(self, spec: SandboxSpec, workspace: Path) -> str:
    """Bring the sandbox up and return an opaque handle to it."""
    ...

  def run_script(
      self,
      handle: str,
      script_name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
      stream_to: Path | None = None,
  ) -> ExecResult:
    """Run a workspace script (by name) inside the live sandbox.

    The script is a file already materialized in the workspace (a mount, or
    written there by an observer); the backend runs it by its in-sandbox path
    with ``SANDBOX_WORKSPACE`` set. Running a persisted file (rather than
    piping text on stdin) keeps the exact script on disk for audit and needs no
    stdin plumbing on a job-as-container backend.

    Args:
      handle: The handle ``up`` returned.
      script_name: The script's workspace-relative filename.
      timeout: Seconds before the execution is killed.
      env: Extra variables set for this execution only.
      stream_to: Stream stdout to this host file instead of capturing it
        in memory. Load-bearing for long agent runs: buffering hours of
        agent output in memory has caused runs to swap-thrash and hang.

    Returns:
      The script's exit status and output.
    """
    ...

  def down(self, handle: str) -> None:
    """Tear the sandbox down, best-effort; never raises."""
    ...
