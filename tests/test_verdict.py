"""The `Verdict` base: what subclasses inherit, and what they cannot skip.

`Verdict` is an ABC rather than a Protocol (ADR-0006) for one reason — two of
its members are derivations with a rule, not fields. These pin the rule, the
enforcement, and the one thing that would silently undo the change.
"""

from dataclasses import dataclass, replace
from typing import override

import pytest

from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    SweBenchProVerdict,
)
from swe_lab.evaluation.verdict import Verdict


@dataclass(frozen=True, slots=True)
class _Minimal(Verdict):
  """The least a dataset has to write: everything else is inherited."""

  points: float
  attempts: int = 1

  @property
  @override
  def score(self) -> float:
    return self.points

  @override
  def with_attempts(self, attempts: int) -> "_Minimal":
    return replace(self, attempts=attempts)

  @override
  def summary(self) -> dict[str, object]:
    return {}

  @override
  def metrics(self) -> dict[str, float]:
    return {}


def _verdict(*, resolved: bool, attempts: int = 1) -> SweBenchProVerdict:
  return SweBenchProVerdict(
      passed=frozenset({"a"}),
      missing=frozenset() if resolved else frozenset({"b"}),
      output_state=OutputState.OK,
      attempts=attempts,
  )


def test_a_verdict_keeps_its_slots():
  # The trap this change could have sprung: `ABC` declares `__slots__`, but a
  # base that does not re-declare it hands every instance a `__dict__` back and
  # silently undoes `slots=True` on the verdict — which exists once per instance
  # per rollout. Nothing else would have failed.
  assert not hasattr(_verdict(resolved=True), "__dict__")
  assert not hasattr(_Minimal(points=1.0), "__dict__")


def test_the_derivations_are_inherited_not_restated():
  # The point of the ABC: a dataset writes `score` and gets the other two right.
  assert "resolved" not in SweBenchProVerdict.__dict__
  assert "flaky" not in SweBenchProVerdict.__dict__
  assert _Minimal(points=1.0).resolved is True
  assert _Minimal(points=0.5).resolved is False


def test_flaky_needs_a_retry_that_actually_resolved():
  # Deliberately not `attempts > 1`. A run that failed every attempt is failed,
  # not flaky, and this feeds the known-flaky registry — getting it wrong would
  # record every exhausted retry chain as a harness flake.
  assert _verdict(resolved=True, attempts=1).flaky is False
  assert _verdict(resolved=True, attempts=3).flaky is True
  assert _verdict(resolved=False, attempts=3).flaky is False


def test_an_incomplete_verdict_cannot_be_constructed():
  # What the Protocol could not do: fail at construction rather than only under
  # a type checker the consumer may not run.

  @dataclass(frozen=True, slots=True)
  class _Partial(Verdict):  # pyright: ignore[reportImplicitAbstractClass]
    attempts: int = 1

  with pytest.raises(TypeError, match="abstract"):
    _ = _Partial()  # pyright: ignore[reportAbstractUsage]
