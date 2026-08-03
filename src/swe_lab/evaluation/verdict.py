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
from typing import Self

from swe_lab.sandbox import Mounts, SandboxFs


class Verdict(ABC):
  """The minimal cross-dataset surface: a scalar score, plus a resolved flag.

  ``score`` is 1.0 for a full pass and 0.0 for none; a future rubric- or
  model-judged eval method may report an intermediate score, so aggregation
  depends only on this scalar (it averages it).

  An ABC rather than a Protocol (ADR-0006, superseding ADR-0002 for this one
  interface): ``resolved`` and ``flaky`` are not fields but *derivations with a
  rule*, and a Protocol can only state a rule it cannot enforce. They are
  concrete here so every verdict inherits the same one.
  """

  # Not decoration: ``ABC`` declares this, but a subclass that does not
  # re-declare it gives every instance a ``__dict__`` again, which silently
  # defeats ``slots=True`` on the concrete verdict — 40 bytes to 56 on this
  # repo's own shape, and typo'd attributes stop raising. One verdict exists per
  # instance per rollout.
  __slots__: tuple[str, ...] = ()

  # How many evaluation attempts produced this verdict (``1`` = first try). More
  # than one means the eval was re-run against the **same patch** after a
  # failure (ADR-0005) — the candidate never changes between attempts, so this
  # averages out harness nondeterminism, not model quality.
  #
  # Declared, not abstract: this is the one member that really is *data*, and a
  # subclass satisfies it with an ordinary dataclass field. An abstract property
  # here would force every verdict to wrap a private field in a getter to say
  # the same thing.
  attempts: int

  @property
  @abstractmethod
  def score(self) -> float:
    """The scalar outcome in ``[0, 1]``."""
    ...

  @property
  def resolved(self) -> bool:
    """Whether the run is a full pass (``score >= 1.0``)."""
    return self.score >= 1.0

  @property
  def flaky(self) -> bool:
    """Whether it resolved only after a retry.

    Derived, never stored, and deliberately *not* ``attempts > 1``: a run that
    failed every attempt is not flaky, it is failed. This is the signal that
    feeds the known-flaky registry, so the difference is the difference between
    recording a harness flake and recording an exhausted retry chain as one.
    """
    return self.attempts > 1 and self.resolved

  @abstractmethod
  def with_attempts(self, attempts: int) -> Self:
    """Return a copy recording how many attempts the run took.

    Implemented by the dataset's verdict (a one-line ``dataclasses.replace``)
    because only it knows its own shape; the generic eval method counts the
    attempts but cannot construct a concrete verdict.

    Args:
      attempts: The number of attempts, ``1`` or more.

    Returns:
      A verdict identical but for the attempt count.
    """
    ...

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
      the compiled expectation).
    grader: Judges the workspace after the run.
    retries: Overrides the caller's retry budget for this instance (ADR-0005),
      or ``None`` to accept whatever the caller passes. **swe-lab sets this
      nowhere** — every instance ships ``None``, so the CLI default applies
      uniformly and the shipped numbers stay comparable. It exists because a
      consumer may know something we do not: an instance measured to fail 16% of
      the time needs more attempts than one measured at 2%, and only whoever
      took that measurement can say so.

      Set it by compiling the spec and replacing the field::

          compiled = instance.unit_test_spec(patch=patch)
          spec = dataclasses.replace(compiled, retries=3)

      or, to reach the CLI path where the spec is compiled internally, by
      overriding ``unit_test_spec`` on a subclass of the dataset's record — the
      same extension point ``run_script`` / ``parser`` already document.

      Raising it for one instance is a **scoring decision** about that instance,
      so it should be as visible as any other; this field is deliberately not
      wired to ``known_flaky.py``, which annotates and never acts.
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
  retries: int | None = None
  native_outputs: dict[str, str] = field(default_factory=dict)
