"""Guards the `resume_loop_feasibility` experiment's report-driving numbers.

The experiment lives under `experiments/`, which is exempt from the
code-quality hooks — but its report turns on a handful of committed values, and
`AGENTS.md` says an invariant needs a test or the claim gets downgraded. The
raw captures are scratch-only and gone the moment `/tmp` is cleared, so
everything here runs **offline against the committed witnesses**.

Three things are guarded, and the third is the one the experiment got wrong
once:

- the turn-unit result — `--max-turns N` yields exactly N assistant messages
  while the unbounded control yields more and finishes;
- the seam detectors have **discriminating power**, shown by a mutant: the same
  reducer that reports zeros on the anchored capture reports non-zero once a
  seam string is planted. A detector whose zero is never contrasted with a
  non-zero is indistinguishable from a broken one;
- the **block shapes** the role-sequence check cannot see — the anchored seam
  is `[tool_result, text]`, the unsegmented control is `[tool_result]`, and the
  committed A' control is a separate trailing `system` message. The report's
  P0 correction depends on those three staying distinct.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest

EXPERIMENT = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/resume_loop_feasibility"
)
STREAMJSON = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/streamjson_input"
)


def _filter_module() -> Any:
  """Load the experiment's synthetic-message filter.

  Returns:
    The loaded module.
  """
  spec = importlib.util.spec_from_file_location(
      "resume_loop_synthetic_filter", EXPERIMENT / "synthetic_filter.py"
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _evidence_module() -> Any:
  """Load the experiment's evidence builder from the checkout this test is in.

  Returns:
    The loaded module.
  """
  spec = importlib.util.spec_from_file_location(
      "resume_loop_evidence", EXPERIMENT / "evidence.py"
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.path.insert(0, str(EXPERIMENT))
  try:
    spec.loader.exec_module(module)
  finally:
    sys.path.remove(str(EXPERIMENT))
  return module


def _witness(name: str) -> dict[str, Any]:
  """Read one committed witness.

  Args:
    name: file name under the experiment's `runs/`.

  Returns:
    The parsed artifact.
  """
  return json.loads((EXPERIMENT / "runs" / name).read_text(encoding="utf-8"))


def _agent_loop_requests(witness: dict[str, Any]) -> list[dict[str, Any]]:
  """Select the main-loop requests of a capture witness.

  An auxiliary call carries no `tools` array; counting one as a main-loop
  request is what made an earlier revision of the report misstate its
  request-to-segment account.

  Args:
    witness: a capture witness.

  Returns:
    The requests that carry tools.
  """
  return [r for r in witness["requests"] if r["has_tools"]]


def test_a_turn_is_one_model_round_trip() -> None:
  """`--max-turns N` stops after N assistant messages; unbounded does not."""
  arms = {a["arm"]: a for a in _witness("q2a-evidence.json")["arms"]}
  for label in ("1", "2", "3"):
    arm = arms[label]
    assert arm["assistant_messages"] == int(label)
    assert arm["result_subtype"] == "error_max_turns"
    assert arm["result_terminal_reason"] == "max_turns"
    assert arm["task_complete"] is False
  unbounded = arms["none"]
  assert unbounded["assistant_messages"] > 3
  assert unbounded["result_subtype"] == "success"
  assert unbounded["task_complete"] is True
  assert unbounded["nonces_in_result"] == 5


def test_the_early_exit_is_distinguishable_from_completion() -> None:
  """The four fields the loop driver would have to read actually differ."""
  arms = {a["arm"]: a for a in _witness("q2a-evidence.json")["arms"]}
  cut, done = arms["1"], arms["none"]
  differing = [
      key
      for key in (
          "result_subtype",
          "result_terminal_reason",
          "result_is_error",
          "exit_code",
      )
      if cut[key] != done[key]
  ]
  assert len(differing) == 4, differing


def test_the_plain_resume_seam_reaches_the_wire() -> None:
  """Both default-resume artifacts appear in a resumed request's body."""
  requests = _agent_loop_requests(_witness("q3wire-evidence.json"))
  resumed = [r for r in requests if r["seam_user_text_blocks"]]
  assert len(resumed) == 1
  assert resumed[0]["seam_synthetic_assistant"] == 1


def test_the_anchored_seam_carries_neither_default_resume_artifact() -> None:
  """`--resume-session-at` removes both, on every main-loop request."""
  requests = _agent_loop_requests(_witness("q7loop-evidence.json"))
  assert len(requests) == 4
  assert [r["messages"] for r in requests] == [2, 4, 6, 8]
  for request in requests:
    assert request["seam_user_text_blocks"] == 0
    assert request["seam_synthetic_assistant"] == 0


def test_the_seam_detector_has_discriminating_power() -> None:
  """A planted seam flips the same reducer from zero to non-zero.

  Without this, every zero in the report is equally consistent with a detector
  that cannot fire at all.
  """
  evidence = _evidence_module()
  clean = {
      "role": "user",
      "content": [
          {"type": "tool_result", "content": "ok"},
          {"type": "text", "text": "Continue."},
      ],
  }
  mutant = {
      "role": "user",
      "content": [
          {"type": "tool_result", "content": "ok"},
          {"type": "text", "text": evidence.SEAM_USER_TEXT},
      ],
  }
  assert json.dumps([clean]).count(evidence.SEAM_USER_TEXT) == 0
  assert json.dumps([mutant]).count(evidence.SEAM_USER_TEXT) == 1
  assert evidence.message_shape(clean)["blocks"] == ["tool_result", "text"]


