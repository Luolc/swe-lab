"""The 2×2 reading over a from-scratch sweep's attempt records.

Literal fixture records, one per cell and two kinds of incompleteness, so each
assertion says which record it is about. Failure conditions, stated the way
this repo requires: fold an incomplete run into a cell and the counts move;
read the first attempt instead of the last and a retried pass reads as a fail;
drop a zero cell from the table and the render assertion goes red.
"""

from __future__ import annotations

import json
from pathlib import Path

from etils import epath
from typer.testing import CliRunner

from swe_lab.cli import app
from swe_lab.sandbox import AttemptRecord, FilesystemStore
from swe_lab.trace_synthesis.guided_gain import (
    Cell,
    guided_gain,
    IncompleteRun,
    RunPair,
)

BASELINE = "baseline_unit_test"
GUIDED = "guided_unit_test"

runner = CliRunner()


def _record(
    instance_id: str,
    task: str,
    *,
    rollout_id: int = 0,
    attempt: int = 0,
    resolved: float | None,
) -> AttemptRecord:
  """One grading attempt's shard; ``resolved=None`` means no verdict at all."""
  metrics = {} if resolved is None else {"unit_test.resolved": resolved}
  return AttemptRecord(
      sweep_id="sw",
      instance_id=instance_id,
      task=task,
      rollout_id=rollout_id,
      attempt=attempt,
      run_ts="ts-0",
      status="success",
      tier="formal",
      backend="fake",
      metrics={**metrics, "unit_test.score": resolved or 0.0},
  )


def _sweep() -> list[AttemptRecord]:
  """One run per cell, plus two runs the reading must not place."""
  return [
      # kept: solved blind, solved guided
      _record("kept", BASELINE, resolved=1.0),
      _record("kept", GUIDED, resolved=1.0),
      # gained: unsolved blind, solved guided
      _record("gained", BASELINE, resolved=0.0),
      _record("gained", GUIDED, resolved=1.0),
      # regressed: solved blind, unsolved guided
      _record("regressed", BASELINE, resolved=1.0),
      _record("regressed", GUIDED, resolved=0.0),
      # unsolved: neither
      _record("unsolved", BASELINE, resolved=0.0),
      _record("unsolved", GUIDED, resolved=0.0),
      # incomplete, first kind: the guided grading never left a record
      _record("no-guided-record", BASELINE, resolved=0.0),
      # incomplete, second kind: a record exists, but grading produced no
      # verdict (a setup failure persists a shard with no *.resolved)
      _record("ungraded", BASELINE, resolved=1.0),
      _record("ungraded", GUIDED, resolved=None),
  ]


def test_the_four_cells_and_both_kinds_of_incomplete_are_told_apart():
  reading = guided_gain(
      _sweep(), sweep_id="sw", baseline_key=BASELINE, guided_key=GUIDED
  )

  assert reading.runs == (
      RunPair("gained", 0, baseline_pass=False, guided_pass=True),
      RunPair("kept", 0, baseline_pass=True, guided_pass=True),
      RunPair("regressed", 0, baseline_pass=True, guided_pass=False),
      RunPair("unsolved", 0, baseline_pass=False, guided_pass=False),
  )
  assert reading.cells() == {
      Cell.KEPT: 1,
      Cell.GAINED: 1,
      Cell.REGRESSED: 1,
      Cell.UNSOLVED: 1,
  }
  # the two numbers the chain is run for, named rather than left to be added
  assert reading.solved_at_baseline == 2  # kept + regressed
  assert reading.gained_with_guidebook == 1
  # neither incomplete run is in any cell, and each says what it lacks
  assert reading.incomplete == (
      IncompleteRun("no-guided-record", 0, missing=(GUIDED,)),
      IncompleteRun("ungraded", 0, missing=(GUIDED,)),
  )


def test_the_json_carries_every_pair_its_cell_and_the_named_marginals():
  payload = guided_gain(
      _sweep(), sweep_id="sw", baseline_key=BASELINE, guided_key=GUIDED
  ).to_json()

  assert payload["sweep_id"] == "sw"
  assert (payload["baseline_key"], payload["guided_key"]) == (
      BASELINE,
      GUIDED,
  )
  assert payload["cells"] == {
      "kept": 1,
      "gained": 1,
      "regressed": 1,
      "unsolved": 1,
  }
  assert payload["solved_at_baseline"] == 2
  assert payload["gained_with_guidebook"] == 1
  runs = payload["runs"]
  assert isinstance(runs, list)
  assert {run["instance_id"]: run["cell"] for run in runs} == {
      "kept": "kept",
      "gained": "gained",
      "regressed": "regressed",
      "unsolved": "unsolved",
  }
  assert payload["incomplete"] == [
      {"instance_id": "no-guided-record", "rollout_id": 0, "missing": [GUIDED]},
      {"instance_id": "ungraded", "rollout_id": 0, "missing": [GUIDED]},
  ]
  # a plain JSON document, no enum or dataclass leaking through
  assert json.loads(json.dumps(payload)) == payload


