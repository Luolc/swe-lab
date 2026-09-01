"""The steered re-run's two gates must fail on what they exist to catch.

Both guard the same thing from different sides: that a sample entering trace
synthesis really is a *reasoning* failure, and that the hint really did survive
into the trace.

`reconcile.py` is the only evidence behind the steered re-run's claim that no
hint was lost. An earlier version compared boundary *counts* and printed
`RECONCILED` on a converted trace with a boundary deleted — equal cardinality
holds when a result is dropped **and** when one is duplicated over its
neighbour. These tests pin the per-boundary bijection that replaced it, using
the two counterexamples that broke the count version.

The module lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path — the same way
`test_injection_shape_redaction.py` loads its driver.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_MODULE = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/steered_rerun/reconcile.py"
)

_HINT = "check what the code produced before your edit"


def _load() -> ModuleType:
  """Import the reconciler by path."""
  spec = importlib.util.spec_from_file_location("steered_reconcile", _MODULE)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def reconciler() -> ModuleType:
  return _load()


def _records(
    hinted: int = 1, identified: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
  """Build a clean three-record run of three boundaries.

  Args:
    hinted: Which boundary carries the hint.
    identified: Whether the host and hook logs carry ``tool_use_id`` — runs
      recorded before the hook logged it must still reconcile, positionally.

  Returns:
    The host judgements, the hook records and the converted boundaries.
  """
  host: list[dict[str, Any]] = []
  hook: list[dict[str, Any]] = []
  converted: list[dict[str, Any]] = []
  for seq in range(3):
    identity = f"toolu_{seq}"
    emitted = seq == hinted
    host.append({
        "session": "s",
        "seq": seq,
        "tool": "Bash",
        "tool_use_id": identity if identified else None,
        "hint": _HINT if emitted else None,
        "hint_emitted": emitted,
    })
    hook.append({
        "seq": seq,
        "tool": "Bash",
        "tool_use_id": identity if identified else None,
        "applied": emitted,
    })
    text = f"real output {seq}"
    if emitted:
      text += f"\n\n<oracle_hint>\n{_HINT}\n</oracle_hint>"
    converted.append({"tool_use_id": identity, "tool": "Bash", "text": text})
  return host, hook, converted


def test_clean_run_reconciles(reconciler: ModuleType) -> None:
  problems, _, identity = reconciler.reconcile(*_records())
  assert problems == []
  assert identity == "tool_use_id"


def test_positional_fallback_is_named(reconciler: ModuleType) -> None:
  """A run without the identity still reconciles, and says the join is weaker."""
  problems, _, identity = reconciler.reconcile(*_records(identified=False))
  assert problems == []
  assert identity == "position + tool name"


def test_omitted_converted_boundary_is_caught(reconciler: ModuleType) -> None:
  """The failure the count-only version printed RECONCILED on."""
  host, hook, converted = _records()
  del converted[0]  # an unhinted boundary, so the hint is still "present"
  problems, _, _ = reconciler.reconcile(host, hook, converted)
  assert problems, "a dropped converted boundary must not reconcile"


def test_duplicated_hint_bearing_result_is_caught(reconciler: ModuleType) -> None:
  """Counts stay equal when a hinted result is pasted over an unhinted one."""
  host, hook, converted = _records()
  converted[0] = dict(converted[1], tool_use_id=converted[0]["tool_use_id"])
  problems, _, _ = reconciler.reconcile(host, hook, converted)
  assert any("never emitted" in problem for problem in problems)


def test_replaced_tool_output_is_caught(reconciler: ModuleType) -> None:
  """A hint that replaced the tool's own bytes rather than appending to them."""
  host, hook, converted = _records()
  converted[1]["text"] = f"<oracle_hint>\n{_HINT}\n</oracle_hint>"
  problems, _, _ = reconciler.reconcile(host, hook, converted)
  assert any("own output is gone" in problem for problem in problems)


def test_hint_missing_from_the_trace_is_caught(reconciler: ModuleType) -> None:
  host, hook, converted = _records()
  converted[1]["text"] = "real output 1"
  problems, _, _ = reconciler.reconcile(host, hook, converted)
  assert any("missing from the converted trace" in problem for problem in problems)


def test_unjudged_boundary_is_caught(reconciler: ModuleType) -> None:
  """The gap that killed the first steered run: the sandbox asked, the host died."""
  host, hook, converted = _records()
  del host[2]
  problems, _, _ = reconciler.reconcile(host, hook, converted)
  assert any("counts disagree" in problem for problem in problems)


def _gates() -> ModuleType:
  """Import the sample freezer by path."""
  spec = importlib.util.spec_from_file_location(
      "steered_freeze_sample",
      Path(__file__).resolve().parents[1]
      / "experiments/trace_synthesis/steered_rerun/freeze_sample.py",
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def freezer() -> ModuleType:
  return _gates()


_HEALTHY = {
    "claude_code.timed_out": 0.0,
    "agent_complete": 1.0,
    "claude_code.exit_code": 0.0,
}


def _attempts(*failures: tuple[str, ...]) -> dict[str, Any]:
  """Build a verdict record from one list of failed tests per attempt."""
  attempts = {
      f"a{index}": {"failed": list(failed), "missing": [], "tests_seen": 2}
      for index, failed in enumerate(failures)
  }
  stable = len({frozenset(f) for f in failures}) == 1
  return {"attempts": attempts, "stable_across_attempts": stable}


def test_a_stable_failure_passes_the_gates(freezer: ModuleType) -> None:
  freezer.check_gates(_HEALTHY, _attempts(("test_a",), ("test_a",), ("test_a",)))


def test_an_actor_that_never_ran_is_refused(freezer: ModuleType) -> None:
  """protonmail/webclients: exit 2, timed_out 0, and the binary never executed."""
  metrics = _HEALTHY | {"agent_complete": 0.0, "claude_code.exit_code": 127.0}
  with pytest.raises(SystemExit, match="agent_complete"):
    freezer.check_gates(metrics, _attempts(("test_a",)))


def test_a_killed_run_is_refused(freezer: ModuleType) -> None:
  with pytest.raises(SystemExit, match="timed_out"):
    freezer.check_gates(_HEALTHY | {"claude_code.timed_out": 1.0}, _attempts(("test_a",)))


def test_disagreeing_grade_attempts_are_refused(freezer: ModuleType) -> None:
  """The fourth cause: the actor finished, the suite disagreed with itself."""
  with pytest.raises(SystemExit, match="attempts disagree"):
    freezer.check_gates(_HEALTHY, _attempts(("test_a",), (), ("test_b",)))


def test_a_run_with_no_grade_attempt_is_refused(freezer: ModuleType) -> None:
  with pytest.raises(SystemExit, match="no grading attempt"):
    freezer.check_gates(_HEALTHY, _attempts())
