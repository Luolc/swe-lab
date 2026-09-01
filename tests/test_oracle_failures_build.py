"""The oracle-failure builder: a finished run in, one dataset row out."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from etils import epath
import polars as pl
import pytest

from swe_lab.datasets.loader import load_dataset
from swe_lab.datasets.oracle_failures import COLUMNS, OracleFailureInstance
from swe_lab.datasets.oracle_failures.build import (
    build_row,
    scan_for_credentials,
    UnusableRunError,
    write_row,
)
import swe_lab.datasets.oracle_failures.build as build_module
import swe_lab.datasets.oracle_failures.record as record_module
from swe_lab.datasets.swebench_pro.unit_test import REQUIRED_TESTS_NAME

from .test_oracle_failures_record import _Underlying, CONVERSATION

INSTANCE_ID = "acme__widget-1"
STORE_PREFIX = f"store/adhoc/{INSTANCE_ID}/r0"
ROLLOUT_KEY_PREFIX = f"adhoc/{INSTANCE_ID}/r0/rollout/a0"


def _metrics(**overrides: float) -> dict[str, dict[str, float]]:
  """Build the two entries' metrics for a clean, unresolved failure."""
  rollout = {
      "agent_complete": 1.0,
      "claude_code.exit_code": 0.0,
      "claude_code.timed_out": 0.0,
      "patch_is_empty": 0.0,
  }
  grading = {
      "unit_test.resolved": 0.0,
      "unit_test.passed": 1.0,
      "unit_test.missing": 1.0,
      "unit_test.required": 2.0,
  }
  for name, value in overrides.items():
    (rollout if name in rollout else grading)[name] = value
  return {"rollout": rollout, "grading": grading}


def _run_dir(
    tmp_path: Path,
    *,
    metrics: dict[str, dict[str, float]] | None = None,
    succeeded: bool = True,
    conversation: str | None = None,
    patch: str = "diff --git a/x b/x\n+fix\n",
    passed: tuple[str, ...] = ("t::a",),
) -> Path:
  """Lay out what `swe-lab run rollout_and_unit_test` leaves behind."""
  metrics = metrics or _metrics()
  run = tmp_path / "run"
  rollout_dir = run / STORE_PREFIX / "rollout" / "a0"
  rollout_dir.mkdir(parents=True)
  (rollout_dir / "conversation.json").write_text(
      conversation
      if conversation is not None
      else CONVERSATION.model_dump_json()
  )
  (rollout_dir / "patch.diff").write_text(patch)
  # The grading entry ran twice; its final attempt's workspace is what the
  # grader re-reads.
  workspace = run / "unit_test" / "ws" / "a1"
  workspace.mkdir(parents=True)
  (workspace / REQUIRED_TESTS_NAME).write_text(json.dumps(["t::a", "t::b"]))
  (workspace / "output.json").write_text(
      json.dumps({"tests": [{"name": t, "status": "PASSED"} for t in passed]})
  )
  record: dict[str, Any] = {
      "sweep_id": "adhoc",
      "instance_id": INSTANCE_ID,
      "rollout_id": 0,
      "run_ts": "20260901-105749",
      "succeeded": succeeded,
      "entries": [
          {
              "key": "rollout",
              "status": "succeeded",
              "attempts": 1,
              "artifact_keys": {
                  "conversation.json": (
                      f"{ROLLOUT_KEY_PREFIX}/conversation.json"
                  ),
                  "patch.diff": f"{ROLLOUT_KEY_PREFIX}/patch.diff",
              },
              "metrics": metrics["rollout"],
          },
          {
              "key": "unit_test",
              "status": "succeeded",
              "attempts": 2,
              "artifact_keys": {},
              "metrics": metrics["grading"],
          },
      ],
      "edges": {},
  }
  (run / STORE_PREFIX / "workflow.json").write_text(json.dumps(record))
  return run


@pytest.fixture(autouse=True)
def underlying(monkeypatch: pytest.MonkeyPatch) -> _Underlying:
  instance = _Underlying()

  def _resolve(dataset: str, instance_id: str) -> _Underlying:
    del dataset, instance_id
    return instance

  # Patched where each module looks it up: the builder resolves the instance
  # itself, and the loader's `from_raw` does so again when reading back.
  for module in (record_module, build_module):
    monkeypatch.setattr(module, "underlying_instance", _resolve)
  return instance


def test_a_finished_unresolved_run_becomes_a_row(tmp_path: Path):
  row = build_row(_run_dir(tmp_path), dataset="fake")

  assert set(row) == set(COLUMNS)
  assert (row["dataset"], row["instance_id"], row["rollout_id"]) == (
      "fake",
      INSTANCE_ID,
      0,
  )
  assert row["conversation"] == CONVERSATION.model_dump_json()
  assert row["patch"] == "diff --git a/x b/x\n+fix\n"
  # The verdict is the dataset grader's own reading of the persisted grading
  # workspace — so it names the failed test, which the metrics alone cannot.
  verdict = json.loads(row["verdict"])
  assert verdict["resolved"] is False
  assert verdict["summary"]["missing"] == ["t::b"]
  assert verdict["metrics"]["required"] == 2.0
  provenance = json.loads(row["provenance"])
  assert provenance["source"]["sweep_id"] == "adhoc"
  assert provenance["source"]["run_ts"] == "20260901-105749"
  assert provenance["source"]["grading_attempts"] == 2
  assert provenance["grading_metrics"]["unit_test.resolved"] == 0.0


