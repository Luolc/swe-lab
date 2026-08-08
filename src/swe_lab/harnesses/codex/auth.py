"""Supply a Codex login to the sandbox, as a file rather than an env var.

Codex authenticates one of two ways, and only one of them fits the usual
"pass the secret by name" pattern:

- an **API key** in ``OPENAI_API_KEY`` — an env var, so the sandbox's
  ``pass_env`` already carries it by reference and nothing here is needed;
- a **ChatGPT login**, which lives in ``auth.json`` under ``CODEX_HOME``. That
  is a *file*, so it has to be staged into the sandbox.

This observer does the second, and does it as a **mount** so the bytes never
reach a command line, never land in the workspace (where the run's own
artifacts are collected from and persisted), and go away with the container.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from etils import epath

from swe_lab.sandbox import (
    LocalFile,
    Mount,
    Mounts,
    SandboxError,
    SandboxObserver,
)

from .constants import AGENT_HOME, AUTH_FILENAME


@dataclass(frozen=True)
class CodexAuthObserver(SandboxObserver):
  """Stage a host ``auth.json`` into the sandbox's ``CODEX_HOME``.

  Deliberately **not** read-only: Codex refreshes its access token and writes
  the file back, and a read-only mount would turn an expired token into an
  opaque mid-run failure. The container gets a copy, so the refreshed
  credential is discarded with it and the host file is untouched.

  Attributes:
    auth_file: Host path of the ``auth.json`` to stage. Resolved eagerly at
      construction so a typo fails before a container is paid for.
    codex_home: The in-sandbox ``CODEX_HOME`` this lands under; must match the
      harness's own ``codex_home``.
  """

  auth_file: epath.PathLike
  codex_home: str = AGENT_HOME

  def __post_init__(self) -> None:
    """Refuse a credential path that is not there.

    Raises:
      SandboxError: If ``auth_file`` does not exist — an absent login would
        otherwise surface as an authentication failure inside the container,
        minutes later and with a much worse message.
    """
    if not epath.Path(self.auth_file).is_file():
      raise SandboxError(
          f"codex auth file not found: {self.auth_file}; a ChatGPT login lives"
          " in <CODEX_HOME>/auth.json, or use OPENAI_API_KEY instead"
      )

  @override
  def mounts(self) -> Mounts:
    """Stage the credential at ``<codex_home>/auth.json``.

    Returns:
      The single mount, writable so a token refresh can land.
    """
    target = f"{self.codex_home.rstrip('/')}/{AUTH_FILENAME}"
    return {target: Mount(LocalFile(epath.Path(self.auth_file)))}
