"""Tests for the DeepSWE record, grader, and eval compile (no Docker)."""

import json
from pathlib import Path

from etils import epath
import pytest

from swe_lab.datasets.deepswe.build_parquet import (
    build_row,
)
from swe_lab.datasets.deepswe.build_parquet import COLUMNS as BUILDER_COLUMNS
from swe_lab.datasets.deepswe.build_parquet import (
    parse_provenance,
)
from swe_lab.datasets.deepswe.fetch import ensure_deepswe_parquet
from swe_lab.datasets.deepswe.record import DeepSweInstance
from swe_lab.datasets.deepswe.unit_test import (
    DeepSweGrader,
)
from swe_lab.sandbox import SandboxError, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

from .test_deepswe_build import _PROVENANCE, _write_task


def _instance(tmp_path: Path, task_id: str = "demo-task") -> DeepSweInstance:
  # Producer -> consumer round trip: the row the builder makes IS the raw the
  # record parses, so this test breaks if either side drifts.
  d = _write_task(tmp_path, task_id)
  row = build_row(d, parse_provenance(_PROVENANCE))
  record = DeepSweInstance.from_raw(row)
  return record


def test_the_record_parses_the_builders_own_row(tmp_path: Path):
  inst = _instance(tmp_path)
  assert DeepSweInstance.COLUMNS is BUILDER_COLUMNS  # one home, asserted
  assert inst.instance_id == "demo-task"
  assert inst.sandbox_spec() == SandboxSpec(
      "demo-task", "example.test/img:demo-task-v1.1", "/app", "a" * 40
  )
  assert inst.prompt() == "solve demo-task\n"
  assert inst.gold_patch() == "diff --git a/s b/s\n"
  assert list(inst.required_tests()) == ["pkg.TestNew", "pkg.T0"]
  # Original tasks: no upstream fix commit exists, and the purge's weakened
  # assertion is the designed behavior, not a gap.
  assert inst.solution_sha() is None


def test_the_eval_script_moves_files_and_never_touches_the_tree(
    tmp_path: Path,
):
  """Their grader owns patch application; ours owns only the boundary.

  A reset here would fight their per-file reset (task-30 §3), so the script
  must contain none — the whole eval side is `cp in, run test.sh, cp out`.
  """
  spec = _instance(tmp_path).unit_test_spec(apply_patch=True)
  script = spec.eval_script
  assert 'cp "$SANDBOX_WORKSPACE"/patch.diff /logs/artifacts/model.patch'
  assert "bash /tests/test.sh" in script
  assert "git" not in script  # no reset, no checkout, no apply — theirs
  assert "cp /logs/verifier/reward.json" in script
  assert sorted(spec.mounts) == [
      "/tests/config.json",
      "/tests/grader.py",
      "/tests/test.patch",
      "/tests/test.sh",
  ]
  # Self-check mode: no patch is staged, and upstream grades the base state
  # reward-0 by construction.
  base = _instance(tmp_path, "short-sha-task").unit_test_spec(apply_patch=False)
  assert "model.patch" not in base.eval_script


def test_baseline_mode_is_refused_not_ignored(tmp_path: Path):
  # Their grader consumes base_commit-relative patches (per-file reset);
  # accepting-and-ignoring the flag would silently mis-grade every run whose
  # patch overlaps image state.
  with pytest.raises(ValueError, match="baseline"):
    _ = _instance(tmp_path).unit_test_spec(
        apply_patch=True, patch_baseline=True
    )


def _sandbox(tmp_path: Path) -> FakeSandbox:
  return FakeSandbox(
      spec=SandboxSpec("x", "img:tag", "/app", "base"),
      workspace=epath.Path(tmp_path),
  )


def test_the_grader_reads_upstreams_verdict(tmp_path: Path):
  _ = (tmp_path / "reward.json").write_text(
      json.dumps(
          {
              "reward": 1,
              "f2p_total": 20,
              "f2p_passed": 20,
              "p2p_total": 3,
              "p2p_passed": 3,
              "f2p": 1.0,
              "p2p": 1.0,
              "partial": 1.0,
          }
      )
  )
  verdict = DeepSweGrader().grade(_sandbox(tmp_path))
  assert verdict.resolved and verdict.score == 1.0
  assert verdict.metrics()["f2p_passed"] == 20.0


def test_an_apply_failure_is_graded_zero_not_crashed(tmp_path: Path):
  # Upstream separates the two: apply_failed writes a reward.json (graded,
  # the patch's fault) — only a MISSING reward.json is the crash sentinel.
  _ = (tmp_path / "reward.json").write_text(
      json.dumps({"reward": 0, "apply_failed": 1, "f2p_total": 5})
  )
  verdict = DeepSweGrader().grade(_sandbox(tmp_path))
  assert verdict.apply_failed and not verdict.resolved
  assert verdict.score == 0.0


def test_a_missing_reward_json_fails_the_attempt_ungraded(tmp_path: Path):
  # Their trap writes reward.txt=-1 for a crashed verifier: infrastructure,
  # not a grade — the same attribution rule the baseline verify enforces.
  with pytest.raises(SandboxError, match="no reward.json"):
    _ = DeepSweGrader().grade(_sandbox(tmp_path))


def test_the_fetch_verifies_even_a_present_file(tmp_path: Path):
  # The pin is the trust anchor, and it anchors nothing if a present-but-
  # drifted file is trusted; verification must run on every load.
  data = tmp_path / "data"
  data.mkdir()
  _ = (data / "deep-swe-1-1.parquet").write_bytes(b"not the pinned bytes")
  with pytest.raises(ValueError, match="does not match the pinned sha256"):
    _ = ensure_deepswe_parquet(data)
