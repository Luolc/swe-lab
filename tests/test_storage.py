"""Tests for the annotation storage layout."""

from __future__ import annotations

import json
from pathlib import Path

from swe_lab.tasks.related_files.agent_run import RunResult
from swe_lab.tasks.related_files.schema import Annotation
from swe_lab.tasks.related_files.storage import (
    candidate_label,
    instance_dir,
    store_run,
)


def _result(instance_id: str) -> RunResult:
  annotation = Annotation(instance_id, (), {"kind": "annotation"})
  return RunResult(
      instance_id=instance_id,
      annotation=annotation,
      last_record={"complete": True, "response": {"message": "hi"}},
      proxy_log_path=Path("/tmp/x.jsonl"),
      complete=True,
  )


def test_candidate_label() -> None:
  assert candidate_label(1) == "candidate_1"
  assert candidate_label(3) == "candidate_3"


def test_instance_dir_layout(tmp_path: Path) -> None:
  d = instance_dir("inst-1", dataset="swebench_pro", repo_root=tmp_path)
  assert (
      d
      == tmp_path
      / "outputs"
      / "related_files"
      / "swebench_pro"
      / "intermediate"
      / "inst-1"
  )


def test_store_run_writes_both_files(tmp_path: Path) -> None:
  ann_path, exch_path = store_run(
      "inst-1",
      "candidate_1",
      _result("inst-1"),
      dataset="swebench_pro",
      repo_root=tmp_path,
  )
  base = (
      tmp_path
      / "outputs"
      / "related_files"
      / "swebench_pro"
      / "intermediate"
      / "inst-1"
  )
  assert ann_path == base / "candidate_1.json"
  assert exch_path == base / "candidate_1.last_exchange.json"

  assert json.loads(ann_path.read_text())["instance_id"] == "inst-1"
  assert json.loads(exch_path.read_text())["complete"] is True


def test_store_run_separates_labels(tmp_path: Path) -> None:
  for label in ("candidate_1", "candidate_2", "aggregate"):
    _ = store_run("inst-1", label, _result("inst-1"), repo_root=tmp_path)
  base = (
      tmp_path
      / "outputs"
      / "related_files"
      / "swebench_pro"
      / "intermediate"
      / "inst-1"
  )
  names = sorted(p.name for p in base.iterdir())
  assert names == [
      "aggregate.json",
      "aggregate.last_exchange.json",
      "candidate_1.json",
      "candidate_1.last_exchange.json",
      "candidate_2.json",
      "candidate_2.last_exchange.json",
  ]
