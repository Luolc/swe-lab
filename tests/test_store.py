"""Tests for the `Store` seam: `FilesystemStore` + the vendor registry."""

from __future__ import annotations

from pathlib import Path

from etils import epath
import pytest

from swe_lab.sandbox import (
    AttemptRecord,
    build_store,
    FilesystemStore,
    registered_stores,
    SandboxError,
    Store,
)


def _record(
    sweep: str = "sw",
    instance: str = "inst",
    ts: str = "ts",
    *,
    task: str = "rollout",
    rollout_id: int = 0,
    attempt: int = 0,
) -> AttemptRecord:
  return AttemptRecord(
      sweep_id=sweep,
      instance_id=instance,
      task=task,
      rollout_id=rollout_id,
      attempt=attempt,
      run_ts=ts,
      status="SUCCESS",
      tier="formal",
      backend="host",
  )


def test_put_get_roundtrip(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  src = tmp_path / "patch.diff"
  _ = src.write_text("DIFF")
  store.put("sw/inst/r0/a0/patch.diff", src)
  out = tmp_path / "back.diff"
  store.get("sw/inst/r0/a0/patch.diff", out)
  assert out.read_text() == "DIFF"


def test_get_missing_key_raises(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  with pytest.raises(SandboxError, match="not found"):
    store.get("sw/inst/r0/a0/nope", tmp_path / "x")


def test_read_manifests_returns_the_whole_sweep(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  store.append_manifest(_record(instance="a"))
  store.append_manifest(_record(instance="b"))
  store.append_manifest(_record(sweep="other", instance="c"))
  records = store.read_manifests("sw")
  assert [r.instance_id for r in records] == ["a", "b"]  # only this sweep
  assert store.read_manifests("missing") == []  # unknown sweep → empty


def test_rollouts_and_attempts_get_distinct_shards(tmp_path: Path):
  # The point of ADR-0004: K samples of one instance coexist, and a retry of a
  # given rollout is its own shard — none of them overwrite each other.
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  for rollout_id in (0, 1, 2):
    store.append_manifest(_record(rollout_id=rollout_id))
  store.append_manifest(_record(rollout_id=1, attempt=1))
  identities = [(r.rollout_id, r.attempt) for r in store.read_manifests("sw")]
  assert identities == [(0, 0), (1, 0), (1, 1), (2, 0)]


def test_read_manifests_sorts_rollouts_numerically(tmp_path: Path):
  # 10 must sort after 2 — a lexical key sort would interleave them.
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  for rollout_id in (10, 2, 1):
    store.append_manifest(_record(rollout_id=rollout_id))
  assert [r.rollout_id for r in store.read_manifests("sw")] == [1, 2, 10]


def test_read_manifest_targets_one_rollout(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  store.append_manifest(_record(rollout_id=0))
  store.append_manifest(_record(rollout_id=1, attempt=0))
  store.append_manifest(_record(rollout_id=1, attempt=1))
  store.append_manifest(_record(instance="other", rollout_id=1))
  attempts = store.read_manifest("sw", "inst", 1)
  assert [r.attempt for r in attempts] == [0, 1]  # this rollout's tries only
  assert store.read_manifest("sw", "inst", 9) == []  # never ran


def test_reappending_the_same_identity_overwrites(tmp_path: Path):
  # The key carries no timestamp, so re-running an attempt is idempotent.
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  store.append_manifest(_record(ts="first"))
  store.append_manifest(_record(ts="second"))
  records = store.read_manifests("sw")
  assert len(records) == 1
  assert records[0].run_ts == "second"  # recorded, but not part of the key


def test_build_store_filesystem(tmp_path: Path):
  store = build_store("filesystem", root=tmp_path / "store")
  assert isinstance(store, FilesystemStore)
  assert isinstance(store, Store)


def test_build_store_unknown_name_raises():
  with pytest.raises(SandboxError, match="unknown store"):
    _ = build_store("s3")  # not registered until task 13


def test_filesystem_is_registered():
  assert "filesystem" in registered_stores()


def test_read_manifest_narrows_to_one_task(tmp_path: Path):
  # The resume/edge read: one task's attempts, not the whole rollout's.
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  store.append_manifest(_record(task="rollout"))
  store.append_manifest(_record(task="eval", attempt=0))
  store.append_manifest(_record(task="eval", attempt=1))
  attempts = store.read_manifest("sw", "inst", 0, task="eval")
  assert [(r.task, r.attempt) for r in attempts] == [("eval", 0), ("eval", 1)]
  # None keeps the aggregation shape: every task of the rollout
  every = store.read_manifest("sw", "inst", 0)
  assert [(r.task, r.attempt) for r in every] == [
      ("eval", 0),
      ("eval", 1),
      ("rollout", 0),
  ]


def test_two_tasks_same_artifact_name_get_distinct_keys(tmp_path: Path):
  # The write-side answer to "two tasks produce patch.diff": the task segment
  # separates them by construction.
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  src = tmp_path / "patch.diff"
  _ = src.write_text("A")
  store.put("sw/inst/r0/task1/a0/patch.diff", src)
  _ = src.write_text("B")
  store.put("sw/inst/r0/task2/a0/patch.diff", src)
  assert store.get_bytes("sw/inst/r0/task1/a0/patch.diff") == b"A"
  assert store.get_bytes("sw/inst/r0/task2/a0/patch.diff") == b"B"


def test_put_bytes_get_bytes_roundtrip(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  store.put_bytes("sw/inst/r0/eval/complete.json", b'{"outcome": "x"}')
  assert store.get_bytes("sw/inst/r0/eval/complete.json") == b'{"outcome": "x"}'
  with pytest.raises(SandboxError, match="not found"):
    _ = store.get_bytes("sw/inst/r0/eval/nope")
