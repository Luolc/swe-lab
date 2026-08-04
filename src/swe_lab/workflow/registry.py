"""The open name registry of workflow definitions.

A definition is **pure declaration** — entries only, no instance, no store, no
run-time values — so it can be written once, statically, anywhere, and invoked
by name against any instance. Selection mirrors the backend and store
registries (ADR-0003 §6.5): a consuming company registers its own definition
from its own wrapper, and the name works with no swe-lab change.

Built-ins register at import of :mod:`swe_lab.workflow.definitions`, which is
deliberately *not* imported by this package — the shipped definitions name
concrete tasks and a concrete agent, and the engine must not depend on either.
"""

from __future__ import annotations

from .workflow import (
    validate_declaration,
    WorkflowEntry,
    WorkflowError,
)

type WorkflowDef = tuple[WorkflowEntry, ...]
"""A registrable workflow: its entries, in order, and nothing run-specific."""

_REGISTRY: dict[str, WorkflowDef] = {}


def register_workflow(name: str, definition: WorkflowDef) -> None:
  """Register a workflow definition under a name.

  The declaration is validated here, so a definition that could never run
  fails at *import* of whatever module registers it, naming the offending
  entry — not on the first attempt to use it.

  A malformed declaration raises ``WorkflowError`` from the validation.

  Args:
    name: The name the definition is invoked by.
    definition: The entries, in declared (topological) order.
  """
  validate_declaration(definition)
  _REGISTRY[name] = definition


def registered_workflows() -> list[str]:
  """Return the registered workflow names, sorted."""
  return sorted(_REGISTRY)


def workflow_definition(name: str) -> WorkflowDef:
  """Return a registered definition.

  Args:
    name: The registered name.

  Returns:
    The definition's entries.

  Raises:
    WorkflowError: If ``name`` is not registered.
  """
  try:
    return _REGISTRY[name]
  except KeyError:
    raise WorkflowError(
        f"unknown workflow {name!r}; registered: {registered_workflows()}"
    ) from None
