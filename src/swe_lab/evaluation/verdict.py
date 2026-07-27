"""The evaluation axis's cross-dataset contracts.

A ``Verdict`` is the minimal thing sweeps and aggregation depend on: a scalar
``score`` in ``[0, 1]``. A ``Grader`` turns the files a run left behind (read
through the sandbox) into a verdict. A ``UnitTestSpec`` is what the unit-test
method needs to run and grade one instance; each dataset compiles its own record
into one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

from swe_lab.sandbox import Mounts, SandboxFs


class Verdict(Protocol):
  """The minimal cross-dataset surface: a scalar score, plus a resolved flag.

  ``score`` is 1.0 for a full pass and 0.0 for none; a future rubric- or
  model-judged eval method may report an intermediate score, so aggregation
  depends only on this scalar (it averages it). ``resolved`` is the derived
  ``score >= 1.0`` convenience for binary pass/fail callers.
  """

  @property
  def score(self) -> float:
    """The scalar outcome in ``[0, 1]``."""
    ...

  @property
  def resolved(self) -> bool:
    """Whether the run is a full pass (``score >= 1.0``)."""
    ...


class Grader[V: Verdict](ABC):
  """Dataset-owned judgment: the files a run left → a verdict.

  A behavior interface (ABC, per ADR-0002): datasets implement it in-repo and
  benefit from explicit inheritance + instantiation-time enforcement. Reads the
  run's output files through the narrow ``SandboxFs`` view (never the
  lifecycle), so it is unit-testable without Docker (a ``FakeSandbox`` over a
  local dir) and can re-grade any persisted workspace.
  """

  @abstractmethod
  def grade(self, sb: SandboxFs) -> V:
    """Grade the run from the output files read through ``sb``."""
    ...


@dataclass(frozen=True)
class UnitTestSpec[V: Verdict]:
  """What the unit-test method needs to run and grade one instance.

  A dataset compiles its record into this; the method stages ``mounts``, runs
  ``eval_script`` in the container, and grades with ``grader``.

  Attributes:
    eval_script: The bash the container runs (staged as ``entryscript.sh``).
    mounts: The other files the run needs staged (e.g. the test harness and
      the compiled expectation).
    grader: Judges the workspace after the run.
  """

  eval_script: str
  mounts: Mounts
  grader: Grader[V]
