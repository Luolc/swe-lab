"""Tests for the honesty-scorer bundle gate.

The gate decides whether a trace bundle may be handed to a blind judge, so its
only dangerous failure is passing something it should have caught. These pin
that direction. It lives under ``experiments/`` and is not an importable
package, so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_GATE = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/honesty_scorer/check_bundles.py"
)

_TRUTH: dict[str, object] = {
    "bundle": "b.bundle.txt",
    "instance_id": "instance_navidrome__navidrome-b3980532237e57ab15b2b93c49d5",
    "base_commit": "b3980532237e57ab15b2b93c49d5cd5b2d050013",
    "screening_verdict": "good",
    "resolved": True,
    "arm": "B",
    "repo": "navidrome/navidrome",
}


@pytest.fixture
def gate() -> ModuleType:
  """Load the gate script by path."""
  spec = importlib.util.spec_from_file_location("check_bundles", _GATE)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _run(gate: ModuleType, tmp_path: Path, text: str, *truth: object) -> int:
  """Write one bundle plus a truth file and run the gate over them."""
  bundles = tmp_path / "bundles"
  bundles.mkdir(exist_ok=True)
  _ = (bundles / "b.bundle.txt").write_text(text)
  truth_path = tmp_path / "truth.json"
  _ = truth_path.write_text(json.dumps(list(truth)))
  return gate.main_with(str(bundles), str(truth_path))


def test_a_short_or_boolean_label_beside_its_field_name_is_caught(
    gate: ModuleType, tmp_path: Path
) -> None:
  # The values that decide the experiment are short or boolean, so they are
  # detectable only beside their own field name.
  leaked = '{"screening_verdict": "good", "resolved": true, "arm": "B"}\n'
  assert _run(gate, tmp_path, leaked, _TRUTH) == 1


def test_a_long_identifier_appearing_anywhere_is_caught(
    gate: ModuleType, tmp_path: Path
) -> None:
  assert (
      _run(gate, tmp_path, f"working on {_TRUTH['instance_id']}\n", _TRUTH) == 1
  )


def test_ground_truth_missing_a_field_fails_rather_than_skipping_it(
    gate: ModuleType, tmp_path: Path
) -> None:
  # A check that could not run must not report success.
  partial = {"bundle": "b.bundle.txt", "repo": "navidrome/navidrome"}
  assert _run(gate, tmp_path, "nothing sensitive here\n", partial) == 1


def test_ordinary_prose_using_those_words_is_not_a_leak(
    gate: ModuleType, tmp_path: Path
) -> None:
  # The counter-direction, and load-bearing: these are ordinary English words,
  # and a gate that alarms on what the operator cannot change gets switched off.
  prose = (
      "I resolved the failing test. The arm of the navidrome scanner walks\n"
      "the tree. Verdict: this looks good to me.\n"
  )
  assert _run(gate, tmp_path, prose, _TRUTH) == 0


def test_finding_no_bundles_is_a_failure_not_an_empty_pass(
    gate: ModuleType, tmp_path: Path
) -> None:
  empty = tmp_path / "empty"
  empty.mkdir()
  truth = tmp_path / "truth.json"
  _ = truth.write_text(json.dumps([_TRUTH]))
  assert gate.main_with(str(empty), str(truth)) == 2


def test_a_bundle_named_twice_in_the_ground_truth_is_a_failure(
    gate: ModuleType, tmp_path: Path
) -> None:
  # A ground truth naming one bundle twice does not know what it asserts.
  other = dict(_TRUTH)
  other["instance_id"] = "instance_other__other-000000000000000000000000"
  other["base_commit"] = "0000000000000000000000000000000000000000"
  other["screening_verdict"] = "bad"
  other["resolved"] = False
  other["arm"] = "A"
  leaked = '{"screening_verdict": "good", "arm": "B"}\n'
  assert _run(gate, tmp_path, leaked, _TRUTH, other) == 1


def test_ground_truth_naming_an_absent_bundle_is_a_failure(
    gate: ModuleType, tmp_path: Path
) -> None:
  # Manifest and directory disagree; nothing downstream can say which is right.
  absent = dict(_TRUTH)
  absent["bundle"] = "not-here.bundle.txt"
  assert _run(gate, tmp_path, "nothing sensitive\n", _TRUTH, absent) == 1
