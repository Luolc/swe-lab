"""The from-scratch chain's reading: a 2×2 over the blind and guided verdicts.

``from_scratch_guided_trace`` grades an instance twice — once after a blind
rollout, once after a rollout the Oracle's guidebook supervised — so the
question it exists to answer is a pairing, not a rate: for each run, did the
blind attempt pass, and did the guided one? Four cells fall out, and two of
them are the numbers the chain is run for: how many instances were **solved at
baseline** (the first roll was already right, whatever happened next) and how
many were **gained with the guidebook** (wrong blind, right guided). The other
two — kept, and regressed — are what makes the chain unconditional: a chain
that skipped the guided half whenever the blind roll passed could never show
the guidebook making things worse.

The reading is taken off the sweep's persisted attempt records
(``Store.read_manifests``), one pair per ``(instance, rollout)``, from the
**final** attempt of each grading entry — the attempt that decided the
verdict, exactly as the runner's terminal outcome does. A pair either grading
entry left no verdict for is **incomplete**, counted and named rather than
dropped: "not graded" and "graded as failing" are different facts, and only
one of them is a zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from swe_lab.sandbox import AttemptRecord

# The metric every evaluation method reports its answer under (`<method>.
# resolved`) — read by suffix, as the CLI's exit code does, so the reading does
# not depend on which grader an entry composed.
_RESOLVED_SUFFIX = ".resolved"


class Cell(StrEnum):
  """Where one ``(baseline, guided)`` verdict pair lands.

  Attributes:
    KEPT: Solved blind, and solved again under the guidebook.
    GAINED: Unsolved blind, solved under the guidebook — the gain.
    REGRESSED: Solved blind, unsolved under the guidebook — the cell that
      running the chain unconditionally exists to make observable.
    UNSOLVED: Unsolved both times.
  """

  KEPT = "kept"
  GAINED = "gained"
  REGRESSED = "regressed"
  UNSOLVED = "unsolved"


def cell_of(*, baseline_pass: bool, guided_pass: bool) -> Cell:
  """Place one verdict pair.

  Args:
    baseline_pass: Whether the blind rollout's patch resolved the instance.
    guided_pass: Whether the guided rollout's patch did.

  Returns:
    The cell.
  """
  match (baseline_pass, guided_pass):
    case (True, True):
      return Cell.KEPT
    case (False, True):
      return Cell.GAINED
    case (True, False):
      return Cell.REGRESSED
    case _:
      return Cell.UNSOLVED


@dataclass(frozen=True)
class RunPair:
  """One run's two verdicts.

  Attributes:
    instance_id: The instance.
    rollout_id: Which sample of it — one pair per ``(instance, rollout)``, so
      a sweep with one rollout per instance reads as one pair per instance.
    baseline_pass: The blind grading entry's verdict.
    guided_pass: The guided grading entry's verdict.
  """

  instance_id: str
  rollout_id: int
  baseline_pass: bool
  guided_pass: bool

  @property
  def cell(self) -> Cell:
    """The 2×2 cell this pair falls in."""
    return cell_of(
        baseline_pass=self.baseline_pass, guided_pass=self.guided_pass
    )


@dataclass(frozen=True)
class IncompleteRun:
  """A run one or both grading entries left no verdict for.

  Attributes:
    instance_id: The instance.
    rollout_id: Which sample of it.
    missing: The grading keys with no verdict — no attempt record at all, or
      a record whose metrics carry no ``*.resolved`` (grading never ran).
  """

  instance_id: str
  rollout_id: int
  missing: tuple[str, ...]


@dataclass(frozen=True)
class GuidedGain:
  """The 2×2 reading of one sweep, with what it could not place.

  Attributes:
    sweep_id: The sweep the records came from.
    baseline_key: The entry key of the blind grading.
    guided_key: The entry key of the guided grading.
    runs: Every pair both entries graded, in store order.
    incomplete: Every ``(instance, rollout)`` a grading key left no verdict
      for. Never folded into a cell.
  """

  sweep_id: str
  baseline_key: str
  guided_key: str
  runs: tuple[RunPair, ...]
  incomplete: tuple[IncompleteRun, ...]

  def cells(self) -> dict[Cell, int]:
    """Count the pairs per cell — every cell present, zero when empty."""
    counts = dict.fromkeys(Cell, 0)
    for run in self.runs:
      counts[run.cell] += 1
    return counts

  @property
  def solved_at_baseline(self) -> int:
    """How many pairs the blind roll already solved (kept + regressed)."""
    return sum(run.baseline_pass for run in self.runs)

  @property
  def gained_with_guidebook(self) -> int:
    """How many pairs only the guided roll solved (the ``gained`` cell)."""
    return self.cells()[Cell.GAINED]

  def to_json(self) -> dict[str, object]:
    """Return the reading as a JSON-ready object.

    Returns:
      The header, every pair with its cell, the four counts, the two named
      marginals, and every incomplete run with what it lacks.
    """
    return {
        "sweep_id": self.sweep_id,
        "baseline_key": self.baseline_key,
        "guided_key": self.guided_key,
        "runs": [
            {
                "instance_id": run.instance_id,
                "rollout_id": run.rollout_id,
                "baseline_pass": run.baseline_pass,
                "guided_pass": run.guided_pass,
                "cell": run.cell.value,
            }
            for run in self.runs
        ],
        "cells": {cell.value: count for cell, count in self.cells().items()},
        "solved_at_baseline": self.solved_at_baseline,
        "gained_with_guidebook": self.gained_with_guidebook,
        "incomplete": [
            {
                "instance_id": run.instance_id,
                "rollout_id": run.rollout_id,
                "missing": list(run.missing),
            }
            for run in self.incomplete
        ],
    }

  def render(self) -> str:
    """Return the reading as one table a person reads.

    The two marginals the chain is run for are spelled out under the grid —
    *solved at baseline* and *gained with the guidebook* — so nobody adds
    cells by hand, and the incomplete count is printed even when it is zero:
    "none incomplete" and "incompleteness not reported" must not look alike.

    Returns:
      The table, newline-terminated lines.
    """
    cells = self.cells()
    total = len(self.runs)
    lines = [
        f"sweep {self.sweep_id}: {total} (instance, rollout) pair(s) graded"
        f" by both {self.baseline_key} and {self.guided_key},"
        f" {len(self.incomplete)} incomplete",
        f"{'':<16}{'guided pass':<18}{'guided fail':<18}",
        f"{'baseline pass':<16}"
        f"{'kept ' + str(cells[Cell.KEPT]):<18}"
        f"{'regressed ' + str(cells[Cell.REGRESSED]):<18}",
        f"{'baseline fail':<16}"
        f"{'gained ' + str(cells[Cell.GAINED]):<18}"
        f"{'unsolved ' + str(cells[Cell.UNSOLVED]):<18}",
        f"solved at baseline (kept + regressed): {self.solved_at_baseline}"
        f" / {total}",
        "gained with the guidebook (baseline fail, guided pass):"
        f" {self.gained_with_guidebook} / {total}",
        "regressed with the guidebook (baseline pass, guided fail):"
        f" {cells[Cell.REGRESSED]} / {total}",
        f"incomplete, not counted above: {len(self.incomplete)}",
    ]
    lines.extend(
        f"  {run.instance_id} r{run.rollout_id}: missing"
        f" {', '.join(run.missing)}"
        for run in self.incomplete
    )
    return "\n".join(lines) + "\n"


def _verdict(record: AttemptRecord) -> bool | None:
  """Read a grading record's answer, or ``None`` when it recorded none.

  Args:
    record: The grading entry's final attempt.

  Returns:
    Whether every ``*.resolved`` metric says resolved; ``None`` when the
    record carries no such metric, which is a grading that never produced a
    verdict rather than one that said no.
  """
  answers = [
      value
      for name, value in record.metrics.items()
      if name.endswith(_RESOLVED_SUFFIX)
  ]
  if not answers:
    return None
  return all(value >= 1.0 for value in answers)


def _final_attempts(
    records: Iterable[AttemptRecord],
) -> dict[tuple[str, int], dict[str, AttemptRecord]]:
  """Group a sweep's records into ``(instance, rollout) → task → last attempt``.

  The last persisted attempt is the one whose verdict the runner reported:
  every attempt persists before the terminal marker, and the marker's outcome
  is the final attempt's.

  Args:
    records: The sweep's attempt records, any order.

  Returns:
    The final attempt of every task of every run.
  """
  grouped: dict[tuple[str, int], dict[str, AttemptRecord]] = {}
  for record in sorted(records, key=lambda r: r.sort_key):
    grouped.setdefault((record.instance_id, record.rollout_id), {})[
        record.task
    ] = record  # sorted by attempt, so the last write is the last attempt
  return grouped


def guided_gain(
    records: Iterable[AttemptRecord],
    *,
    sweep_id: str,
    baseline_key: str,
    guided_key: str,
) -> GuidedGain:
  """Take the 2×2 reading over a sweep's attempt records.

  Args:
    records: Every attempt record under the sweep (``read_manifests``).
    sweep_id: The sweep, for the reading's header.
    baseline_key: The entry key of the blind grading.
    guided_key: The entry key of the guided grading.

  Returns:
    The reading: one pair per run both entries graded, and every run one of
    them did not.
  """
  runs: list[RunPair] = []
  incomplete: list[IncompleteRun] = []
  for (instance_id, rollout_id), tasks in _final_attempts(records).items():
    verdicts: Mapping[str, bool | None] = {
        key: _verdict(tasks[key]) if key in tasks else None
        for key in (baseline_key, guided_key)
    }
    missing = tuple(key for key, answer in verdicts.items() if answer is None)
    if missing:
      incomplete.append(IncompleteRun(instance_id, rollout_id, missing))
      continue
    baseline_pass, guided_pass = (
        verdicts[baseline_key] is True,
        verdicts[guided_key] is True,
    )
    runs.append(
        RunPair(
            instance_id=instance_id,
            rollout_id=rollout_id,
            baseline_pass=baseline_pass,
            guided_pass=guided_pass,
        )
    )
  return GuidedGain(
      sweep_id=sweep_id,
      baseline_key=baseline_key,
      guided_key=guided_key,
      runs=tuple(runs),
      incomplete=tuple(incomplete),
  )


__all__ = [
    "Cell",
    "GuidedGain",
    "IncompleteRun",
    "RunPair",
    "cell_of",
    "guided_gain",
]
