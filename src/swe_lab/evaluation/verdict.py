"""The evaluation axis's cross-dataset contracts.

A ``Verdict`` is the minimal thing sweeps and aggregation depend on: a scalar
``score`` in ``[0, 1]``. A ``Grader`` turns the workspace a run left behind into
a verdict. A ``UnitTestSpec`` is what the unit-test method needs to run and
grade one instance; each dataset compiles its own record into one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from swe_lab.sandbox import Mounts


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


class Grader[V: Verdict](Protocol):
  """Dataset-owned judgment: the workspace files a run left → a verdict.

  Pure over the workspace (reads files, touches no container), so it is
  unit-testable without Docker and can re-grade any persisted workspace.
  """

  def grade(self, workspace: Path) -> V:
    """Grade the run from the files under ``workspace``."""
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
