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

Both verdicts of a pair come from **one invocation** of the workflow, and the
authority on what one invocation graded is its **workflow record** — the
roll-up ``Workflow.execute`` writes last, naming the run (``run_ts``) and, per
entry, the final attempt's metrics. The attempt shards are not that authority:
a store keeps every shard it was given, so a forced re-run (``resume=False``,
the CLI's default) overwrites ``a0`` and leaves an older run's ``a1`` behind,
and a re-run that stopped early leaves the previous run's downstream shards
beside its own fresh upstream ones. Read by highest attempt number, the first
pairs a verdict with a run that no longer exists and the second grades a
guidebook the current run never wrote. So the shards only say **which runs
exist**; every verdict is read off the run's record, the same way the task
runner reads its own terminal marker rather than the last shard on disk.

The record has to be the **current** one, and a record's presence does not
prove that: nothing clears the previous invocation's ``workflow.json`` when a
``resume=False`` run starts — ``Workflow.execute`` overwrites it last — so a
run killed after its fresh shards landed and before its record leaves new
shards under an old record that describes a run which no longer exists. Two
tests establish currency, both from what the store already holds:

1. **No shard under the run postdates the record.** ``run_ts`` is sortable
   by construction (``persist_wiring.run_ts``), and a shard written by a
   later invocation sorts after a record that predates it. Deliberately not
   "every shard's ``run_ts`` equals the record's": a ``resume=True``
   invocation legitimately rolls up resumed entries whose shards carry an
   older ``run_ts``, and a completed forced re-run legitimately sits beside
   outlived older attempts — both are *older* than their record.
2. **The record agrees with the shards it rolls up.** The clock has
   one-second grain, so two invocations can share a ``run_ts`` and the first
   test cannot tell them apart. But the record copies each entry's final
   attempt — its metrics and artifact keys — and a later invocation that
   rewrote that attempt's shard left one that no longer says what the record
   says. For every entry the record ran, the shard at its final attempt must
   exist and match; otherwise the record is a different invocation's.

What the second test cannot see is a re-run whose every landed shard is
identical, metric for metric, to the one it replaced — and for those entries
the record's answer is the re-run's answer too. The residue is a re-run
launched in the same second as the run that wrote the record, killed after
landing only such shards: its unreached entries read as the record's. Closing
that needs a per-invocation generation signal on the shards and the record,
which changes their shape and is not done here.

A run either grading entry left no verdict for is **incomplete**, counted and
named rather than dropped: "not graded" and "graded as failing" are different
facts, and only one of them is a zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any

from swe_lab.sandbox import AttemptRecord, SandboxError, Store
from swe_lab.workflow.workflow import WORKFLOW_RECORD_NAME

# The metric every evaluation method reports its answer under (`<method>.
# resolved`) — read by suffix, as the CLI's exit code does, so the reading does
# not depend on which grader an entry composed.
_RESOLVED_SUFFIX = ".resolved"
# The workflow record's word for an entry whose final attempt was valid; a
# grading that ended any other way produced no verdict, whatever its metrics.
_SUCCEEDED = "succeeded"
# What an incomplete run lacks when its record is present but older than a
# shard under the run: the invocation those shards belong to never wrote one.
STALE_RECORD = f"{WORKFLOW_RECORD_NAME} predates shards"
# What it lacks when the record's roll-up of an entry's final attempt does not
# match the shard at that attempt: a later invocation rewrote the shard.
DISAGREEING_RECORD = f"{WORKFLOW_RECORD_NAME} disagrees with shards"


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
  """One run's two verdicts, both from the same workflow invocation.

  Attributes:
    instance_id: The instance.
    rollout_id: Which sample of it — one pair per ``(instance, rollout)``, so
      a sweep with one rollout per instance reads as one pair per instance.
    baseline_pass: The blind grading entry's verdict.
    guided_pass: The guided grading entry's verdict.
    run_ts: The invocation both verdicts come from — the workflow record's
      launch timestamp, so a reader can tell which run a pair describes.
  """

  instance_id: str
  rollout_id: int
  baseline_pass: bool
  guided_pass: bool
  run_ts: str

  @property
  def cell(self) -> Cell:
    """The 2×2 cell this pair falls in."""
    return cell_of(
        baseline_pass=self.baseline_pass, guided_pass=self.guided_pass
    )


@dataclass(frozen=True)
class IncompleteRun:
  """A run its workflow record cannot give both verdicts for.

  Attributes:
    instance_id: The instance.
    rollout_id: Which sample of it.
    missing: What the run lacks: the grading keys with no verdict in the
      record — an entry that never ran, ended failed, or reported no
      ``*.resolved`` — or the record's own name (``workflow.json``) when the
      run left shards but no record, because it never reached the end of an
      invocation; :data:`STALE_RECORD` (``workflow.json predates shards``)
      when a record is there but is older than a shard under the run; or
      :data:`DISAGREEING_RECORD` (``workflow.json disagrees with shards``)
      when the record's roll-up of an entry's final attempt does not match
      the shard at that attempt. The last two are an earlier invocation's
      record, left in place by a later one that was killed before writing its
      own.
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
    incomplete: Every ``(instance, rollout)`` whose record gives no verdict
      for a grading key, or that has no current record. Never folded into a
      cell.
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
      The header, every pair with its cell and its run, the four counts, the
      two named marginals, and every incomplete run with what it lacks.
    """
    return {
        "sweep_id": self.sweep_id,
        "baseline_key": self.baseline_key,
        "guided_key": self.guided_key,
        "runs": [
            {
                "instance_id": run.instance_id,
                "rollout_id": run.rollout_id,
                "run_ts": run.run_ts,
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
        f"  {run.instance_id} r{run.rollout_id}:"
        f" {', '.join(_lack(name) for name in run.missing)}"
        for run in self.incomplete
    )
    return "\n".join(lines) + "\n"


def _lack(name: str) -> str:
  """Phrase one entry of ``IncompleteRun.missing`` for the table."""
  if name in (STALE_RECORD, DISAGREEING_RECORD):
    return name
  return f"missing {name}"


def _runs_in(
    records: Iterable[AttemptRecord],
) -> dict[tuple[str, int], list[AttemptRecord]]:
  """Group a sweep's shards by the ``(instance, rollout)`` run they belong to.

  Args:
    records: The sweep's attempt records, any order.

  Returns:
    Every distinct run, in the shards' identity order, with its shards.
  """
  runs: dict[tuple[str, int], list[AttemptRecord]] = {}
  for record in sorted(records, key=lambda r: r.sort_key):
    runs.setdefault((record.instance_id, record.rollout_id), []).append(record)
  return runs


def _predates(
    record: Mapping[str, Any], shards: Iterable[AttemptRecord]
) -> bool:
  """Whether a shard under the run was written by a later invocation.

  Args:
    record: The run's workflow record.
    shards: Every attempt shard under the run.

  Returns:
    ``True`` when any shard's ``run_ts`` sorts after the record's — the record
    belongs to an earlier invocation than the shards do.
  """
  run_ts = str(record["run_ts"])
  return any(shard.run_ts > run_ts for shard in shards)


def _disagrees(
    record: Mapping[str, Any], shards: Iterable[AttemptRecord]
) -> bool:
  """Whether the record's roll-up no longer matches the shards it rolls up.

  The record copies, per entry, the final attempt's metrics and artifact keys
  (``Workflow._write_record``). A later invocation that rewrote that attempt
  — a forced re-run launched in the same second, so the timestamp test is
  blind to it — left a shard that says something else, or none at all.

  Args:
    record: The run's workflow record.
    shards: Every attempt shard under the run.

  Returns:
    ``True`` when some entry the record ran has no shard at its final
    attempt, or one whose metrics or artifact keys differ from the record's.
  """
  by_attempt = {(shard.task, shard.attempt): shard for shard in shards}
  for entry in record.get("entries", []):
    attempts = int(entry.get("attempts", 0))
    if attempts == 0:
      continue  # never ran; nothing in the store to agree with
    shard = by_attempt.get((str(entry["key"]), attempts - 1))
    if shard is None:
      return True
    if dict(shard.metrics) != dict(entry.get("metrics", {})):
      return True
    if dict(shard.artifact_keys) != dict(entry.get("artifact_keys", {})):
      return True
  return False


def _workflow_record(
    store: Store, sweep_id: str, instance_id: str, rollout_id: int
) -> Mapping[str, Any] | None:
  """Read one run's workflow record, or ``None`` when the run left none.

  Args:
    store: The store the sweep persisted through.
    sweep_id: The sweep.
    instance_id: The instance.
    rollout_id: Which sample of it.

  Returns:
    The parsed record, or ``None`` — the run never reached the end of an
    invocation (killed mid-way, or still running).
  """
  key = f"{sweep_id}/{instance_id}/r{rollout_id}/{WORKFLOW_RECORD_NAME}"
  try:
    body = store.get_bytes(key)
  except SandboxError:
    return None
  return json.loads(body)


def _verdict(entry: Mapping[str, Any] | None) -> bool | None:
  """Read a grading entry's answer off the workflow record.

  Args:
    entry: The entry's roll-up in the record; ``None`` when the record has
      no entry under the grading key.

  Returns:
    Whether every ``*.resolved`` metric of the entry's final attempt says
    resolved; ``None`` when there is no such verdict — the entry is absent,
    never ran, ended failed (an invalid final attempt is not a grading), or
    reported no ``*.resolved``.
  """
  if entry is None or entry.get("status") != _SUCCEEDED:
    return None
  metrics: Mapping[str, float] = entry.get("metrics", {})
  answers = [
      value
      for name, value in metrics.items()
      if name.endswith(_RESOLVED_SUFFIX)
  ]
  if not answers:
    return None
  return all(value >= 1.0 for value in answers)


def guided_gain(
    store: Store,
    *,
    sweep_id: str,
    baseline_key: str,
    guided_key: str,
) -> GuidedGain:
  """Take the 2×2 reading over a sweep in the store.

  Args:
    store: The store the sweep's runs persisted through.
    sweep_id: The sweep, for discovering its runs and for the header.
    baseline_key: The entry key of the blind grading.
    guided_key: The entry key of the guided grading.

  Returns:
    The reading: one pair per run whose workflow record grades both, and
    every run it could not place, with what that run lacks.
  """
  runs: list[RunPair] = []
  incomplete: list[IncompleteRun] = []
  runs_in_store = _runs_in(store.read_manifests(sweep_id))
  for (instance_id, rollout_id), shards in runs_in_store.items():
    record = _workflow_record(store, sweep_id, instance_id, rollout_id)
    if record is None:
      incomplete.append(
          IncompleteRun(instance_id, rollout_id, (WORKFLOW_RECORD_NAME,))
      )
      continue
    if _predates(record, shards):
      incomplete.append(IncompleteRun(instance_id, rollout_id, (STALE_RECORD,)))
      continue
    if _disagrees(record, shards):
      incomplete.append(
          IncompleteRun(instance_id, rollout_id, (DISAGREEING_RECORD,))
      )
      continue
    entries: dict[str, Mapping[str, Any]] = {
        entry["key"]: entry for entry in record.get("entries", [])
    }
    verdicts = {
        key: _verdict(entries.get(key)) for key in (baseline_key, guided_key)
    }
    missing = tuple(key for key, answer in verdicts.items() if answer is None)
    if missing:
      incomplete.append(IncompleteRun(instance_id, rollout_id, missing))
      continue
    runs.append(
        RunPair(
            instance_id=instance_id,
            rollout_id=rollout_id,
            baseline_pass=verdicts[baseline_key] is True,
            guided_pass=verdicts[guided_key] is True,
            run_ts=str(record["run_ts"]),
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
    "DISAGREEING_RECORD",
    "GuidedGain",
    "IncompleteRun",
    "RunPair",
    "STALE_RECORD",
    "cell_of",
    "guided_gain",
]
