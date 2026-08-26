"""Tests for the DeepSWE parquet builder (synthetic checkout, no network)."""

import json
from pathlib import Path

import polars as pl
import pytest

from swe_lab.datasets.deepswe.build_parquet import (
    build_row,
    build_rows,
    COLUMNS,
    parse_provenance,
    row_content_hash,
    verify_round_trip,
)

_PROVENANCE = """
| Task ID | Upstream project | Upstream license |
| ------- | ---------------- | ---------------- |
| demo-task | acme/demo | MIT |
| short-sha-task | acme/short | Apache-2.0 |
"""


def _write_task(
    root: Path, task_id: str, *, base_commit: str = "a" * 40
) -> Path:
  d = root / "tasks" / task_id
  (d / "tests").mkdir(parents=True)
  (d / "solution").mkdir()
  _ = (d / "task.toml").write_text(
      f"""
schema_version = "1.3"
[task]
name = "datacurve/{task_id}"
[metadata]
ext_id = "x{task_id}"
task_id = "{task_id}"
display_title = "T"
display_description = "D"
category = "enhancement"
language = "go"
repository_url = "https://github.com/acme/demo"
base_commit_hash = "{base_commit}"
[verifier]
timeout_sec = 1800.0
[agent]
timeout_sec = 5400.0
[environment]
docker_image = "example.test/img:{task_id}-v1.1"
cpus = 2
memory_mb = 8192
storage_mb = 20480
"""
  )
  _ = (d / "instruction.md").write_text(f"solve {task_id}\n")
  _ = (d / "tests" / "config.json").write_text(
      json.dumps({"f2p_node_ids": ["pkg.TestNew"], "p2p_node_ids": ["pkg.T0"]})
  )
  _ = (d / "tests" / "test.sh").write_text("#!/bin/bash\n")
  _ = (d / "tests" / "grader.py").write_text("# grader\n")
  _ = (d / "tests" / "test.patch").write_text("diff --git a/t b/t\n")
  _ = (d / "solution" / "solution.patch").write_text("diff --git a/s b/s\n")
  _ = (d / "solution" / "solve.sh").write_text("#!/bin/bash\n")
  return d


def test_a_row_carries_every_column_and_the_derived_lists_match(
    tmp_path: Path,
):
  d = _write_task(tmp_path, "demo-task")
  row = build_row(d, parse_provenance(_PROVENANCE))
  assert set(row) == set(COLUMNS)
  assert row["base_commit"] == row["base_commit_hash"] == "a" * 40
  assert row["upstream_repo"] == "acme/demo"
  assert row["upstream_license"] == "MIT"
  # The convenience lists are DERIVED from config_json — same source, so
  # they cannot disagree with what the verifier will actually read.
  assert row["f2p"] == json.loads(row["config_json"])["f2p_node_ids"]
  assert row["p2p"] == json.loads(row["config_json"])["p2p_node_ids"]


def test_a_short_sha_is_normalized_beside_the_verbatim_value(tmp_path: Path):
  # The fix is a SEPARATE column: the upstream value stays, auditable per row
  # (task-30 §2b — fixes are never overwrites).
  d = _write_task(tmp_path, "short-sha-task", base_commit="68dafce")
  full = "68dafce" + "b" * 33
  row = build_row(
      d,
      parse_provenance(_PROVENANCE),
      base_commit_fixes={"short-sha-task": full},
  )
  assert row["base_commit_hash"] == "68dafce"
  assert row["base_commit"] == full


def test_a_fix_that_does_not_extend_the_recorded_prefix_is_refused(
    tmp_path: Path,
):
  # A "fix" disagreeing with the value it claims to complete is itself wrong;
  # silently preferring either side would bake the error into the dataset.
  d = _write_task(tmp_path, "short-sha-task", base_commit="68dafce")
  with pytest.raises(ValueError, match="does not extend"):
    _ = build_row(
        d,
        parse_provenance(_PROVENANCE),
        base_commit_fixes={"short-sha-task": "c" * 40},
    )


def test_a_task_without_provenance_is_refused(tmp_path: Path):
  # Every row must carry its licensing answer — a missing entry means the
  # attribution table drifted from the task list, which must fail the build,
  # not publish a row with an empty license.
  d = _write_task(tmp_path, "unlisted-task")
  with pytest.raises(ValueError, match="no PROVENANCE.md entry"):
    _ = build_row(d, parse_provenance(_PROVENANCE))


def test_the_content_hash_ignores_key_order_but_not_content():
  a = {"task_id": "t", "x": 1, "y": ["p", "q"]}
  b = {"y": ["p", "q"], "x": 1, "task_id": "t"}
  assert row_content_hash(a) == row_content_hash(b)
  assert row_content_hash(a) != row_content_hash({**a, "x": 2})


def test_round_trip_through_a_real_parquet(tmp_path: Path):
  _ = _write_task(tmp_path, "demo-task")
  _ = _write_task(tmp_path, "short-sha-task")
  _ = (tmp_path / "PROVENANCE.md").write_text(_PROVENANCE)
  rows = build_rows(tmp_path)
  assert [r["task_id"] for r in rows] == ["demo-task", "short-sha-task"]
  parquet = tmp_path / "out.parquet"
  pl.DataFrame(rows).select(COLUMNS).write_parquet(str(parquet))
  verify_round_trip(parquet, rows)  # byte-level: hashes equal after re-read
