"""Tests for SweBenchProGrader: the output tri-state + score, from a ws."""

import json
from pathlib import Path

from swe_lab.core.datasets.swebench_pro.unit_test import (
    OutputState,
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
)


def _workspace(tmp_path: Path, required: list[str], output: str | None) -> Path:
  ws = tmp_path / "ws"
  ws.mkdir(parents=True)
  _ = (ws / REQUIRED_TESTS_NAME).write_text(json.dumps(required))
  if output is not None:
    _ = (ws / "output.json").write_text(output)
  return ws


def _passed(*names: str) -> str:
  return json.dumps({"tests": [{"name": n, "status": "PASSED"} for n in names]})


def test_full_pass_resolves_with_score_one(tmp_path: Path):
  ws = _workspace(tmp_path, ["a", "b"], _passed("a", "b"))
  v = SweBenchProGrader().grade(ws)
  assert v.output_state is OutputState.OK
  assert v.passed == frozenset({"a", "b"})
  assert v.missing == frozenset()
  assert v.score == 1.0
  assert v.resolved is True


def test_partial_pass_scores_zero(tmp_path: Path):
  ws = _workspace(tmp_path, ["a", "b"], _passed("a"))
  v = SweBenchProGrader().grade(ws)
  assert v.output_state is OutputState.OK
  assert v.missing == frozenset({"b"})
  assert v.score == 0.0
  assert v.resolved is False


def test_absent_output_is_distinct_from_no_pass(tmp_path: Path):
  ws = _workspace(tmp_path, ["a"], output=None)
  v = SweBenchProGrader().grade(ws)
  assert v.output_state is OutputState.ABSENT
  assert v.passed == frozenset()
  assert v.missing == frozenset({"a"})
  assert v.score == 0.0


def test_corrupt_output_is_unparseable_not_no_pass(tmp_path: Path):
  ws = _workspace(tmp_path, ["a"], output="{ this is not json")
  v = SweBenchProGrader().grade(ws)
  assert v.output_state is OutputState.UNPARSEABLE  # the P0-2 fix
  assert v.score == 0.0


def test_non_dict_output_is_unparseable(tmp_path: Path):
  ws = _workspace(tmp_path, ["a"], output="[1, 2, 3]")
  v = SweBenchProGrader().grade(ws)
  assert v.output_state is OutputState.UNPARSEABLE


def test_grader_is_stateless(tmp_path: Path):
  # a zero-field object, fed only a workspace — no per-instance state
  grader = SweBenchProGrader()
  ws1 = _workspace(tmp_path / "one", ["a"], _passed("a"))
  ws2 = _workspace(tmp_path / "two", ["a", "b"], _passed("a"))
  assert grader.grade(ws1).resolved is True
  assert grader.grade(ws2).resolved is False
