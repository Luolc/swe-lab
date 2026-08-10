"""Concrete sandboxes + the open name registry that selects one.

Each concrete sandbox realizes the ``Sandbox`` ABC one way:
``DockerHostSandbox`` drives Docker from the host (A-host); ``GitHubJobSandbox``
runs in the job's own shell (A-ghjob). ``build_sandbox(name, …)`` is the single
construction seam both CLIs use.

Selection is an **open name registry**, not a closed enum (ADR-0003 §6.5): a
consuming company registers ``register_sandbox("acme", factory)`` from its own
wrapper (or an entry point), and ``--backend acme`` then works with no swe-lab
change. Built-ins register ``host`` / ``ghjob`` at import.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from etils import epath

from ..errors import SandboxError
from ..sandbox import Sandbox
from ..spec import SandboxSpec
from .ghjob import GitHubJobClaudeCodeBinaryObserver, GitHubJobSandbox
from .host import (
    DockerHostSandbox,
    HostClaudeCodeBinaryObserver,
    HostCodexBinaryObserver,
    HostMetricsObserver,
)

__all__ = [
    "DockerHostSandbox",
    "DockerHostSandboxConfig",
    "GhjobSandboxConfig",
    "GitHubJobClaudeCodeBinaryObserver",
    "GitHubJobSandbox",
    "HostClaudeCodeBinaryObserver",
    "HostCodexBinaryObserver",
    "HostMetricsObserver",
    "SandboxConfig",
    "SandboxFactory",
    "backend_of",
    "build_sandbox",
    "build_sandbox_config",
    "register_sandbox",
    "registered_backends",
    "sandbox_config_type",
    "sandbox_factory",
]


@dataclass(frozen=True, slots=True)
class SandboxConfig:
  """Backend-agnostic run **semantics** — what a task/entry may declare.

  Only what every backend must either honor or refuse loudly at
  construction — never silently ignore. Backend *mechanics* (a host
  workspace, an image pull) live on each backend's own subclass
  (:class:`DockerHostSandboxConfig`, :class:`GhjobSandboxConfig`, or a
  downstream backend's own — Resource-style ownership: the factory that
  registered alongside a config subclass is its only consumer).

  Attributes:
    network: Whether the run may reach the network. A backend that cannot
      enforce ``False`` (A-ghjob: the job container is already live) must
      refuse it, not no-op.
    env: Variables set on each exec as ``KEY=VALUE``.
    pass_env: Names of variables inherited by reference (value never on
      argv or in a staged file).
    shell: The interpreter each ``run_script`` / ``run_command`` uses —
      every backend execs scripts, so this is a run semantic.
  """

  network: bool = True
  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Sequence[str] = ()
  shell: str = "/bin/bash"


@dataclass(frozen=True, slots=True)
class DockerHostSandboxConfig(SandboxConfig):
  """A-host mechanics: how this backend realizes a run.

  Attributes:
    workspace: The bind-mounted host directory the run lives in.
    pull: Whether to pull the image before the run.
  """

  workspace: epath.Path | None = None
  pull: bool = True


@dataclass(frozen=True, slots=True)
class GhjobSandboxConfig(SandboxConfig):
  """A-ghjob mechanics (the job container is already the sandbox).

  Attributes:
    workspace: The job-local directory the run lives in.
  """

  workspace: epath.Path | None = None


type SandboxFactory = Callable[[SandboxSpec, SandboxConfig], Sandbox]
"""Builds a live (not-yet-up) sandbox from a spec and its backend's config."""

_REGISTRY: dict[str, tuple[SandboxFactory, type[SandboxConfig]]] = {}


def register_sandbox(
    name: str,
    factory: SandboxFactory,
    *,
    config_type: type[SandboxConfig] = SandboxConfig,
) -> None:
  """Register a sandbox factory (and its config type) under a backend name.

  Args:
    name: The name ``--backend`` selects it by.
    factory: Builds the sandbox from ``(spec, config)``.
    config_type: The config class this backend consumes; ``build_sandbox``
      constructs it from flat keyword settings, and a synthesizing caller
      (the workflow runner) uses it as the prototype type.
  """
  _REGISTRY[name] = (factory, config_type)


def registered_backends() -> list[str]:
  """Return the registered backend names, sorted."""
  return sorted(_REGISTRY)


def _lookup(name: str) -> tuple[SandboxFactory, type[SandboxConfig]]:
  try:
    return _REGISTRY[name]
  except KeyError:
    raise SandboxError(
        f"unknown backend {name!r}; registered: {registered_backends()}"
    ) from None


def sandbox_factory(name: str) -> SandboxFactory:
  """Return the registered factory — the object-config construction path.

  Args:
    name: The registered backend name.

  An unknown name raises ``SandboxError`` from the lookup.

  Returns:
    The factory; call it with ``(spec, config)`` where ``config`` is (a
    subclass of) the backend's registered config type.
  """
  return _lookup(name)[0]


