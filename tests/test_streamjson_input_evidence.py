"""Guards the `streamjson_input` experiment's central §14 claim.

The experiment lives under `experiments/`, which is exempt from the code-quality
hooks — but its report asserts an equality between two committed artifacts, and
`AGENTS.md` says an invariant needs a test or the claim gets downgraded. Two
things are guarded here, both offline:

- the evidence builder compares the **last agent-loop request**, not the last
  request — a trailing prompt-suggestion exchange (which the TUI makes and the
  headless path does not) must not be the one selected. Selecting it silently
  made the committed evidence render 8/9 wire messages where the report says
  6/7;
- the committed evidence still says what §14 says: the four arms' message
  counts and `<system-reminder>` counts, and the byte-identical mid-turn
  wrapper, checked through the digest the artifact carries.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from typing import Any

import pytest

from swe_lab.paths import find_repo_root

EXPERIMENT = (
    find_repo_root() / "experiments" / "trace_synthesis" / "streamjson_input"
)


def _evidence_module() -> Any:
  spec = importlib.util.spec_from_file_location(
      "streamjson_evidence", EXPERIMENT / "evidence.py"
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.path.insert(0, str(EXPERIMENT))
  try:
    spec.loader.exec_module(module)
  finally:
    sys.path.remove(str(EXPERIMENT))
  return module


def _agent_loop_record(tag: str) -> dict[str, object]:
  return {
      "request": {
          "body": {
              "tools": [{"name": "Bash"}],
              "messages": [
                  {"role": "user", "content": [{"type": "text", "text": tag}]}
              ],
          }
      }
  }


def _suggestion_record() -> dict[str, object]:
  return {
      "request": {
          "body": {
              "tools": [{"name": "Bash"}],
              "messages": [
                  {
                      "role": "user",
                      "content": [{"type": "text", "text": "task"}],
                  },
                  {
                      "role": "user",
                      "content": (
                          "[SUGGESTION MODE: Suggest what the user might"
                          " naturally type next into Claude Code.]"
                      ),
                  },
              ],
          }
      }
  }


def _quota_record() -> dict[str, object]:
  return {
      "request": {"body": {"messages": [{"role": "user", "content": "quota"}]}}
  }


def _wire(run: str) -> dict[str, Any]:
  path = EXPERIMENT / "runs" / run / "evidence.json"
  if not path.is_file():
    pytest.skip(f"{path} is not present in this checkout")
  return json.loads(path.read_text())["wire"]


def test_wire_selection_skips_a_trailing_prompt_suggestion_request():
  evidence = _evidence_module()
  records = [
      _quota_record(),
      _agent_loop_record("turn 1"),
      _agent_loop_record("turn 2"),
      _suggestion_record(),
  ]

  index, counts = evidence.select_wire_record(records)

  assert index == 2, "the last *agent-loop* request is the one §14 compares"
  assert counts == {
      "api_calls": 4,
      "agent_loop_calls": 2,
      "excluded_side_calls": 2,
  }


def test_wire_selection_reports_no_record_when_there_is_no_agent_loop():
  evidence = _evidence_module()

  index, counts = evidence.select_wire_record([_quota_record()])

  assert index is None
  assert counts["agent_loop_calls"] == 0


@pytest.mark.parametrize(
    ("run", "messages", "reminders"),
    [
        ("proxy-control", 6, 3),
        ("tui-control", 6, 3),
        ("proxy-midturn", 7, 4),
        ("tui-midturn", 7, 4),
    ],
)
def test_committed_evidence_matches_the_section_14_table(
    run: str, messages: int, reminders: int
):
  wire = _wire(run)
  assert len(wire["messages"]) == messages
  assert wire["system_reminder_blocks"] == reminders


def test_the_mid_turn_wrapper_is_byte_identical_across_front_ends():
  headless = _wire("proxy-midturn")["messages"][-1]
  tui = _wire("tui-midturn")["messages"][-1]

  assert headless["role"] == tui["role"] == "system"
  # The digests are taken over the raw wire text at build time, so this is the
  # equality §14 claims, not an equality between two truncated copies.
  assert headless["text_digests"] == tui["text_digests"]
  assert headless["texts"] == tui["texts"]
  assert (
      "The user sent a new message while you were working"
      in headless["texts"][0]
  )
