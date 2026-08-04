"""The evaluation axis's cross-dataset contracts.

A ``Verdict`` is the minimal thing sweeps and aggregation depend on: a scalar
``score`` in ``[0, 1]``. A ``Grader`` turns the files a run left behind (read
through the sandbox) into a verdict. A ``UnitTestSpec`` is what the unit-test
method needs to run and grade one instance; each dataset compiles its own record
into one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from swe_lab.sandbox import Mounts, SandboxFs
from swe_lab.sandbox.observers import PATCH_NAME


class Verdict(ABC):
  """The minimal cross-dataset surface: a scalar score, plus a resolved flag.

  ``score`` is 1.0 for a full pass and 0.0 for none; a future rubric- or
  model-judged eval method may report an intermediate score, so aggregation
  depends only on this scalar (it averages it).

  A verdict is **one grading of one tree**: it says how that run went and
  carries no history. Retrying is the runner's business (ADR-0008), and the
  evidence for it is the persisted attempt sequence, not a counter riding on
  the answer.

  An ABC rather than a Protocol (ADR-0006, superseding ADR-0002 for this one
  interface): ``resolved`` is not a field but a *derivation with a rule*, and a
  Protocol can only state a rule it cannot enforce. It is concrete here so
  every verdict inherits the same one.
  """

  # Not decoration: ``ABC`` declares this, but a subclass that does not
  # re-declare it gives every instance a ``__dict__`` again, which silently
  # defeats ``slots=True`` on the concrete verdict — 40 bytes to 56 on this
  # repo's own shape, and typo'd attributes stop raising. One verdict exists per
  # instance per rollout.
  __slots__: tuple[str, ...] = ()

  @property
  @abstractmethod
  def score(self) -> float:
    """The scalar outcome in ``[0, 1]``."""
    ...

  @property
  def resolved(self) -> bool:
    """Whether the run is a full pass (``score >= 1.0``)."""
    return self.score >= 1.0

  @abstractmethod
  def summary(self) -> dict[str, object]:
    """Dataset-specific detail for a report, beyond ``score`` / ``resolved``.

    Keeps a caller (a CLI, a report) from having to know a concrete verdict's
    fields — it prints ``score`` + ``resolved`` + whatever this returns.

    Scalar entries here are the ones a persisted run record keeps; list-valued
    entries are for a human report only, since a shard must not grow with the
    instance (one SWE-Bench Pro instance has 681 required tests).
    """
    ...

  @abstractmethod
  def metrics(self) -> dict[str, float]:
    """Dataset-specific *numeric* detail, for the run's metrics.

    Separate from :meth:`summary` because metrics are scalars a sweep can
    aggregate across runs (counts, ratios), where summary carries prose and
    lists. Names are unqualified — the eval method namespaces them.
    """
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
      the compiled expectation). The **patch is not among them**: it is the
      eval task's declared input, staged by whoever supplies it.
    grader: Judges the workspace after the run.
    patch_name: The workspace file the compiled script applies, when it
      applies one. Recorded so the spec self-describes what it reads, and so
      the task's declared input and the script can never drift apart.
    native_outputs: Byproducts the eval script writes, as artifact name →
      workspace-relative filename. Declared by the dataset because the names
      are its own (``output.json``, the test logs), and registered *best
      effort* — a run that died early simply produces fewer. This is what makes
      a failed grading debuggable after the fact, so it should name the logs,
      not only the parsed result.
  """

  eval_script: str
  mounts: Mounts
  grader: Grader[V]
  patch_name: str = PATCH_NAME
  native_outputs: dict[str, str] = field(default_factory=dict)