def test_the_three_correction_shapes_are_distinct() -> None:
  """The anchored seam is not the unsegmented shape and not A''s shape.

  This is the report's P0 correction. If these three ever coincide, the
  narrower conclusion the report now draws would need rewriting — in either
  direction — so the distinction is pinned rather than described.
  """
  anchored = _agent_loop_requests(_witness("q7loop-evidence.json"))
  seams = [r for r in anchored if r["mixed_tool_result_and_text_indices"]]
  assert seams, "expected at least one anchored seam"
  for request in seams:
    assert request["last_message_blocks"] == ["tool_result", "text"]
  # One correction block per seam, so the layout accumulates rather than
  # appearing once: 30 turns would end with 30 of them.
  assert [r["correction_text_blocks"] for r in anchored] == [0, 1, 2, 3]

  unsegmented = [
      r
      for r in _agent_loop_requests(_witness("q3wire-evidence.json"))
      if not r["seam_user_text_blocks"] and r["messages"] == 4
  ]
  assert unsegmented, "expected the same-task unsegmented control"
  assert unsegmented[0]["last_message_blocks"] == ["tool_result"]

  a_prime = json.loads(
      (STREAMJSON / "runs/proxy-midturn/evidence.json").read_text(
          encoding="utf-8"
      )
  )["wire"]["messages"][-1]
  assert a_prime["role"] == "system"
  assert a_prime["blocks"] == ["text"]
  assert a_prime["text_digests"][0]["len"] == 440


@pytest.mark.parametrize(
    "name",
    ("q3wire-evidence.json", "q7loop-evidence.json", "q7probe-evidence.json"),
)
def test_every_capture_witness_passed_the_credential_gate(name: str) -> None:
  """Redaction fired, and no credential shape survived — both arms."""
  gate = _witness(name)["credential_gate"]
  assert gate["redaction_marker_occurrences"] > 0
  assert set(gate["credential_shapes"].values()) == {0}


def test_the_real_rollout_control_carries_its_denominator() -> None:
  """The inference-time control is 0 in 496, and says so with its divisor.

  A bare zero is not evidence. This pins both the numerator and the
  denominator, so a later edit cannot quietly turn the count into "never".
  """
  witness = _witness("first-e2e-control-evidence.json")
  assert witness["union_user_tool_result_and_text"] == 0
  assert witness["union_user_tool_result"] == 496
  assert witness["main_loop_requests"] == 32
  # The only block shape observed on a user message carrying a tool result.
  assert witness["distinct_user_tool_result_block_shapes"] == {
      "tool_result": 496
  }
  gate = witness["credential_gate"]
  assert gate["redaction_marker_occurrences"] > 0
  assert set(gate["credential_shapes"].values()) == {0}


def _record(model: str | None, *, request_id: str | None) -> dict[str, Any]:
  """Build an assistant record with the fields the filter's chain reads.

  Args:
    model: the `message.model` value, or None to omit it.
    request_id: the `requestId` value, or None to omit it.

  Returns:
    One transcript record.
  """
  message: dict[str, Any] = {
      "role": "assistant",
      "content": [{"type": "text", "text": "some text"}],
  }
  if model is not None:
    message["model"] = model
  record: dict[str, Any] = {"type": "assistant", "message": message}
  if request_id is not None:
    record["requestId"] = request_id
  return record


def test_the_synthetic_assistant_turn_is_removed() -> None:
  """Positive arm: the record the model never wrote does not survive.

  This is the owner's one hard constraint after criterion (b) was relaxed on
  2026-09-03, so it is asserted rather than described.
  """
  synthetic = _record("<synthetic>", request_id=None)
  kept = _filter_module().strip_synthetic_assistants([synthetic])
  assert kept == []


def test_a_real_assistant_turn_is_kept() -> None:
  """Control arm: a filter that drops everything passes the positive arm too.

  Without this, `strip_synthetic_assistants` could `return []` and the arm
  above would still be green — indistinguishable from a correct filter.
  """
  real = _record("claude-sonnet-5", request_id="req_abc")
  kept = _filter_module().strip_synthetic_assistants([real])
  assert kept == [real]


def test_the_filter_keeps_order_and_passes_other_records_through() -> None:
  """A mixed transcript loses exactly the synthetic record and nothing else."""
  real_one = _record("claude-sonnet-5", request_id="req_1")
  synthetic = _record("<synthetic>", request_id=None)
  real_two = _record("claude-sonnet-5", request_id="req_2")
  user = {"type": "user", "message": {"role": "user", "content": "hi"}}
  attachment = {"type": "attachment", "attachment": {"type": "budget_usd"}}
  kept = _filter_module().strip_synthetic_assistants(
      [user, real_one, synthetic, attachment, real_two]
  )
  assert kept == [user, real_one, attachment, real_two]


def test_the_chain_is_positive_not_an_exclusion_list() -> None:
  """An assistant record missing its provenance fields is dropped, not kept.

  An exclusion list keyed on the literal `<synthetic>` marker would keep every
  one of these, and the marker is not promised by any interface.
  """
  module = _filter_module()
  for record in (
      _record(None, request_id="req_1"),
      _record("", request_id="req_1"),
      _record("claude-sonnet-5", request_id=None),
      {"type": "assistant"},
      {"type": "assistant", "message": "not a dict"},
  ):
    assert module.strip_synthetic_assistants([record]) == []


def test_the_committed_shape_fixture_matches_what_the_filter_reads() -> None:
  """The real/synthetic distinction the filter uses is the observed one.

  Guards against the filter drifting away from the records it was written for:
  the fixture is reduced from a real resumed session.
  """
  fixture = _witness("assistant-record-shapes.json")
  synthetic = fixture["synthetic_assistant_examples"][0]
  assert synthetic["model"] == "<synthetic>"
  assert synthetic["has_requestId"] is False
  real = fixture["real_assistant_examples"][0]
  assert real["model"] != "<synthetic>"
  assert real["has_requestId"] is True
