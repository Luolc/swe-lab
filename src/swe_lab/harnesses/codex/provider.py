"""Point Codex at an OpenAI-compatible endpoint, authenticated by API key.

The other half of :mod:`swe_lab.harnesses.codex.auth`. Codex authenticates two
ways, and they need different machinery:

- a **ChatGPT login**, which is a *file* (``auth.json``) — see ``auth.py``;
- an **API key**, which is an *env var*. Against OpenAI's own endpoint that is
  all it takes: the built-in ``openai`` provider already reads
  ``OPENAI_API_KEY``, so the sandbox passes it by reference through
  ``pass_env`` and nothing here is needed.

This module exists for the case that needs more: **a different base URL** — an
internal gateway, a proxy, a compatible third-party endpoint. Codex has no flag
for a base URL; it is a *config* value, expressible either in ``config.toml``
or through ``-c`` overrides, and this renders the latter.

**``-c``, not a staged ``config.toml``** — verified 2026-08-09 that a full
provider declared entirely through ``-c`` is honoured (a run so configured
dialled the custom base URL). Two reasons to prefer it: nothing has to be
mounted, and Codex **writes its own ``config.toml`` into ``CODEX_HOME``** at
startup, so a staged one would be a file the agent and the harness both claim.

**The API key never travels this way.** Codex does expose a config value that
takes a token directly (``experimental_bearer_token``, which its own schema
calls discouraged), but a ``-c`` argument lands in the process's argv *and* in
our staged invocation script — both readable. ``env_key`` names the variable
instead, and the value reaches the agent by reference through the sandbox's
``pass_env``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from swe_lab.sandbox import SandboxError

from .constants import API_KEY_ENV

type WireApi = Literal["responses"]
"""The wire protocol a provider speaks.

One value today: Codex 0.147.0's schema admits only ``responses`` (the API at
``/v1/responses``). Typed as a Literal anyway so a build that adds another is a
deliberate edit here rather than a string that silently means nothing.
"""

# A TOML bare key. The id is interpolated into a dotted config path
# (`model_providers.<id>.base_url`), so anything needing quotes would have to
# be quoted *inside* the path — far more likely a mistake than an intention.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


def _toml_string(value: str) -> str:
  """Render ``value`` as a TOML basic string.

  Hand-rolled because rendering a handful of scalars is all we need and a
  runtime dependency for it would cross the repo's ask-first boundary. Escapes
  exactly what the basic-string grammar requires.

  Args:
    value: The string to render.

  Returns:
    The quoted, escaped literal.

  Raises:
    SandboxError: If the value contains a control character, which no field
      here has any business carrying and which would produce a `-c` argument
      Codex cannot parse as TOML (and would then take as a literal string).
  """
  if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
    raise SandboxError(
        f"control character in codex provider value {value!r}; refusing to"
        " build an override Codex would misread"
    )
  escaped = value.replace("\\", "\\\\").replace('"', '\\"')
  return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
  """Render ``value`` as a TOML boolean (lowercase, unquoted).

  Args:
    value: The flag to render.

  Returns:
    ``"true"`` or ``"false"`` — quoting it would make Codex read the *string*,
    which is truthy either way.
  """
  return "true" if value else "false"


@dataclass(frozen=True)
class CodexProvider:
  """An OpenAI-compatible endpoint Codex should use instead of the default.

  Attributes:
    provider_id: The key this provider is registered and selected under.
      Restricted to a TOML bare key (it becomes part of a dotted config path).
    base_url: The endpoint's OpenAI-compatible base URL — the reason this type
      exists, since Codex has no flag for it.
    env_key: The environment variable Codex reads the API key from. Only the
      **name** is ever rendered; the value is the sandbox's job (``pass_env``),
      so the secret reaches neither argv nor a staged file.
    name: Friendly display name; defaults to ``provider_id``.
    wire_api: The protocol the endpoint speaks.
    supports_websockets: Whether the endpoint speaks the Responses API's
      **WebSocket** transport. **Off**, because the usual reason to set a base
      URL is a local reverse proxy, and a proxy typically forwards plain HTTP
      only — a run that tried to upgrade would fail at connect time.
    requires_openai_auth: Whether Codex should treat this endpoint as needing
      an OpenAI credential. **Off**, and this one is load-bearing for an
      unattended run: upstream's own description says a true value presents
      "login screen on first run", which in a headless container is a prompt
      nobody answers — the run would hang until the caller's timeout killed
      it. The endpoint is authenticated by ``env_key`` instead.
  """

  provider_id: str
  base_url: str
  env_key: str = API_KEY_ENV
  name: str = ""
  wire_api: WireApi = "responses"
  supports_websockets: bool = False
  requires_openai_auth: bool = False

  def __post_init__(self) -> None:
    """Refuse a provider that would render as an invalid or inert override.

    Raises:
      SandboxError: If the id is not a TOML bare key, or the base URL is empty
        — an empty one would silently leave the built-in endpoint in place,
        which is the opposite of why a caller reached for this.
    """
    if not _BARE_KEY_RE.match(self.provider_id):
      raise SandboxError(
          f"codex provider id {self.provider_id!r} must match"
          f" {_BARE_KEY_RE.pattern} (a TOML bare key)"
      )
    if not self.base_url.strip():
      raise SandboxError(
          "codex provider base_url is empty; omit the provider entirely to use"
          " the built-in endpoint rather than declaring one that does nothing"
      )

  def config_overrides(self) -> tuple[str, ...]:
    """Render the provider as ``-c key=value`` payloads.

    The selector comes first so a reader sees immediately which provider the
    run uses; the table entries follow.

    The two booleans are emitted **even when they match Codex's current
    defaults**. They are the difference between a run that works against a
    local proxy and one that hangs on a login prompt or fails a WebSocket
    upgrade, and a default that flips in a future build must not change that
    silently — we pin a version, but versions get bumped.

    Returns:
      The override strings, ready to be passed one per ``-c``. **No API key** —
      only the name of the variable Codex should read it from.
    """
    table = f"model_providers.{self.provider_id}"
    return (
        f"model_provider={_toml_string(self.provider_id)}",
        f"{table}.name={_toml_string(self.name or self.provider_id)}",
        f"{table}.base_url={_toml_string(self.base_url)}",
        f"{table}.env_key={_toml_string(self.env_key)}",
        f"{table}.wire_api={_toml_string(self.wire_api)}",
        f"{table}.supports_websockets={_toml_bool(self.supports_websockets)}",
        f"{table}.requires_openai_auth={_toml_bool(self.requires_openai_auth)}",
    )
