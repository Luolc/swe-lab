"""Supply a Grok OAuth login to the sandbox, as bytes.

The codex auth observer's pattern, applied to grok's credential file. Grok
authenticates two ways:

- an **API key** in ``XAI_API_KEY`` — an env var, carried by the sandbox's
  ``pass_env``; nothing here is needed;
- an **OAuth login**, which lives in ``auth.json`` under ``$HOME/.grok``
  (grok derives the dir from ``HOME``; there is no relocation variable). That
  is a *file*, staged here as an **inline** mount: the credential is held as
  bytes and written into the sandbox directly, so a sandbox sharing no
  filesystem with this process works, and a caller can hand over a credential
  that never touched local disk. :meth:`GrokAuthObserver.from_file` is the
  convenience for a login that already is a local file.

Either way the bytes never reach a command line, never land in the workspace
(where the run's artifacts are collected from), and go away with the container.
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

from .constants import AGENT_HOME, AUTH_FILENAME, grok_config_dir


@dataclass(frozen=True)
class GrokAuthObserver(SandboxObserver):
  """Stage an OAuth login into the sandbox's ``$HOME/.grok``.

  Mounted **writable**, deliberately: grok refreshes its access token against
  the ``refresh_token`` in the file and writes it back, and a read-only
  credential would turn an expiring token into an opaque mid-run failure. The
  container gets a copy, so the refreshed credential is discarded with it and
  the caller's original is untouched.

  Attributes:
    auth_json: The credential's bytes. Kept out of ``repr``: a credential that
      prints itself in a traceback or a debugger is one careless log line away
      from a leak, and hiding it costs nothing.
    agent_home: The in-sandbox ``HOME``; must match the harness's own. The
      credential lands in the dir derived from it (``$HOME/.grok``), so this
      observer and the harness cannot disagree about where grok will look.
  """

  auth_json: bytes = field(repr=False)
  agent_home: str = AGENT_HOME

  def __post_init__(self) -> None:
    """Refuse a credential grok could not use.

    Checked here, on the host, because the alternative is an authentication
    failure minutes into a run inside a container that is then thrown away.

    Raises:
      SandboxError: If the credential is empty, is not valid JSON, or is not a
        JSON object.
    """
    if not self.auth_json.strip():
      raise SandboxError(
          "grok auth_json is empty; supply an OAuth login's auth.json bytes,"
          " or use an API key (XAI_API_KEY) instead"
      )
    try:
      parsed = json.loads(self.auth_json)
    except ValueError as error:
      # Deliberately reports the error's *type* and not the payload: this
      # message may be logged, and the payload is a credential.
      raise SandboxError(
          f"grok auth_json is not valid JSON ({type(error).__name__}); it"
          " should be the contents of a grok auth.json"
      ) from error
    if not isinstance(parsed, dict):
      raise SandboxError(
          "grok auth_json must be a JSON object, as grok's auth.json is"
      )

  @classmethod
  def from_file(
      cls, path: epath.PathLike, *, agent_home: str = AGENT_HOME
  ) -> GrokAuthObserver:
    """Build one from a login that is already a file on this host.

    Args:
      path: Host path of the ``auth.json`` to stage (``~/.grok/auth.json``).
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
          f"grok auth file not found: {path}; an OAuth login lives in"
          " ~/.grok/auth.json, or use XAI_API_KEY instead"
      )
    return cls(auth_json=source.read_bytes(), agent_home=agent_home)

  @override
  def mounts(self) -> Mounts:
    """Stage the credential at ``$HOME/.grok/auth.json``.

    Returns:
      The single mount: inline, so a sandbox sharing no filesystem with this
      process still works, and writable, so a token refresh can land.
    """
    target = f"{grok_config_dir(self.agent_home)}/{AUTH_FILENAME}"
    return {target: Mount(Inline(self.auth_json))}