def test_the_table_names_the_two_marginals_and_lists_the_incomplete():
  table = guided_gain(
      _sweep(), sweep_id="sw", baseline_key=BASELINE, guided_key=GUIDED
  ).render()

  assert "4 (instance, rollout) pair(s) graded by both" in table
  assert "kept 1" in table and "regressed 1" in table
  assert "gained 1" in table and "unsolved 1" in table
  assert "solved at baseline (kept + regressed): 2 / 4" in table
  assert "gained with the guidebook (baseline fail, guided pass): 1 / 4" in (
      table
  )
  assert "incomplete, not counted above: 2" in table
  assert "no-guided-record r0: missing guided_unit_test" in table
  assert "ungraded r0: missing guided_unit_test" in table


def test_an_ungraded_run_is_incomplete_not_unsolved():
  """A record with no verdict is an absence, and an absence is not a zero.

  A grading entry whose sandbox never came up still persists a shard, with no
  ``*.resolved`` on it. Reading that as "did not resolve" would put an
  infrastructure failure in the ``unsolved`` cell — the substitution the
  incomplete count exists to prevent — so the discriminating assertion is
  that the cell stays empty.
  """
  reading = guided_gain(
      [
          _record("x", BASELINE, resolved=0.0),
          _record("x", GUIDED, resolved=None),
      ],
      sweep_id="sw",
      baseline_key=BASELINE,
      guided_key=GUIDED,
  )
  assert reading.runs == ()
  assert reading.cells()[Cell.UNSOLVED] == 0
  assert reading.incomplete == (IncompleteRun("x", 0, missing=(GUIDED,)),)


def test_the_final_attempt_decides_as_it_does_for_the_runner():
  # A flaky suite: attempt 0 unresolved, attempt 1 resolved. The runner's
  # terminal outcome is the last attempt's, so the reading agrees with it.
  reading = guided_gain(
      [
          _record("x", BASELINE, attempt=0, resolved=0.0),
          _record("x", BASELINE, attempt=1, resolved=1.0),
          _record("x", GUIDED, resolved=1.0),
      ],
      sweep_id="sw",
      baseline_key=BASELINE,
      guided_key=GUIDED,
  )
  assert [run.cell for run in reading.runs] == [Cell.KEPT]


def test_two_rollouts_of_one_instance_are_two_pairs():
  reading = guided_gain(
      [
          _record("x", BASELINE, rollout_id=0, resolved=1.0),
          _record("x", GUIDED, rollout_id=0, resolved=1.0),
          _record("x", BASELINE, rollout_id=1, resolved=0.0),
          _record("x", GUIDED, rollout_id=1, resolved=1.0),
      ],
      sweep_id="sw",
      baseline_key=BASELINE,
      guided_key=GUIDED,
  )
  assert [(run.rollout_id, run.cell) for run in reading.runs] == [
      (0, Cell.KEPT),
      (1, Cell.GAINED),
  ]


def test_every_cell_and_the_incomplete_count_print_even_at_zero():
  # A clean sweep and an unreported one must not read the same.
  table = guided_gain(
      [
          _record("x", BASELINE, resolved=1.0),
          _record("x", GUIDED, resolved=1.0),
      ],
      sweep_id="sw",
      baseline_key=BASELINE,
      guided_key=GUIDED,
  ).render()
  assert "kept 1" in table
  assert "gained 0" in table
  assert "regressed 0" in table
  assert "unsolved 0" in table
  assert "incomplete, not counted above: 0" in table


# ─── the command ─────────────────────────────────────────────────────────────


def test_the_command_reads_a_store_root_and_prints_json_and_the_table(
    tmp_path: Path,
):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  for record in _sweep():
    store.append_manifest(record)

  result = runner.invoke(
      app, ["guided-gain", "sw", "--store-root", str(tmp_path / "store")]
  )

  assert result.exit_code == 0, result.output
  payload = json.loads(result.stdout)
  assert payload["cells"] == {
      "kept": 1,
      "gained": 1,
      "regressed": 1,
      "unsolved": 1,
  }
  assert payload["solved_at_baseline"] == 2
  assert "solved at baseline (kept + regressed): 2 / 4" in result.stderr
  assert "incomplete, not counted above: 2" in result.stderr


def test_a_sweep_with_no_records_is_refused_not_rendered_as_zeros(
    tmp_path: Path,
):
  # "Nothing measured" must not print as four zeros and a clean table.
  result = runner.invoke(
      app, ["guided-gain", "nothing", "--store-root", str(tmp_path / "empty")]
  )
  assert result.exit_code == 1
  assert "has no attempt records" in result.stderr
  assert result.stdout == ""
