"""Reporting a rate, with the counts that make it readable.

ADR-0015 §5 and ADR-0016 (``docs/decisions/``)
require every rate to be reported with **two** counts beside it: how many runs
were excluded as ours, and how many nobody could attribute. Both ADRs recorded
that as a contract on a reporter that did not exist, which is the same shape as
a metric nobody reads — so this module exists to make it structural instead.

**The rate and its counts are one value, with one rendering.**
:meth:`Rate.render` is the only line this module produces, and it produces all
of the numbers together.

**What that is, and what it is not.** It makes the *canonical* report line
impossible to write without the counts, so a reporter built on this cannot drop
them by accident. It does **not** make a bare fraction unobtainable: the fields
are public and a caller can format its own string. That would be writing a
second renderer, which is a visible thing to do in review — not the same as
forgetting a count. The invariant this module can hold is stated at that
strength deliberately, because a stronger claim than the code supports is how a
contract becomes decoration.

The counts are printed **even when they are zero**, deliberately. "No runs were
excluded" and "exclusions were not reported" are different facts, and a
parenthetical that appears only when non-zero makes them look identical — which
is the silence these two ADRs are about.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from swe_lab.rollout import RolloutOutcome


@dataclass(frozen=True, slots=True)
class Rate:
  """A rate together with what was left out of it, and what nobody could place.

  Attributes:
    numerator: How many counted runs met the condition being reported.
    denominator: How many runs were counted — those whose ending was **not**
      positively identified as ours.
    excluded: How many runs left the denominator because their ending was ours.
    unclassified: How many counted runs ended in a way that was positively
      identified as neither ours nor the actor's. These are **inside** the
      denominator (ADR-0015 §4); the count is what keeps them from being
      silent there.
  """

  numerator: int
  denominator: int
  excluded: int
  unclassified: int

  def __post_init__(self) -> None:
    """Refuse a rate that cannot describe a real batch.

    Raises:
      ValueError: If any count is negative, or the numerator exceeds the
        denominator, or more runs are unclassified than were counted.
    """
    counts = (
        self.numerator,
        self.denominator,
        self.excluded,
        self.unclassified,
    )
    if any(count < 0 for count in counts):
      raise ValueError(f"negative count in {counts}")
    if self.numerator > self.denominator:
      raise ValueError(
          f"numerator {self.numerator} exceeds denominator {self.denominator}"
      )
    if self.unclassified > self.denominator:
      raise ValueError(
          f"{self.unclassified} unclassified exceeds the {self.denominator}"
          " runs counted; unclassified runs are inside the denominator"
      )

  @property
  def estimable(self) -> bool:
    """Whether any run was counted, so a fraction means anything at all.

    A batch whose runs were **all** ours is a real batch — the infrastructure
    failed on every one of them — and it has no rate. Reporting it as ``0 / 0``
    would render our own total failure as an actor that solved nothing, which
    is the exact substitution ADR-0015 and ADR-0016 exist to prevent.

    Returns:
      Whether the denominator is non-zero.
    """
    return self.denominator > 0

  def render(self, label: str) -> str:
    """Return the one reportable line for this rate.

    The only rendering there is, so the counts cannot be dropped by a caller
    that only wanted the number.

    **Never give this method a boolean.** A flag like ``concise=True`` is how
    the counts come off again: the name "the rate's report line" would then
    mean two different things, one of them without them. If a shorter form is
    ever genuinely needed it is a **different function returning a different
    type**, so that this name keeps pointing at the thing that carries its
    counts.

    Args:
      label: What the numerator counts, e.g. ``"resolved"``.

    Returns:
      A line of the form
      ``resolved 12 / 40 (3 system failures excluded, 2 unclassified)``.
    """
    counts = (
        f"({self.excluded} system failures excluded,"
        f" {self.unclassified} unclassified)"
    )
    if not self.estimable:
      # The counts still print: they are the whole content of this line.
      return f"{label} not estimable — 0 runs counted {counts}"
    return f"{label} {self.numerator} / {self.denominator} {counts}"


def rate_of(runs: Iterable[tuple[RolloutOutcome, bool]], /) -> Rate:
  """Count a batch of finished runs into a reportable rate.

  Args:
    runs: One ``(outcome, met_the_condition)`` pair per run. The flag is only
      consulted for runs that count — a run excluded as ours contributes to
      :attr:`Rate.excluded` and to nothing else, so a system failure can never
      raise or lower the rate it is excluded from.

  Returns:
    The rate, carrying its two counts.
  """
  numerator = denominator = excluded = unclassified = 0
  for outcome, met in runs:
    if not outcome.counts_in_denominator:
      excluded += 1
      continue
    denominator += 1
    if outcome.unclassified:
      unclassified += 1
    if met:
      numerator += 1
  return Rate(
      numerator=numerator,
      denominator=denominator,
      excluded=excluded,
      unclassified=unclassified,
  )
