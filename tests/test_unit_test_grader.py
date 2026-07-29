"""Tests for SweBenchProGrader: the output tri-state + score, read via sb."""

import json
from pathlib import Path

from etils import epath

from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.sandbox import SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

_SPEC = SandboxSpec(
    instance_id="acme__widget-1",
    image_ref="img:tag",
    workdir="/repo",
    base_commit="abc123",
)


def _grade(
    tmp_path: Path, required: list[str], output: str | None
) -> SweBenchProVerdict:
  """Grade a seeded workspace through a FakeSandbox (the grader's reader).

  The grader now reads its files through the narrow ``SandboxFs`` view, so a
  ``FakeSandbox`` over a real dir stands in for a live sandbox.
  """
  ws = tmp_path / "ws"
  ws.mkdir(parents=True)
  _ = (ws / REQUIRED_TESTS_NAME).write_text(json.dumps(required))
  if output is not None:
    _ = (ws / "output.json").write_text(output)
  return SweBenchProGrader().grade(
      FakeSandbox(spec=_SPEC, workspace=epath.Path(ws))
  )


def _passed(*names: str) -> str:
  return json.dumps({"tests": [{"name": n, "status": "PASSED"} for n in names]})


def test_full_pass_resolves_with_score_one(tmp_path: Path):
  v = _grade(tmp_path, ["a", "b"], _passed("a", "b"))
  assert v.output_state is OutputState.OK
  assert v.passed == frozenset({"a", "b"})
  assert v.missing == frozenset()
  assert v.score == 1.0
  assert v.resolved is True


def test_partial_pass_scores_zero(tmp_path: Path):
  v = _grade(tmp_path, ["a", "b"], _passed("a"))
  assert v.output_state is OutputState.OK
  assert v.missing == frozenset({"b"})
  assert v.score == 0.0
  assert v.resolved is False


def test_absent_output_is_distinct_from_no_pass(tmp_path: Path):
  v = _grade(tmp_path, ["a"], output=None)
  assert v.output_state is OutputState.ABSENT
  assert v.passed == frozenset()
  assert v.missing == frozenset({"a"})
  assert v.score == 0.0


def test_corrupt_output_is_unparseable_not_no_pass(tmp_path: Path):
  v = _grade(tmp_path, ["a"], output="{ this is not json")
  assert v.output_state is OutputState.UNPARSEABLE  # the P0-2 fix
  assert v.score == 0.0


def test_non_dict_output_is_unparseable(tmp_path: Path):
  v = _grade(tmp_path, ["a"], output="[1, 2, 3]")
  assert v.output_state is OutputState.UNPARSEABLE


def test_grader_is_stateless(tmp_path: Path):
  # a zero-field object, fed only a reader — no per-instance state
  grader = SweBenchProGrader()
  ws1 = tmp_path / "one"
  ws1.mkdir()
  _ = (ws1 / REQUIRED_TESTS_NAME).write_text(json.dumps(["a"]))
  _ = (ws1 / "output.json").write_text(_passed("a"))
  ws2 = tmp_path / "two"
  ws2.mkdir()
  _ = (ws2 / REQUIRED_TESTS_NAME).write_text(json.dumps(["a", "b"]))
  _ = (ws2 / "output.json").write_text(_passed("a"))
  assert (
      grader.grade(FakeSandbox(spec=_SPEC, workspace=epath.Path(ws1))).resolved
      is True
  )
  assert (
      grader.grade(FakeSandbox(spec=_SPEC, workspace=epath.Path(ws2))).resolved
      is False
  )