def sandbox_config_type(name: str) -> type[SandboxConfig]:
  """Return the backend's config class (the override/prototype seam).

  Args:
    name: The registered backend name.

  An unknown name raises ``SandboxError`` from the lookup.

  Returns:
    The config class the backend consumes.
  """
  return _lookup(name)[1]


def build_sandbox_config(name: str) -> SandboxConfig:
  """Return a default instance of ``name``'s config class.

  The registry hook a CLI override uses to swap a whole config by backend
  name (``--rollout.sandbox=ghjob``), exactly as ``build_harness`` swaps an
  agent. Fields are that class's defaults: a swap replaces, it does not merge.

  Args:
    name: The registered backend name.

  Returns:
    The backend's config, at its defaults. An unknown name raises
    ``SandboxError`` from the lookup.
  """
  return _lookup(name)[1]()


def backend_of(config: SandboxConfig) -> str:
  """Return the backend name whose factory consumes this config.

  A config *is* the backend choice — ``GhjobSandboxConfig`` can only mean the
  job backend — so nothing has to carry the name alongside it.

  Args:
    config: The config to resolve.

  Returns:
    The registered backend name.

  Raises:
    SandboxError: If no registered backend consumes this config type.
  """
  declared = type(config)
  for name, (_, config_type) in _REGISTRY.items():
    if config_type is declared:
      return name
  # A downstream subclass of a registered config runs on that backend.
  for name, (_, config_type) in _REGISTRY.items():
    if isinstance(config, config_type) and config_type is not SandboxConfig:
      return name
  raise SandboxError(
      f"no registered backend consumes {declared.__name__};"
      f" registered: {registered_backends()}"
  )


def build_sandbox(name: str, spec: SandboxSpec, **settings: Any) -> Sandbox:
  """Construct the named sandbox from flat keyword settings.

  The settings become the backend's **own** config class, so an unknown or
  unsupported knob fails loudly at construction (``pull`` for A-ghjob is a
  ``SandboxError`` here, not a silent no-op). A caller holding a ready
  config object — possibly a downstream subclass — uses
  ``sandbox_factory(name)(spec, config)`` instead.

  Args:
    name: The registered backend name (e.g. ``host`` / ``ghjob``).
    spec: The run context the sandbox realizes.
    **settings: Fields of the backend's config class (``workspace`` accepts
      any path-like).

  Returns:
    The constructed sandbox (not yet up).

  Raises:
    SandboxError: If ``name`` is not registered, a setting is not a field of
      the backend's config, or a local backend is built without a
      ``workspace``.
  """
  factory, config_type = _lookup(name)
  if settings.get("workspace") is not None:
    settings["workspace"] = epath.Path(settings["workspace"])
  try:
    config = config_type(**settings)
  except TypeError as error:
    raise SandboxError(
        f"backend {name!r} ({config_type.__name__}) rejects these settings:"
        f" {error}"
    ) from error
  return factory(spec, config)


def _local_workspace(
    config: DockerHostSandboxConfig | GhjobSandboxConfig, name: str
) -> epath.Path:
  """Return the required host workspace, or fail if a local backend has none."""
  if config.workspace is None:
    raise SandboxError(
        f"backend {name!r} runs locally and needs a workspace directory"
    )
  return config.workspace


def _build_host(spec: SandboxSpec, config: SandboxConfig) -> Sandbox:
  if not isinstance(config, DockerHostSandboxConfig):
    raise SandboxError(
        f"backend 'host' consumes DockerHostSandboxConfig, got"
        f" {type(config).__name__}"
    )
  return DockerHostSandbox(
      spec=spec,
      workspace=_local_workspace(config, "host"),
      network=config.network,
      pull=config.pull,
      shell=config.shell,
      env=dict(config.env),
      pass_env=config.pass_env,
  )


def _build_ghjob(spec: SandboxSpec, config: SandboxConfig) -> Sandbox:
  if not isinstance(config, GhjobSandboxConfig):
    raise SandboxError(
        f"backend 'ghjob' consumes GhjobSandboxConfig, got"
        f" {type(config).__name__}"
    )
  if not config.network:
    # The job container is already live; its network cannot be cut. Refuse
    # loudly — a run that DECLARED offline semantics must not get online.
    raise SandboxError("backend 'ghjob' cannot honor network=False")
  return GitHubJobSandbox(
      spec=spec,
      workspace=_local_workspace(config, "ghjob"),
      shell=config.shell,
      env=dict(config.env),
      pass_env=config.pass_env,
  )


register_sandbox("host", _build_host, config_type=DockerHostSandboxConfig)
register_sandbox("ghjob", _build_ghjob, config_type=GhjobSandboxConfig)
