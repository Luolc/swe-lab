"""The `Verdict` base: what subclasses inherit, and what they cannot skip.

`Verdict` is an ABC rather than a Protocol (ADR-0006) for one reason —
``resolved`` is a derivation with a rule, not a field. These pin the rule, the
enforcement, and the one thing that would silently undo the change.
"""

from dataclasses import dataclass
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

  @property
  @override
  def score(self) -> float:
    return self.points

  @override
  def summary(self) -> dict[str, object]:
    return {}

  @override
  def metrics(self) -> dict[str, float]:
    return {}


def _verdict(*, resolved: bool) -> SweBenchProVerdict:
  return SweBenchProVerdict(
      passed=frozenset({"a"}),
      missing=frozenset() if resolved else frozenset({"b"}),
      output_state=OutputState.OK,
  )


def test_a_verdict_keeps_its_slots():
  # The trap this change could have sprung: `ABC` declares `__slots__`, but a
  # base that does not re-declare it hands every instance a `__dict__` back and
  # silently undoes `slots=True` on the verdict — which exists once per instance
  # per rollout. Nothing else would have failed.
  assert not hasattr(_verdict(resolved=True), "__dict__")
  assert not hasattr(_Minimal(points=1.0), "__dict__")


def test_the_derivation_is_inherited_not_restated():
  # The point of the ABC: a dataset writes `score` and gets `resolved` right.
  assert "resolved" not in SweBenchProVerdict.__dict__
  assert _Minimal(points=1.0).resolved is True
  assert _Minimal(points=0.5).resolved is False


def test_a_verdict_carries_no_attempt_history():
  # ADR-0008: one verdict grades one tree. How many attempts it took, and
  # whether an earlier one flaked, are the runner's facts — recorded per
  # attempt in the store, never folded into the answer.
  assert not hasattr(_verdict(resolved=True), "attempts")
  assert not hasattr(_verdict(resolved=True), "flaky")


def test_an_incomplete_verdict_cannot_be_constructed():
  # What the Protocol could not do: fail at construction rather than only under
  # a type checker the consumer may not run.

  @dataclass(frozen=True, slots=True)
  class _Partial(Verdict):  # pyright: ignore[reportImplicitAbstractClass]
    points: float = 0.0

  with pytest.raises(TypeError, match="abstract"):
    _ = _Partial()  # pyright: ignore[reportAbstractUsage]
