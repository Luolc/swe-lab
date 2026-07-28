"""The dataset-agnostic runnable-instance interface.

A CLI must never import a concrete dataset. Instead, a dataset's record exposes
*how to run this instance* through :class:`TaskInstance`: the sandbox context,
the solve prompt, the gold patch, and its compiled unit-test eval. The CLIs
resolve an instance by name (``load_dataset(name).require(id)``) and call these
methods polymorphically — swapping in a new SWE-like dataset is a new record
type that implements this ABC, with no CLI change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.sandbox import SandboxSpec


class TaskInstance[V: Verdict](ABC):
  """A runnable dataset instance: how to solve and grade it (ADR-0002 ABC).

  A behavior interface implemented by a dataset's record type (which is also a
  ``DatasetRecord`` for the loader). Generic over the dataset's verdict type so
  ``unit_test_spec`` returns a correctly-typed spec; a CLI uses only the base
  ``Verdict`` surface (``score`` / ``resolved`` / ``summary``).
  """

  instance_id: str  # provided by the concrete record

  @abstractmethod
  def sandbox_spec(self) -> SandboxSpec:
    """Return the run context (image / workdir / base commit) to run in."""
    ...

  @abstractmethod
  def solve_prompt(self) -> str:
    """Return the dataset-derived prompt for the solving agent."""
    ...

  @abstractmethod
  def gold_patch(self) -> str:
    """Return the instance's own reference (gold) patch."""
    ...

  @abstractmethod
  def unit_test_spec(
      self,
      *,
      patch: str | None,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[V]:
    """Compile this instance's unit-test evaluation spec.

    Pair it with :meth:`sandbox_spec` for the run context (that is not returned
    here — it is the same context solving uses).

    Args:
      patch: The candidate diff to apply, or ``None`` to grade the base commit
        untouched (a self-check that the required tests fail without a fix).
      checkout_golden_tests: Restore the held-out golden test files after the
        reset (so a candidate patch cannot game them).

    Returns:
      The compiled unit-test spec.
    """
    ...
