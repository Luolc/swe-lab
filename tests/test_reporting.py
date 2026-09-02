"""Tests for reporting a rate together with the counts that make it readable.

The contract is ADR-0015 §5 plus ADR-0016: every rate is reported with the
number of runs excluded as ours **and** the number nobody could attribute. Both
ADRs recorded it as a promise about a reporter that did not exist. These tests
are what turn it into something that fails when it is broken.
"""

from __future__ import annotations

import pytest

from swe_lab.reporting import Rate, rate_of
from swe_lab.rollout import RolloutOutcome


def _batch() -> list[tuple[RolloutOutcome, bool]]:
  """Build a batch with both kinds of oddity: one alone proves less."""
  return (
      [(RolloutOutcome.PATCH_PRODUCED, True)] * 12
      + [(RolloutOutcome.NO_PATCH, False)] * 26
      + [(RolloutOutcome.SYSTEM_FAILED, False)] * 2
      + [(RolloutOutcome.OOM_KILLED, False)]
      + [(RolloutOutcome.UNCLASSIFIED, False)] * 2
  )


def test_a_reported_rate_carries_both_of_its_counts():
  """The whole contract, as the one line a reader actually sees.

  Failure condition, stated the way this repo requires: **delete either count
  from the rendering and this test goes red.** The rate and the counts are one
  value with one rendering, so a report that dropped a count is not a
  discipline someone has to keep — it is a value that cannot be built.
  """
  rate = rate_of(_batch())
  assert rate.render("resolved") == (
      "resolved 12 / 40 (3 system failures excluded, 2 unclassified)"
  )


def test_a_run_that_was_ours_moves_neither_half_of_the_fraction():
  """An excluded run must not be able to raise or lower the rate it left.

  It is counted once, as an exclusion, and contributes to nothing else — so a
  bad night on the infrastructure cannot flatter or depress a result.
  """
  clean = rate_of([(RolloutOutcome.PATCH_PRODUCED, True)] * 3)
  assert (clean.numerator, clean.denominator) == (3, 3)

  with_ours = rate_of(
      [(RolloutOutcome.PATCH_PRODUCED, True)] * 3
      + [(RolloutOutcome.SYSTEM_FAILED, True)]  # "met" is ignored for ours
      + [(RolloutOutcome.OOM_KILLED, False)]
  )
  assert (with_ours.numerator, with_ours.denominator) == (3, 3)
  assert with_ours.excluded == 2


def test_an_unclassified_run_is_inside_the_denominator_and_still_visible():
  """The point of the second count: it is *not* an exclusion.

  ADR-0015 §4 keeps an unattributed ending in, because that can only understate
  a rate. ADR-0016 adds the count so that keeping it in does not make it
  silent — the two facts have to hold at once.
  """
  rate = rate_of(
      [(RolloutOutcome.PATCH_PRODUCED, True)]
      + [(RolloutOutcome.UNCLASSIFIED, False)] * 2
  )
  assert rate.denominator == 3  # in, not excluded
  assert rate.excluded == 0
  assert rate.unclassified == 2
  assert "2 unclassified" in rate.render("resolved")


def test_the_counts_are_reported_when_they_are_zero():
  """A clean batch and an unreported one must not read the same.

  A parenthetical that appears only when non-zero makes a clean batch and an
  unreported one look identical, which is the silence these ADRs are about.
  """
  rate = rate_of([(RolloutOutcome.PATCH_PRODUCED, True)] * 5)
  assert rate.render("resolved") == (
      "resolved 5 / 5 (0 system failures excluded, 0 unclassified)"
  )


def test_which_runs_leave_is_read_off_the_outcome_not_a_list_here():
  """The reporter must not keep its own idea of which endings are ours.

  A second list would be a second thing to update, and it would disagree
  silently the first time a word is added. Stated over every member, so a new
  one has to satisfy it rather than fall off the end of a literal.
  """
  for outcome in RolloutOutcome:
    rate = rate_of([(outcome, True)])
    if outcome.ours:
      assert (rate.denominator, rate.excluded) == (0, 1), outcome
    else:
      assert (rate.denominator, rate.excluded) == (1, 0), outcome
      assert rate.unclassified == (1 if outcome.unclassified else 0), outcome


def test_a_rate_that_could_not_describe_a_batch_is_refused():
  # Cheap guards against a caller assembling one by hand and reporting
  # something arithmetically impossible.
  with pytest.raises(ValueError, match="exceeds the"):
    _ = Rate(numerator=1, denominator=2, excluded=0, unclassified=3)
  with pytest.raises(ValueError, match="exceeds denominator"):
    _ = Rate(numerator=3, denominator=2, excluded=0, unclassified=0)
  with pytest.raises(ValueError, match="negative"):
    _ = Rate(numerator=1, denominator=2, excluded=-1, unclassified=0)


def test_a_batch_that_was_all_ours_has_no_rate_and_says_so():
  """A batch with nothing counted has no rate, and must not print one.

  ``0 / 0`` is not a zero. A batch whose runs were every one of them ours is a
  real batch, and rendering it as a fraction reports a total infrastructure
  failure as an actor that solved nothing — the substitution ADR-0015 and
  ADR-0016 forbid. The counts stay, because in that line they are the whole
  content.
  """
  rate = rate_of([(RolloutOutcome.SYSTEM_FAILED, False)] * 5)
  assert rate.estimable is False
  assert rate.render("resolved") == (
      "resolved not estimable — 0 runs counted"
      " (5 system failures excluded, 0 unclassified)"
  )
  # The number that would have been reported as a rate is not in the line.
  assert "0 / 0" not in rate.render("resolved")


def test_an_empty_batch_is_not_estimable_either():
  # Nothing ran, so there is nothing to be a rate of — and it must not read as
  # a perfect or a zero score.
  rate = rate_of([])
  assert rate.estimable is False
  assert "not estimable" in rate.render("resolved")
