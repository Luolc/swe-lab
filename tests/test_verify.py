"""Verdict logic for golden verification (pure; no Docker)."""

from __future__ import annotations

from swe_lab.core.benchmark import EvalSpec
from swe_lab.core.datasets.swebench_pro import EvalResult
from swe_lab.evaluation.verify import (
    _base_json,
    BASE_UNEXPECTED_PASS,
    classify,
    ERROR,
    GOLDEN_FAIL,
    OK,
)


def _spec(
    fail_to_pass: tuple[str, ...], pass_to_pass: tuple[str, ...]
) -> EvalSpec:
  return EvalSpec(
      instance_id="i",
      image_ref="img",
      workdir="/app",
      base_commit="c",
      before_repo_set_cmd="",
      run_script="",
      parser="",
      fail_to_pass=fail_to_pass,
      pass_to_pass=pass_to_pass,
      selected_tests=(),
  )


def _res(
    resolved: bool,
    passed: tuple[str, ...] = (),
    *,
    output_found: bool = True,
    timed_out: bool = False,
) -> EvalResult:
  return EvalResult(
      instance_id="i",
      resolved=resolved,
      passed=passed,
      missing=(),
      exit_code=0,
      timed_out=timed_out,
      output_found=output_found,
  )


_SPEC = _spec(("t1",), ("t2",))


def test_ok_base_fails_golden_passes() -> None:
  base = _res(False, passed=("t2",))  # ptp passes, bug test fails
  golden = _res(True, passed=("t1", "t2"))
  assert classify(_SPEC, base, golden) == OK


def test_golden_fail() -> None:
  base = _res(False, passed=("t2",))
  golden = _res(False, passed=("t2",))
  assert classify(_SPEC, base, golden) == GOLDEN_FAIL


def test_base_unexpected_pass_when_base_resolves() -> None:
  base = _res(True, passed=("t1", "t2"))
  golden = _res(True, passed=("t1", "t2"))
  assert classify(_SPEC, base, golden) == BASE_UNEXPECTED_PASS


def test_base_unexpected_pass_when_bug_test_passes_at_base() -> None:
  # Bug test t1 passes at base even though ptp t2 is missing -> still suspect.
  base = _res(False, passed=("t1",))
  golden = _res(True, passed=("t1", "t2"))
  assert classify(_SPEC, base, golden) == BASE_UNEXPECTED_PASS


def test_error_on_missing_output() -> None:
  base = _res(False, output_found=False)
  golden = _res(True, passed=("t1", "t2"))
  assert classify(_SPEC, base, golden) == ERROR


def test_error_on_timeout_takes_precedence_over_golden_fail() -> None:
  base = _res(False, passed=("t2",))
  golden = _res(False, timed_out=True)
  assert classify(_SPEC, base, golden) == ERROR


def test_base_json_diagnostics() -> None:
  base = _res(False, passed=("t2",))
  data = _base_json(_SPEC, base)
  assert data["fail_to_pass_passed"] == []
  assert data["pass_to_pass_missing"] == []
  assert data["resolved"] is False
