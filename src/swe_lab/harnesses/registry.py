"""The open name registry that selects a harness.

Swapping the agent is not a field change — it is a different class — so it
cannot ride the same dotted-path override as ``model`` or ``capture``. It
rides a **name**: ``--rollout.harness=claude_code`` builds the registered
harness, and the field overrides that follow apply to whatever it built.

Selection is an open registry, like backends, stores and workflows (ADR-0003
§6.5): a consuming company registers its own agent from its own wrapper and
the name works with no swe-lab change. Built-ins register at import of their
own package.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import Harness

type HarnessFactory = Callable[[], Harness]
"""Builds a harness with its default configuration.

An invocation adjusts the fields afterwards, so what a name selects is the
agent, never a particular configuration of it.
"""

_REGISTRY: dict[str, HarnessFactory] = {}


def register_harness(name: str, factory: HarnessFactory) -> None:
  """Register a harness factory under a name.

  A **factory**, not an instance: the name selects a default-configured agent
  that the invocation then adjusts field by field, and two runs must not share
  one object.

  Args:
    name: The name it is selected by.
    factory: Builds the harness, configured as its default.
  """
  _REGISTRY[name] = factory


def registered_harnesses() -> list[str]:
  """Return the registered harness names, sorted."""
  return sorted(_REGISTRY)


def build_harness(name: str) -> Harness:
  """Build the named harness, with its own defaults.

  Args:
    name: The registered name.

  Returns:
    A freshly built harness.

  Raises:
    KeyError: If ``name`` is not registered — reported with the names that are.
  """
  try:
    factory = _REGISTRY[name]
  except KeyError:
    raise KeyError(
        f"unknown harness {name!r}; registered: {registered_harnesses()}"
    ) from None
  return factory()
