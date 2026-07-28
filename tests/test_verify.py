"""Verdict logic for golden verification (pure; no Docker).

Ports the legacy ``EvalSpec``/``EvalResult`` fixtures onto the engine: an
instance is a real ``SweBenchProInstance`` and each run is a
``(RunResult, SweBenchProVerdict | None)`` pair, exactly what ``classify``
consumes now.
"""

from __future__ import annotations

from swe_lab.datasets.swebench_pro.record import SweBenchProInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    SweBenchProVerdict,
)
from swe_lab.datasets.swebench_pro.verify import (
    _base_json,
    BASE_UNEXPECTED_PASS,
    classify,
    ERROR,
    GOLDEN_FAIL,
    OK,
)
from swe_lab.sandbox import RunResult, RunStatus

_BASE = {
    "repo": "acme/widget",
    "instance_id": "acme__widget-1",
    "base_commit": "abc123",
    "patch": "PATCH",
    "test_patch": "",
    "problem_statement": "p",
    "requirements": "",
    "interface": "",
    "repo_language": "python",
    "fail_to_pass": "['t1']",
    "pass_to_pass": "['t2']",
    "issue_specificity": "[]",
    "issue_categories": "[]",
    "before_repo_set_cmd": "",
    "selected_test_files_to_run": "['test/foo.py']",
    "dockerhub_tag": "widget-tag",
}


def _instance(**overrides: str) -> SweBenchProInstance:
  return SweBenchProInstance.from_raw({**_BASE, **overrides})


def _result(status: RunStatus = RunStatus.SUCCESS) -> RunResult:
  return RunResult(label="i", status=status, artifacts={}, metrics={})


def _run(
    passed: tuple[str, ...] = (),
    *,
    required: tuple[str, ...] = ("t1", "t2"),
    output_state: OutputState = OutputState.OK,
    status: RunStatus = RunStatus.SUCCESS,
) -> tuple[RunResult, SweBenchProVerdict | None]:
  """Build a ``(RunResult, verdict)`` pair for one graded run.

  Args:
    passed: The tests the parser reported as passed.
    required: The required test set (``missing`` is derived from it).
    output_state: Whether ``output.json`` was found and readable.
    status: How the engine run ended.

  Returns:
    The run pair ``classify`` consumes.
  """
  verdict = SweBenchProVerdict(
      passed=frozenset(passed),
      missing=frozenset(required) - frozenset(passed),
      output_state=output_state,
  )
  return _result(status), verdict


_INSTANCE = _instance()  # fail_to_pass=('t1',), pass_to_pass=('t2',)


def test_ok_base_fails_golden_passes() -> None:
  base = _run(("t2",))  # ptp passes, bug test fails
  golden = _run(("t1", "t2"))
  assert classify(_INSTANCE, base, golden) == OK


def test_golden_fail() -> None:
  base = _run(("t2",))
  golden = _run(("t2",))  # bug test still missing under the golden patch
  assert classify(_INSTANCE, base, golden) == GOLDEN_FAIL


def test_base_unexpected_pass_when_base_resolves() -> None:
  base = _run(("t1", "t2"))  # base resolves — tests don't detect the bug
  golden = _run(("t1", "t2"))
  assert classify(_INSTANCE, base, golden) == BASE_UNEXPECTED_PASS


def test_base_unexpected_pass_when_bug_test_passes_at_base() -> None:
  # Bug test t1 passes at base even though ptp t2 is missing -> still suspect.
  base = _run(("t1",))
  golden = _run(("t1", "t2"))
  assert classify(_INSTANCE, base, golden) == BASE_UNEXPECTED_PASS


def test_error_on_absent_output() -> None:
  base = _run((), output_state=OutputState.ABSENT)
  golden = _run(("t1", "t2"))
  assert classify(_INSTANCE, base, golden) == ERROR


def test_error_on_unparseable_output() -> None:
  base = _run(("t2",))
  golden = _run(("t1", "t2"), output_state=OutputState.UNPARSEABLE)
  assert classify(_INSTANCE, base, golden) == ERROR


def test_error_on_non_success_status_takes_precedence() -> None:
  # A failed run is ERROR even when the (would-be) verdict looks like a finding.
  base = _run(("t2",))
  golden = _run(("t2",), status=RunStatus.RUN_ERROR)
  assert classify(_INSTANCE, base, golden) == ERROR


def test_error_when_verdict_is_none() -> None:
  base = (_result(), None)  # grading never ran (setup failure)
  golden = _run(("t1", "t2"))
  assert classify(_INSTANCE, base, golden) == ERROR


def test_base_json_diagnostics() -> None:
  base = _run(("t2",))
  data = _base_json(_INSTANCE, base)
  assert data["fail_to_pass_passed"] == []  # bug test does not pass at base
  assert data["pass_to_pass_missing"] == []  # ptp passes at base
  assert data["resolved"] is False
  assert data["output_state"] == OutputState.OK.value
  assert data["status"] == RunStatus.SUCCESS.value
