"""Supply a Codex ChatGPT login to the sandbox, as bytes.

Codex authenticates one of two ways, and only one of them fits the usual "pass
the secret by name" pattern:

- an **API key** in an env var — the sandbox's ``pass_env`` already carries it
  by reference, and a custom endpoint is configured through
  :mod:`swe_lab.harnesses.codex.provider`; nothing here is needed;
- a **ChatGPT login**, which lives in ``auth.json`` under ``CODEX_HOME``. That
  is a *file*, so it has to reach the sandbox some other way.

This observer stages it as an **inline** mount — the credential is held as
bytes and written into the sandbox directly, rather than referenced as a host
path. That is what makes it work for a sandbox sharing no filesystem with this
process (a remote one), and it lets a caller supply a credential that never
touches local disk at all: read straight out of a secret manager and handed
over. :meth:`CodexAuthObserver.from_file` is the convenience for the ordinary
case of a login that already *is* a local file.

Either way the bytes never reach a command line, never land in the workspace
(where the run's artifacts are collected from and persisted), and go away with
the container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import override

from etils import epath

from swe_lab.sandbox import (
    Inline,
    Mount,
    Mounts,
    SandboxError,
    SandboxObserver,
)

from .constants import AGENT_HOME, AUTH_FILENAME, codex_config_dir


@dataclass(frozen=True)
class CodexAuthObserver(SandboxObserver):
  """Stage a ChatGPT login into the sandbox's ``CODEX_HOME``.

  Mounted **writable**, deliberately: Codex refreshes its access token and
  writes the file back, and a read-only credential would turn an expiring token
  into an opaque mid-run failure. The container gets a copy, so the refreshed
  credential is discarded with it and the caller's original is untouched.

  Attributes:
    auth_json: The credential's bytes. Kept out of ``repr``: nothing in the
      engine logs a mount today (errors carry only the target path), but a
      credential that prints itself in a traceback or a debugger is one
      careless log line away from a leak, and hiding it costs nothing.
    agent_home: The in-sandbox ``HOME``; must match the harness's own. The
      credential lands in the config dir derived from it (``$HOME/.codex``), so
      this observer and the harness cannot disagree about where Codex will
      look.
  """

  auth_json: bytes = field(repr=False)
  agent_home: str = AGENT_HOME

  def __post_init__(self) -> None:
    """Refuse a credential Codex could not use.

    Checked here, on the host, because the alternative is an authentication
    failure minutes into a run inside a container that is then thrown away.

    Raises:
      SandboxError: If the credential is empty, is not valid JSON, or is not a
        JSON object.
    """
    if not self.auth_json.strip():
      raise SandboxError(
          "codex auth_json is empty; supply a ChatGPT login's auth.json bytes,"
          " or use an API key (OPENAI_API_KEY) instead"
      )
    try:
      parsed = json.loads(self.auth_json)
    except ValueError as error:
      # Deliberately reports the error's *type* and not the payload: this
      # message may be logged, and the payload is a credential.
      raise SandboxError(
          f"codex auth_json is not valid JSON ({type(error).__name__}); it"
          " should be the contents of a Codex auth.json"
      ) from error
    if not isinstance(parsed, dict):
      raise SandboxError(
          "codex auth_json must be a JSON object, as Codex's auth.json is"
      )

  @classmethod
  def from_file(
      cls, path: epath.PathLike, *, agent_home: str = AGENT_HOME
  ) -> CodexAuthObserver:
    """Build one from a login that is already a file on this host.

    Args:
      path: Host path of the ``auth.json`` to stage (``~/.codex/auth.json``).
      agent_home: The in-sandbox ``HOME``.

    Returns:
      The observer, holding the file's bytes.

    Raises:
      SandboxError: If the file is not there — an absent login would otherwise
        surface as an authentication failure inside the container, minutes
        later and with a much worse message.
    """
    source = epath.Path(path)
    if not source.is_file():
      raise SandboxError(
          f"codex auth file not found: {path}; a ChatGPT login lives in"
          " <CODEX_HOME>/auth.json, or use OPENAI_API_KEY instead"
      )
    return cls(auth_json=source.read_bytes(), agent_home=agent_home)

  @override
  def mounts(self) -> Mounts:
    """Stage the credential at ``$HOME/.codex/auth.json``.

    Returns:
      The single mount: inline, so a sandbox sharing no filesystem with this
      process still works, and writable, so a token refresh can land.
    """
    target = f"{codex_config_dir(self.agent_home)}/{AUTH_FILENAME}"
    return {target: Mount(Inline(self.auth_json))}