@pytest.mark.parametrize(
    ("metrics", "reason"),
    [
        (_metrics(**{"claude_code.timed_out": 1.0}), "killed at its budget"),
        (_metrics(agent_complete=0.0), "did not finish"),
        (_metrics(**{"claude_code.exit_code": 127.0}), "did not exit cleanly"),
        (_metrics(**{"unit_test.resolved": 1.0}), "not a failure"),
    ],
    ids=["timed-out", "not-complete", "crashed", "resolved"],
)
def test_the_gates_refuse_a_run_that_is_not_a_reasoning_failure(
    tmp_path: Path, metrics: dict[str, dict[str, float]], reason: str
):
  # An unresolved verdict is not evidence the actor erred: it reads the same
  # when the actor was killed, never started, or crashed. Each gate names
  # what it saw, and nothing is written.
  with pytest.raises(UnusableRunError, match=reason):
    _ = build_row(_run_dir(tmp_path, metrics=metrics), dataset="fake")


def test_a_workflow_that_did_not_succeed_is_refused(tmp_path: Path):
  with pytest.raises(UnusableRunError, match="did not succeed"):
    _ = build_row(_run_dir(tmp_path, succeeded=False), dataset="fake")


def test_a_workspace_that_regrades_as_resolved_is_refused(tmp_path: Path):
  # The record says unresolved but the persisted files grade as a pass: they
  # are not the graded files, and a row built on either would be wrong.
  run = _run_dir(tmp_path, passed=("t::a", "t::b"))
  with pytest.raises(UnusableRunError, match="re-grading .* resolves"):
    _ = build_row(run, dataset="fake")


def test_a_credential_shaped_string_refuses_the_row_without_echoing_it(
    tmp_path: Path,
):
  secret = "sk-ant-oat01-" + "A" * 40
  leaked = CONVERSATION.model_dump_json().replace("I tried.", f"token {secret}")
  with pytest.raises(UnusableRunError) as info:
    _ = build_row(_run_dir(tmp_path, conversation=leaked), dataset="fake")
  message = str(info.value)
  assert "anthropic api key or oauth token" in message
  assert secret not in message


def test_a_credential_in_a_malformed_conversation_is_refused_before_parsing(
    tmp_path: Path,
):
  # A parser's error quotes the input it rejected, so the scan has to run on
  # the raw artifact first — otherwise a malformed conversation carrying a
  # token would print the token on its way to being refused.
  secret = "sk-ant-oat01-" + "B" * 40
  malformed = json.dumps({"messages": [{"role": "user", "content": secret}]})
  with pytest.raises(UnusableRunError) as info:
    _ = build_row(_run_dir(tmp_path, conversation=malformed), dataset="fake")
  message = str(info.value)
  assert "anthropic api key or oauth token" in message
  assert secret not in message


def test_a_malformed_conversation_is_refused_without_echoing_its_content(
    tmp_path: Path,
):
  # No credential this time, so the parser is reached — and its refusal must
  # still describe the failure by location and kind, never by input value.
  marker = "UNIQUE-CONTENT-MARKER-7f3a"
  malformed = json.dumps({"messages": [{"role": "user", "content": marker}]})
  with pytest.raises(
      UnusableRunError, match="not a typed Conversation"
  ) as info:
    _ = build_row(_run_dir(tmp_path, conversation=malformed), dataset="fake")
  assert marker not in str(info.value)
  assert "messages/0/content (list_type)" in str(info.value)


def test_the_provenance_carries_no_host_path(tmp_path: Path):
  # A run directory names the operator on an ordinary workstation; a trace
  # record redacts operator PII at write time, so the row identifies its
  # source by store key and timestamp and never by where it sat on disk.
  home = tmp_path / "Users" / "alice"
  run = _run_dir(home)
  row = build_row(run, dataset="fake")
  assert "alice" not in row["provenance"]
  assert str(run) not in row["provenance"]
  source = json.loads(row["provenance"])["source"]
  assert "run_dir" not in source
  assert source["workflow_record"] == f"{STORE_PREFIX}/workflow.json"


def test_the_credential_scan_names_patterns_not_values():
  assert scan_for_credentials("plain text, nothing here") == []
  hits = scan_for_credentials(
      "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123 and hf_" + "b" * 24
  )
  assert hits == ["hugging face token", "bearer credential"]


def test_write_row_replaces_the_instances_earlier_row(tmp_path: Path):
  out = epath.Path(
      tmp_path / "datasets" / "oracle_failures" / "data" / "x.parquet"
  )
  first = build_row(_run_dir(tmp_path / "one"), dataset="fake")
  second = build_row(
      _run_dir(tmp_path / "two", patch="diff --git a/y b/y\n+other\n"),
      dataset="fake",
  )

  assert write_row(out, first) is False
  assert write_row(out, second) is True

  frame = pl.read_parquet(str(out))
  assert frame.height == 1
  assert frame["patch"].to_list() == ["diff --git a/y b/y\n+other\n"]
  # …and what was written is what the loader reads back, as a runnable record
  dataset = load_dataset("oracle_failures", root=tmp_path / "datasets")
  record = dataset.require(INSTANCE_ID)
  assert isinstance(record, OracleFailureInstance)
  assert record.rollout_id == 0


def test_a_same_id_row_from_another_source_is_a_collision_not_a_replacement(
    tmp_path: Path,
):
  # The file is indexed by instance id alone, so two sources sharing an id
  # cannot both live in it — and the second must not quietly delete the
  # first. Refuse, keep the file as it was, and say which source holds the id.
  out = epath.Path(tmp_path / "x.parquet")
  first = build_row(_run_dir(tmp_path / "one"), dataset="one")
  second = build_row(_run_dir(tmp_path / "two"), dataset="two")
  assert write_row(out, first) is False
  with pytest.raises(UnusableRunError, match="from dataset 'one'"):
    _ = write_row(out, second)
  frame = pl.read_parquet(str(out))
  assert frame["dataset"].to_list() == ["one"]
