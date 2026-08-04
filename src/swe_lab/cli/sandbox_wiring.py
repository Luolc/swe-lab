"""Turn a command's sandbox flags into the chosen backend's own config.

The runner builds every sandbox through the backend registry, so a command
hands it a **config instance** — the invocation's half of what a run needs
(which backend, and that backend's mechanics), onto which a workflow entry's
declared semantics and the runner's per-attempt workspace are merged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from typing import Any

from swe_lab.sandbox import sandbox_config_type, SandboxConfig


def invocation_config(
    backend: str,
    *,
    network: bool = True,
    pull: bool = True,
    pass_env: Sequence[str] = (),
) -> SandboxConfig:
  """Build ``backend``'s config from this command's flags.

  ``--pull`` is a Docker-host mechanic, and a backend whose config has no such
  field (the GH job is already running inside its container) is simply not
  asked to pull: a flag's *default* is not an instruction, and refusing here
  would make ``--backend ghjob`` unusable without spelling out flags that
  cannot apply to it. Everything a backend does declare is passed straight
  through, so a value it cannot honor still fails loudly at construction.

  Args:
    backend: The registered backend name.
    network: Whether the run may reach the network.
    pull: Whether to pull the image first (host-style backends only).
    pass_env: Names of environment variables inherited by reference.

  Returns:
    The backend's own config, ready to hand to the runner.
  """
  config_type = sandbox_config_type(backend)
  settings: dict[str, Any] = {"network": network, "pass_env": tuple(pass_env)}
  if any(f.name == "pull" for f in fields(config_type)):
    settings["pull"] = pull
  return config_type(**settings)
