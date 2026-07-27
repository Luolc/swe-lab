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
from pathlib import Path

from ..errors import SandboxError
from ..sandbox import Sandbox
from ..spec import SandboxSpec
from .ghjob import GitHubJobSandbox
from .host import DockerHostSandbox

__all__ = [
    "DockerHostSandbox",
    "GitHubJobSandbox",
    "SandboxConfig",
    "SandboxFactory",
    "build_sandbox",
    "register_sandbox",
    "registered_backends",
]


@dataclass(frozen=True, slots=True)
class SandboxConfig:
  """The common run settings a factory maps onto whichever sandbox it builds.

  A-ghjob ignores ``network`` / ``pull`` / ``extra_hosts`` (Docker-only — the
  job is already the live container), so callers pass one option set and each
  factory takes what applies.

  Attributes:
    network: Whether the container gets network access (A-host only).
    pull: Whether to pull the image before the run (A-host only).
    env: Variables set on each exec as ``KEY=VALUE``.
    pass_env: Names of variables inherited by reference (value never on argv).
    shell: The interpreter each ``run_script`` / ``run_command`` uses.
    extra_hosts: ``--add-host`` entries for the container (A-host only).
  """

  network: bool = True
  pull: bool = True
  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Sequence[str] = ()
  shell: str = "/bin/bash"
  extra_hosts: Sequence[str] = ()


type SandboxFactory = Callable[[SandboxSpec, Path, SandboxConfig], Sandbox]
"""Builds a live sandbox from a spec, workspace, and the common config."""

_REGISTRY: dict[str, SandboxFactory] = {}


def register_sandbox(name: str, factory: SandboxFactory) -> None:
  """Register a sandbox factory under a ``--backend`` name.

  Args:
    name: The name ``--backend`` selects it by.
    factory: Builds the sandbox from ``(spec, workspace, config)``.
  """
  _REGISTRY[name] = factory


def registered_backends() -> list[str]:
  """Return the registered backend names, sorted."""
  return sorted(_REGISTRY)


def build_sandbox(
    name: str,
    spec: SandboxSpec,
    workspace: Path,
    *,
    network: bool = True,
    pull: bool = True,
    env: Mapping[str, str] | None = None,
    pass_env: Sequence[str] = (),
    shell: str = "/bin/bash",
    extra_hosts: Sequence[str] = (),
) -> Sandbox:
  """Construct the named sandbox from a common set of run settings.

  Args:
    name: The registered backend name (e.g. ``host`` / ``ghjob``).
    spec: The run context the sandbox realizes.
    workspace: The host/local workspace directory.
    network: Whether the container gets network access (A-host only).
    pull: Whether to pull the image before the run (A-host only).
    env: Variables set on each exec as ``KEY=VALUE``.
    pass_env: Names of variables inherited by reference.
    shell: The interpreter each script / command runs under.
    extra_hosts: ``--add-host`` entries (A-host only).

  Returns:
    The constructed sandbox (not yet up).

  Raises:
    SandboxError: If ``name`` is not registered.
  """
  try:
    factory = _REGISTRY[name]
  except KeyError:
    raise SandboxError(
        f"unknown backend {name!r}; registered: {registered_backends()}"
    ) from None
  config = SandboxConfig(
      network=network,
      pull=pull,
      env=dict(env or {}),
      pass_env=tuple(pass_env),
      shell=shell,
      extra_hosts=tuple(extra_hosts),
  )
  return factory(spec, workspace, config)


def _build_host(
    spec: SandboxSpec, workspace: Path, config: SandboxConfig
) -> Sandbox:
  return DockerHostSandbox(
      spec=spec,
      workspace=workspace,
      network=config.network,
      pull=config.pull,
      shell=config.shell,
      env=dict(config.env),
      pass_env=config.pass_env,
      extra_hosts=config.extra_hosts,
  )


def _build_ghjob(
    spec: SandboxSpec, workspace: Path, config: SandboxConfig
) -> Sandbox:
  return GitHubJobSandbox(
      spec=spec,
      workspace=workspace,
      shell=config.shell,
      env=dict(config.env),
      pass_env=config.pass_env,
  )


register_sandbox("host", _build_host)
register_sandbox("ghjob", _build_ghjob)
