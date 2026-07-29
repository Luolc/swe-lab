"""Tests for the `Store` seam: `FilesystemStore` + the vendor registry."""

from __future__ import annotations

from pathlib import Path

from etils import epath
import pytest

from swe_lab.sandbox import (
    build_store,
    FilesystemStore,
    registered_stores,
    RunRecord,
    SandboxError,
    Store,
)


def _record(
    sweep: str = "sw", instance: str = "inst", ts: str = "ts"
) -> RunRecord:
  return RunRecord(
      sweep_id=sweep,
      instance_id=instance,
      run_ts=ts,
      status="SUCCESS",
      tier="formal",
      backend="host",
  )


def test_put_get_roundtrip(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  src = tmp_path / "patch.diff"
  _ = src.write_text("DIFF")
  store.put("runs/sw/inst/ts/patch.diff", src)
  out = tmp_path / "back.diff"
  store.get("runs/sw/inst/ts/patch.diff", out)
  assert out.read_text() == "DIFF"


def test_get_missing_key_raises(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  with pytest.raises(SandboxError, match="not found"):
    store.get("runs/sw/inst/ts/nope", tmp_path / "x")


def test_append_and_read_manifest(tmp_path: Path):
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  store.append_manifest(_record(ts="a"))
  store.append_manifest(_record(ts="b"))
  store.append_manifest(_record(sweep="other", ts="c"))
  records = store.read_manifest("sw")
  assert [r.run_ts for r in records] == ["a", "b"]  # only this sweep, sorted
  assert store.read_manifest("missing") == []  # unknown sweep → empty


def test_build_store_filesystem(tmp_path: Path):
  store = build_store("filesystem", root=tmp_path / "store")
  assert isinstance(store, FilesystemStore)
  assert isinstance(store, Store)


def test_build_store_unknown_name_raises():
  with pytest.raises(SandboxError, match="unknown store"):
    _ = build_store("s3")  # not registered until task 13


def test_filesystem_is_registered():
  assert "filesystem" in registered_stores()
