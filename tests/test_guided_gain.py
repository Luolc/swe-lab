"""The 2×2 reading over a from-scratch sweep, taken from its workflow records.

Literal fixture runs on a real ``FilesystemStore``, one per cell and one per
kind of incompleteness, so each assertion says which run it is about. Failure
conditions, stated the way this repo requires: fold an incomplete run into a
cell and the counts move; read a verdict off a shard instead of the run's
record and the rerun tests in ``test_from_scratch_guided_trace.py`` go red;
drop a zero cell from the table and the render assertion goes red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from etils import epath
import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
from swe_lab.sandbox import AttemptRecord, FilesystemStore
from swe_lab.trace_synthesis.guided_gain import (
    Cell,
    DISAGREEING_RECORD,
    guided_gain,
    IncompleteRun,
    RunPair,
    STALE_RECORD,
)
from swe_lab.workflow.workflow import WORKFLOW_RECORD_NAME

BASELINE = "baseline_unit_test"
GUIDED = "guided_unit_test"

# What one grading entry did in a run: resolved or not (a float, as the metric
# is), ran and ended failed with no verdict (``None``), or never ran because
# an earlier entry failed (``"blocked"``).
type Grading = float | None | Literal["blocked"]

runner = CliRunner()


def _shard(
    instance_id: str,
    task: str,
    *,
    rollout_id: int,
    run_ts: str,
    resolved: float | None,
) -> AttemptRecord:
  metrics = {} if resolved is None else {"unit_test.resolved": resolved}
  return AttemptRecord(
      sweep_id="sw",
      instance_id=instance_id,
      task=task,
      rollout_id=rollout_id,
      attempt=0,
      run_ts=run_ts,
      status="success" if resolved is not None else "run_error",
      tier="formal",
      backend="fake",
      metrics=metrics,
  )


def _run(
    store: FilesystemStore,
    instance_id: str,
    *,
    baseline: Grading,
    guided: Grading,
    rollout_id: int = 0,
    run_ts: str = "ts-0",
    record: bool = True,
) -> None:
  """Persist one invocation the way the engine leaves it.

  An attempt shard per entry that ran, and the workflow record rolling them
  up — the record's per-entry ``status`` / ``attempts`` / ``metrics`` are what
  the reading consults. ``record=False`` leaves the shards without a record:
  a run killed before it finished.
  """
  entries: list[dict[str, object]] = []
  for key, grading in ((BASELINE, baseline), (GUIDED, guided)):
    if isinstance(grading, str):  # "blocked"
      entries.append(
          {
              "key": key,
              "status": "blocked",
              "attempts": 0,
              "resumed": False,
              "artifact_keys": {},
              "metrics": {},
          }
      )
      continue
    shard = _shard(
        instance_id, key, rollout_id=rollout_id, run_ts=run_ts, resolved=grading
    )
    store.append_manifest(shard)
    entries.append(
        {
            "key": key,
            "status": "succeeded" if grading is not None else "failed",
            "attempts": 1,
            "resumed": False,
            "artifact_keys": {},
            "metrics": dict(shard.metrics),
        }
    )
  if record:
    store.put_bytes(
        f"sw/{instance_id}/r{rollout_id}/{WORKFLOW_RECORD_NAME}",
        json.dumps(
            {
                "sweep_id": "sw",
                "instance_id": instance_id,
                "rollout_id": rollout_id,
                "run_ts": run_ts,
                "succeeded": all(
                    g not in (None, "blocked") for g in (baseline, guided)
                ),
                "entries": entries,
                "edges": {},
            }
        ).encode("utf-8"),
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemStore:
  return FilesystemStore(epath.Path(tmp_path / "store"))


def _sweep(store: FilesystemStore) -> FilesystemStore:
  """One run per cell, plus five runs the reading must not place."""
  _run(store, "kept", baseline=1.0, guided=1.0)
  _run(store, "gained", baseline=0.0, guided=1.0)
  _run(store, "regressed", baseline=1.0, guided=0.0)
  _run(store, "unsolved", baseline=0.0, guided=0.0)
  # incomplete, first kind: the guided grading never ran (an earlier entry
  # failed, so the record lists it blocked with no attempts)
  _run(store, "no-guided-grading", baseline=0.0, guided="blocked")
  # incomplete, second kind: the guided grading ran and ended failed — a shard
  # exists, the record says failed, and there is no verdict on either
  _run(store, "ungraded", baseline=1.0, guided=None)
  # incomplete, third kind: shards, but no workflow record — the run never
  # reached the end of an invocation
  _run(store, "unfinished", baseline=1.0, guided=1.0, record=False)
  # incomplete, fourth kind: a complete old run, then a re-run that landed a
  # fresh baseline shard and was killed before writing its record — the old
  # record is still there and describes a run that no longer exists
  _run(store, "interrupted", baseline=1.0, guided=1.0, run_ts="ts-0")
  _run(
      store,
      "interrupted",
      baseline=0.0,
      guided="blocked",
      run_ts="ts-1",
      record=False,
  )
  # incomplete, fifth kind: the same, but the killed re-run was launched in
  # the same second as the run that wrote the record — no shard postdates it,
  # yet the record's roll-up of the baseline grading no longer matches the
  # shard it points at
  _run(store, "same-second", baseline=1.0, guided=1.0, run_ts="ts-0")
  _run(
      store,
      "same-second",
      baseline=0.0,
      guided="blocked",
      run_ts="ts-0",
      record=False,
  )
  return store


def _reading(store: FilesystemStore):
  return guided_gain(
      store, sweep_id="sw", baseline_key=BASELINE, guided_key=GUIDED
  )


def test_the_four_cells_and_every_kind_of_incomplete_are_told_apart(
    store: FilesystemStore,
):
  reading = _reading(_sweep(store))

  assert reading.runs == (
      RunPair(
          "gained", 0, baseline_pass=False, guided_pass=True, run_ts="ts-0"
      ),
      RunPair("kept", 0, baseline_pass=True, guided_pass=True, run_ts="ts-0"),
      RunPair(
          "regressed", 0, baseline_pass=True, guided_pass=False, run_ts="ts-0"
      ),
      RunPair(
          "unsolved", 0, baseline_pass=False, guided_pass=False, run_ts="ts-0"
      ),
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
  # no incomplete run is in any cell, and each says what it lacks
  assert reading.incomplete == (
      IncompleteRun("interrupted", 0, missing=(STALE_RECORD,)),
      IncompleteRun("no-guided-grading", 0, missing=(GUIDED,)),
      IncompleteRun("same-second", 0, missing=(DISAGREEING_RECORD,)),
      IncompleteRun("unfinished", 0, missing=(WORKFLOW_RECORD_NAME,)),
      IncompleteRun("ungraded", 0, missing=(GUIDED,)),
  )


def test_the_json_carries_every_pair_its_cell_its_run_and_the_marginals(
    store: FilesystemStore,
):
  payload = _reading(_sweep(store)).to_json()

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
  assert {run["instance_id"]: (run["cell"], run["run_ts"]) for run in runs} == {
      "kept": ("kept", "ts-0"),
      "gained": ("gained", "ts-0"),
      "regressed": ("regressed", "ts-0"),
      "unsolved": ("unsolved", "ts-0"),
  }
  assert payload["incomplete"] == [
      {
          "instance_id": "interrupted",
          "rollout_id": 0,
          "missing": [STALE_RECORD],
      },
      {
          "instance_id": "no-guided-grading",
          "rollout_id": 0,
          "missing": [GUIDED],
      },
      {
          "instance_id": "same-second",
          "rollout_id": 0,
          "missing": [DISAGREEING_RECORD],
      },
      {
          "instance_id": "unfinished",
          "rollout_id": 0,
          "missing": [WORKFLOW_RECORD_NAME],
      },
      {"instance_id": "ungraded", "rollout_id": 0, "missing": [GUIDED]},
  ]
  # a plain JSON document, no enum or dataclass leaking through
  assert json.loads(json.dumps(payload)) == payload


def test_the_table_names_the_two_marginals_and_lists_the_incomplete(
    store: FilesystemStore,
):
  table = _reading(_sweep(store)).render()

  assert "4 (instance, rollout) pair(s) graded by both" in table
  assert "kept 1" in table and "regressed 1" in table
  assert "gained 1" in table and "unsolved 1" in table
  assert "solved at baseline (kept + regressed): 2 / 4" in table
  assert "gained with the guidebook (baseline fail, guided pass): 1 / 4" in (
      table
  )
  assert "incomplete, not counted above: 5" in table
  assert "interrupted r0: workflow.json predates shards" in table
  assert "same-second r0: workflow.json disagrees with shards" in table
  assert "no-guided-grading r0: missing guided_unit_test" in table
  assert "unfinished r0: missing workflow.json" in table
  assert "ungraded r0: missing guided_unit_test" in table


def test_an_ungraded_run_is_incomplete_not_unsolved(store: FilesystemStore):
  """A grading that produced no verdict is an absence, and not a zero.

  A grading entry whose sandbox never came up still persists a shard, and the
  record lists it failed with no ``*.resolved``. Reading that as "did not
  resolve" would put an infrastructure failure in the ``unsolved`` cell — the
  substitution the incomplete count exists to prevent — so the discriminating
  assertion is that the cell stays empty.
  """
  _run(store, "x", baseline=0.0, guided=None)
  reading = _reading(store)
  assert reading.runs == ()
  assert reading.cells()[Cell.UNSOLVED] == 0
  assert reading.incomplete == (IncompleteRun("x", 0, missing=(GUIDED,)),)


def test_a_record_without_the_grading_key_at_all_is_incomplete(
    store: FilesystemStore,
):
  # The record is per (sweep, instance, rollout), whatever workflow wrote it:
  # a later `rollout_and_unit_test` run under the same coordinates — its own
  # shard in place, so the record is current — has neither grading key, and
  # must not read as anything but "not this chain's run".
  _run(store, "x", baseline=1.0, guided=1.0)
  store.append_manifest(
      _shard("x", "unit_test", rollout_id=0, run_ts="ts-1", resolved=1.0)
  )
  store.put_bytes(
      f"sw/x/r0/{WORKFLOW_RECORD_NAME}",
      json.dumps(
          {
              "sweep_id": "sw",
              "instance_id": "x",
              "rollout_id": 0,
              "run_ts": "ts-1",
              "succeeded": True,
              "entries": [
                  {
                      "key": "unit_test",
                      "status": "succeeded",
                      "attempts": 1,
                      "resumed": False,
                      "artifact_keys": {},
                      "metrics": {"unit_test.resolved": 1.0},
                  }
              ],
              "edges": {},
          }
      ).encode("utf-8"),
  )
  reading = _reading(store)
  assert reading.runs == ()
  assert reading.incomplete == (
      IncompleteRun("x", 0, missing=(BASELINE, GUIDED)),
  )


def test_two_rollouts_of_one_instance_are_two_pairs(store: FilesystemStore):
  _run(store, "x", baseline=1.0, guided=1.0, rollout_id=0)
  _run(store, "x", baseline=0.0, guided=1.0, rollout_id=1)
  reading = _reading(store)
  assert [(run.rollout_id, run.cell) for run in reading.runs] == [
      (0, Cell.KEPT),
      (1, Cell.GAINED),
  ]


def test_every_cell_and_the_incomplete_count_print_even_at_zero(
    store: FilesystemStore,
):
  # A clean sweep and an unreported one must not read the same.
  _run(store, "x", baseline=1.0, guided=1.0)
  table = _reading(store).render()
  assert "kept 1" in table
  assert "gained 0" in table
  assert "regressed 0" in table
  assert "unsolved 0" in table
  assert "incomplete, not counted above: 0" in table


# ─── the command ─────────────────────────────────────────────────────────────


def test_the_command_reads_a_store_root_and_prints_json_and_the_table(
    store: FilesystemStore,
):
  _sweep(store)

  result = runner.invoke(
      app, ["guided-gain", "sw", "--store-root", str(store.root)]
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
  assert "incomplete, not counted above: 5" in result.stderr


def test_a_sweep_with_no_runs_is_refused_not_rendered_as_zeros(
    tmp_path: Path,
):
  # "Nothing measured" must not print as four zeros and a clean table.
  result = runner.invoke(
      app, ["guided-gain", "nothing", "--store-root", str(tmp_path / "empty")]
  )
  assert result.exit_code == 1
  assert "has no runs" in result.stderr
  assert result.stdout == ""
