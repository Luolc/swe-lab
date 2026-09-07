"""Which upstream answers the supervisor's calls, named rather than assumed.

A supervised run pays a provider. Which one is a *deployment* choice — it does
not change what supervision does — but it is not a free one either: a paid run
in this repo spends the OpenRouter key pool (``AGENTS.md``, Boundaries), and
until this module existed there was no way to say so on an invocation: the base
URL was a module constant baked into a ``functools.partial`` and a closure, and
``swe_lab.cli.overrides`` walks dataclass fields, so nothing on a command line
could reach it.

So a provider is a **name** here, not a pair of strings the caller assembles:
``--rollout.harness.segmented.provider=openrouter`` selects one, an unknown
name is refused before a container is paid for, and the name lands on every
decision row the run writes. That last part is the constraint ADR-0024 states
for supervision settings and it applies here for a weaker reason — a provider
is not an arm — but the same failure: two runs whose records look identical
while a different account answered them cannot be compared, and nothing about
the request would show it afterwards.

**A provider carries no model name.** Measured 2026-09-07 (the three arms are
recorded in ``docs/conventions.md``, Secrets): OpenRouter's Messages endpoint
accepts the bare Anthropic name and namespaces it itself, so the pinned
:data:`~swe_lab.workflow.definitions.SUPERVISOR_MODEL` travels unchanged and
there is nothing here to translate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import dataclasses
import functools
import json
import urllib.request

from .judge import messages_transport, Transport

#: OpenRouter's API root. ``/v1/messages`` is appended by the transport, which
#: is why this stops at ``/api``: the endpoint is
#: ``https://openrouter.ai/api/v1/messages``. Verified 2026-09-07 to serve the
#: Anthropic Messages wire — ``x-api-key`` plus ``anthropic-version``, an
#: Anthropic-shaped answer back — so the transport needs no provider branch.
OPENROUTER_API = "https://openrouter.ai/api"

#: Where OpenRouter reports one key's own status. Asked before a run is spent,
#: never for its body: the answer is only used as "this key is live".
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"

#: How long a liveness probe may take. Short: it runs before the run does, and
#: a key that cannot answer in this is not one to start a rollout on.
PROBE_TIMEOUT_SECONDS = 20.0


class ProviderError(Exception):
  """A provider that cannot be used — refused before anything is spent."""


@dataclasses.dataclass(frozen=True)
class Provider:
  """One upstream, and everything a call to it needs but the payload.

  Attributes:
    name: The registry name, and what a record says answered the run.
    base_url: Upstream root; the transport appends ``/v1/messages``.
    api_key_env: Environment variable holding the credential. Only the name
      lives here — the value is read at call time and never reaches a command
      line, a log or this repo.
    pooled: Whether ``api_key_env`` holds a comma-separated pool rather than a
      single key. A pool is split **inside this program**, never in a shell,
      and one live member is sampled rather than assumed — see
      :func:`live_key`.
  """

  name: str
  base_url: str
  api_key_env: str
  pooled: bool


#: The registry name of the default provider — the owner's own Anthropic
#: account, reached with a personal key. The default only because it is what
#: the shipped definitions have always used; **it is not what a paid experiment
#: spends** (``AGENTS.md``, Boundaries).
ANTHROPIC = "anthropic"

#: The registry name of the key pool a paid run spends. Each member is a
#: separate account with its own balance, which is why the pool is a pool and
#: not a fallback chain.
OPENROUTER = "openrouter"


@functools.cache
def _providers() -> Mapping[str, Provider]:
  """Return the registry, built once.

  The **deferred import** is the reason this is a function rather than a dict
  literal: the Anthropic base URL has one home, in the ``claude_code``
  harness's constants, and that package imports
  :mod:`swe_lab.trace_synthesis.segmented_loop`, which imports this module.
  :mod:`swe_lab.trace_synthesis.supervisor` defers its own imports of that
  harness for the same cycle. Copying the URL here instead would give one fact
  two homes.

  Returns:
    Provider name to provider.
  """
  from swe_lab.harnesses.claude_code.constants import (  # noqa: PLC0415
      ANTHROPIC_API,
  )

  providers = (
      Provider(
          name=ANTHROPIC,
          base_url=ANTHROPIC_API,
          api_key_env="ANTHROPIC_API_KEY",
          pooled=False,
      ),
      Provider(
          name=OPENROUTER,
          base_url=OPENROUTER_API,
          api_key_env="OPENROUTER_API_KEYS",
          pooled=True,
      ),
  )
  return {provider.name: provider for provider in providers}


def build_provider(name: str) -> Provider:
  """Return the provider registered under ``name``.

  Args:
    name: The registry name, as an override spells it.

  Returns:
    The provider.

  Raises:
    ProviderError: No provider goes by that name.
  """
  providers = _providers()
  provider = providers.get(name)
  if provider is None:
    known = ", ".join(sorted(providers))
    raise ProviderError(
        f"unknown supervisor provider {name!r} (known: {known})"
    )
  return provider


#: Answers "is this key live", given the key. Injected so the selection logic
#: is testable without a network and without a credential.
Probe = Callable[[str], bool]


def _openrouter_probe(key: str) -> bool:
  """Ask OpenRouter whether one key is usable.

  Args:
    key: The credential. Sent in a header — never on a command line.

  Returns:
    Whether the key answered.
  """
  request = urllib.request.Request(
      OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {key}"}
  )
  try:
    with urllib.request.urlopen(
        request, timeout=PROBE_TIMEOUT_SECONDS
    ) as response:
      _ = json.load(response)
  except Exception:  # noqa: BLE001 — a dead key is data, not an error
    return False
  return True


def live_key(pool: str, *, probe: Probe = _openrouter_probe) -> str:
  """Return the first member of ``pool`` that answers.

  **Sampled, not assumed.** Not every key in the pool is live, and a rollout
  started on a dead one fails after a container has been paid for. The pool is
  split here, inside the program, because splitting it in a shell would put
  every key on a command line.

  No key value appears in the return path of an error: a failure names how many
  were tried and nothing else.

  Args:
    pool: The raw environment value — comma-separated credentials.
    probe: How a single key is tested.

  Returns:
    The first key that answered.

  Raises:
    ProviderError: The pool is empty, or no member answered.
  """
  keys = [part for part in pool.split(",") if part]
  if not keys:
    raise ProviderError("the key pool is empty")
  for key in keys:
    if probe(key):
      return key
  raise ProviderError(f"no key in the pool answered ({len(keys)} tried)")


def _sole_key(raw: str) -> str:
  """Return the environment value unchanged — a provider holding one key."""
  return raw


def _pooled_selector(probe: Probe) -> Callable[[str], str]:
  """Return a selector that samples a live key once and reuses it.

  Once, because the alternative is a probe per judge call: the supervisor asks
  a model at every boundary, and a second HTTP round trip on each of them would
  be paid for by the run's wall clock for no reading.

  Args:
    probe: How a single key is tested.

  Returns:
    A selector mapping the raw pool value to one live key.
  """
  chosen: list[str] = []

  def select(raw: str) -> str:
    if not chosen:
      chosen.append(live_key(raw, probe=probe))
    return chosen[0]

  return select


def transport_for(
    provider: Provider, *, probe: Probe = _openrouter_probe
) -> Transport:
  """Return the transport that sends this provider's requests.

  Args:
    provider: The upstream to address.
    probe: How a pooled provider's keys are tested; ignored for a provider
      holding a single key.

  Returns:
    The transport.
  """
  return functools.partial(
      messages_transport,
      base_url=provider.base_url,
      api_key_env=provider.api_key_env,
      select_key=_pooled_selector(probe) if provider.pooled else _sole_key,
  )
