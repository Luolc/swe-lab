"""Tests for the post-run persist step: persist / promote / index."""

from __future__ import annotations

from pathlib import Path

from etils import epath

from swe_lab.sandbox import (
    AttemptRecord,
    FilesystemStore,
    index,
    persist,
    promote,
)
from swe_lab.sandbox.testing import FakeStore

_SWEEP = "2026-07-30-sonnet"


def _record(
    instance: str,
    ts: str,
    *,
    status: str = "SUCCESS",
    task: str = "rollout",
    rollout_id: int = 0,
    attempt: int = 0,
) -> AttemptRecord:
  return AttemptRecord(
      sweep_id=_SWEEP,
      instance_id=instance,
      task=task,
      rollout_id=rollout_id,
      attempt=attempt,
      run_ts=ts,
      status=status,
      tier="formal",
      backend="host",
      model="claude-sonnet-5",
      metrics={"score": 1.0},
      extra={"is_empty_patch": False},
  )


def test_run_record_json_roundtrip():
  rec = _record("flipt__flipt-1", "1706-0")
  assert AttemptRecord.from_json(rec.to_json()) == rec


def test_persist_uploads_under_run_key_and_appends_shard(tmp_path: Path):
  patch = tmp_path / "patch.diff"
  _ = patch.write_text("DIFF")
  conv = tmp_path / "conversation.json"
  _ = conv.write_text("{}")
  store = FakeStore()

  out = persist(
      store,
      _record("flipt__flipt-1", "1706-0"),
      {"patch.diff": patch, "conversation.json": conv},
  )

  prefix = f"{_SWEEP}/flipt__flipt-1/r0/rollout/a0"
  assert out.artifact_keys == {
      "patch.diff": f"{prefix}/patch.diff",
      "conversation.json": f"{prefix}/conversation.json",
  }
  assert store.objects[f"{prefix}/patch.diff"] == b"DIFF"
  assert store.manifests == [out]  # one shard appended, with keys filled


def test_persist_keeps_failed_runs(tmp_path: Path):
  art = tmp_path / "stderr.log"
  _ = art.write_text("boom")
  store = FakeStore()
  out = persist(
      store,
      _record("acme__widget-1", "0900-0", status="RUN_ERROR"),
      {"stderr.log": art},
  )
  assert out.status == "RUN_ERROR"
  assert store.manifests == [
      out
  ]  # failures persist (gate is tier, not success)


def test_promote_uploads_whole_workspace_preserving_nesting(tmp_path: Path):
  ws = tmp_path / "ws"
  (ws / "diagnostics").mkdir(parents=True)
  _ = (ws / "patch.diff").write_text("DIFF")
  _ = (ws / "diagnostics" / "git_status.txt").write_text("clean")
  store = FakeStore()

  out = promote(store, _record("flipt__flipt-1", "1706-0"), ws)

  prefix = f"{_SWEEP}/flipt__flipt-1/r0/rollout/a0"
  assert out.artifact_keys == {
      "patch.diff": f"{prefix}/patch.diff",
      "diagnostics/git_status.txt": f"{prefix}/diagnostics/git_status.txt",
  }
  assert store.objects[f"{prefix}/diagnostics/git_status.txt"] == b"clean"


def test_index_aggregates_a_sweeps_shards(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  a = tmp_path / "a.txt"
  _ = a.write_text("a")
  persist(store, _record("inst-a", "1000-0"), {"a.txt": a})
  persist(store, _record("inst-b", "1001-0"), {"a.txt": a})
  # a different sweep must not leak in
  persist(store, _record("inst-c", "1002-0"), {"a.txt": a})

  records = index(store, _SWEEP)
  assert {r.instance_id for r in records} == {"inst-a", "inst-b", "inst-c"}
  assert all(r.artifact_keys["a.txt"].startswith(f"{_SWEEP}/") for r in records)
