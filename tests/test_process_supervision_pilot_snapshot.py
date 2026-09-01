"""The process-supervision analysis must rederive its numbers from `runs/`.

Its pilot-scale figures come from another component's run ledger, which lives
off-repo and in no git repository. A reduced snapshot of that ledger is
committed beside the runs so the figures are auditable by someone who has only
this checkout; these tests pin the two halves of that guarantee, because both
failures are silent — a script that quietly reads a path only one machine has,
and a checkout that quietly reports less than it claims to.

The script lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_ANALYZE = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/process_supervision/analyze.py"
)

_PILOT_ATTEMPTS = 20

# The machine-specific ledger, as a fresh checkout sees it.
_NO_LEDGER = Path("/nonexistent/ledger.jsonl")


def _load_analysis() -> ModuleType:
  """Import the analysis script by path."""
  spec = importlib.util.spec_from_file_location("process_supervision", _ANALYZE)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def analysis() -> ModuleType:
  return _load_analysis()


def test_the_analysis_runs_without_the_off_repo_ledger(
    analysis: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
  """A fresh checkout has no pilot ledger, and must still print every number."""
  monkeypatch.setattr(
      analysis, "PILOT_SOURCE", Path("/nonexistent/ledger.jsonl")
  )
  monkeypatch.setattr(sys, "argv", ["analyze.py"])

  assert analysis.main() == 0

  out = capsys.readouterr().out
  assert "pilot ledger (scale calibration)" in out
  assert f"'n': {_PILOT_ATTEMPTS}" in out or f"n: {_PILOT_ATTEMPTS}" in out


def test_the_pilot_scale_comes_from_the_committed_snapshot(
    analysis: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The five scale statistics derive from `runs/`, not from the machine."""
  monkeypatch.setattr(
      analysis, "PILOT_SOURCE", Path("/nonexistent/ledger.jsonl")
  )

  scale = analysis.ledger_scale()

  assert scale["n"] == _PILOT_ATTEMPTS
  for key in (
      "cache_read_input_tokens",
      "cache_creation_input_tokens",
      "total_cost_usd",
      "rollout_wall_seconds",
      "num_turns",
  ):
    assert scale[key]["mean"] > 0
  assert scale["mean_prefix_tokens_per_turn"] > 0


def test_a_missing_snapshot_fails_instead_of_reporting_less(
    analysis: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  """The snapshot is committed, so its absence is a broken checkout."""
  monkeypatch.setattr(analysis, "PILOT_FROZEN", tmp_path / "pilot_ledger.jsonl")

  with pytest.raises(FileNotFoundError, match="--freeze-pilot"):
    analysis.ledger_scale()
