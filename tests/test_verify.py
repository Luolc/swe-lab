"""Verdict logic for golden verification (pure; no Docker).

Ports the legacy ``EvalSpec``/``EvalResult`` fixtures onto the engine: an
instance is a real ``SweBenchProInstance`` and each run is a
``(RunResult, SweBenchProVerdict | None)`` pair, exactly what ``classify``
consumes now.
"""

from __future__ import annotations

from pathlib import Path

from swe_lab.datasets.deepswe.unit_test import DeepSweVerdict
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.record import SweBenchProInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    OutputState,
    SweBenchProVerdict,
)
from swe_lab.datasets.verify import (
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


# ─── the same classification over DeepSWE's count-shaped verdicts ────────────


def _dsw_run(
    *,
    reward: int,
    f2p_passed: int = 0,
    status: RunStatus = RunStatus.SUCCESS,
) -> tuple[RunResult, DeepSweVerdict | None]:
  verdict = DeepSweVerdict(
      reward=reward,
      f2p_total=5,
      f2p_passed=f2p_passed,
      p2p_total=2,
      p2p_passed=2,
      partial=0.0,
  )
  return _result(status), verdict


def _dsw_instance(tmp_path: Path) -> TaskInstance[DeepSweVerdict]:
  from swe_lab.datasets.deepswe.build_parquet import (
      build_row,
      parse_provenance,
  )
  from swe_lab.datasets.deepswe.record import DeepSweInstance

  from .test_deepswe_build import _PROVENANCE, _write_task

  d = _write_task(tmp_path, "demo-task")
  return DeepSweInstance.from_raw(build_row(d, parse_provenance(_PROVENANCE)))


def test_deepswe_ok(tmp_path: Path) -> None:
  inst = _dsw_instance(tmp_path)
  base = _dsw_run(reward=0)
  golden = _dsw_run(reward=1, f2p_passed=5)
  assert classify(inst, base, golden) == OK


def test_deepswe_golden_fail(tmp_path: Path) -> None:
  inst = _dsw_instance(tmp_path)
  assert classify(inst, _dsw_run(reward=0), _dsw_run(reward=0)) == GOLDEN_FAIL


def test_deepswe_base_sneak_via_counts(tmp_path: Path) -> None:
  # DeepSWE verdicts carry no test NAMES, only counts — the same "a bug test
  # already passes at base" signal must come from f2p_passed > 0.
  inst = _dsw_instance(tmp_path)
  base = _dsw_run(reward=0, f2p_passed=1)
  golden = _dsw_run(reward=1, f2p_passed=5)
  assert classify(inst, base, golden) == BASE_UNEXPECTED_PASS


def test_deepswe_missing_verdict_is_error_not_a_finding(tmp_path: Path) -> None:
  # DeepSWE's grader RAISES on a missing reward.json, so verification sees a
  # None verdict — inconclusive infra, never a dataset finding.
  inst = _dsw_instance(tmp_path)
  golden = _dsw_run(reward=1, f2p_passed=5)
  assert classify(inst, (_result(), None), golden) == ERROR


def test_deepswe_run_json_uses_the_verdicts_own_summary(tmp_path: Path) -> None:
  del tmp_path
  from swe_lab.datasets.verify import _run_json

  data = _run_json(_dsw_run(reward=1, f2p_passed=5))
  assert data["resolved"] is True
  assert data["f2p"] == "5/5"  # the verdict's summary(), not sbp's fields
  assert "output_state" not in data
