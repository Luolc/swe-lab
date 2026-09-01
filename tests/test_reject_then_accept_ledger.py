"""The witness run's cost ceiling must cover every call it authorizes.

A stop rule that watches only some paid calls does not bound anything: an accept
arriving just under the ceiling could still buy its two repeat judgements, and a
cache-off confirmation could follow K attempts, both landing outside the total
the run reports. So the ledger is the single place cost accrues, and "exceeds"
is tested at the boundary rather than assumed.

The digests are checked against the pre-registration in the same file, because a
pre-registration whose numbers drift from the code it governs binds nothing.

Loaded by path like `test_steered_rerun_driver.py`: `experiments/` is exempt
from the code-quality hooks and is not an importable package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_WITNESS = (
    _ROOT
    / "experiments/trace_synthesis/process_supervision"
    / "reject_then_accept_witness/witness.py"
)
_PRE_REGISTRATION = _WITNESS.parent / "PRE-REGISTRATION.md"


@pytest.fixture(scope="module")
def witness() -> ModuleType:
  """Import the witness runner by path.

  Returns:
    The executed module.
  """
  spec = importlib.util.spec_from_file_location("witness_runner", _WITNESS)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  try:
    spec.loader.exec_module(module)
  finally:
    del sys.modules[spec.name]
  return module


def test_the_ledger_counts_every_kind_of_billable_call(
    witness: ModuleType, tmp_path: Path
):
  """Actor, judge, repeat and cache-off calls all reach the same total."""
  ledger = witness.Ledger(tmp_path / "ledger.json")
  for kind in (
      "judge:attempt-0",
      "actor:attempt-1",
      "judge:attempt-1",
      "judge:repeat",
      "actor:cache-off",
  ):
    ledger.record(kind, {"cost": 0.10})
  assert ledger.total == pytest.approx(0.50)
  assert len(ledger.entries) == 5
  assert (tmp_path / "ledger.json").exists()


def test_the_ceiling_trips_only_once_cost_exceeds_it(
    witness: ModuleType, tmp_path: Path
):
  """At the ceiling the run may continue; past it, it may not."""
  ledger = witness.Ledger(tmp_path / "ledger.json")
  ledger.record("actor:attempt-1", {"cost": witness._COST_CEILING_USD})
  assert not ledger.exhausted
  ledger.record("judge:repeat", {"cost": 0.01})
  assert ledger.exhausted


def test_a_missing_cost_is_zero_not_an_error(
    witness: ModuleType, tmp_path: Path
):
  """A usage block without a cost must not abort the run."""
  ledger = witness.Ledger(tmp_path / "ledger.json")
  ledger.record("actor:attempt-1", {})
  ledger.record("judge:attempt-1", None)
  assert ledger.total == 0.0


def test_the_pre_registered_digests_match_the_document(witness: ModuleType):
  """The digests the code enforces are the ones the document fixed."""
  text = _PRE_REGISTRATION.read_text()
  assert witness._BODY_SHA256 in text
  assert witness._ORIGINAL_COMPLETION_SHA256 in text
