"""Tests for sandbox mounts: validation, merging, materialization."""

from pathlib import Path

import pytest

from swe_lab.sandbox import merge_mounts, Mount, SandboxError
from swe_lab.sandbox.mounts import materialize


def test_mount_requires_exactly_one_source():
  with pytest.raises(SandboxError, match="exactly one"):
    Mount()
  with pytest.raises(SandboxError, match="exactly one"):
    Mount(content=b"x", source=Path("/tmp/x"))


def test_merge_disjoint_and_duplicate():
  a = {"a.sh": Mount(content=b"a")}
  b = {"b.sh": Mount(content=b"b")}
  merged = merge_mounts(a, b)
  assert set(merged) == {"a.sh", "b.sh"}
  with pytest.raises(SandboxError, match="duplicate mount target 'a.sh'"):
    merge_mounts(a, {"a.sh": Mount(content=b"other")})


def test_materialize_content_source_and_executable(tmp_path: Path):
  src = tmp_path / "big.bin"
  _ = src.write_bytes(b"binary!")
  workspace = tmp_path / "ws"
  workspace.mkdir()
  materialize(
      {
          "run.sh": Mount(content=b"#!/bin/bash\n", executable=True),
          "nested/dir/parser.py": Mount(content=b"print()"),
          "agent": Mount(source=src),
      },
      workspace,
  )
  assert (workspace / "run.sh").read_bytes() == b"#!/bin/bash\n"
  assert (workspace / "run.sh").stat().st_mode & 0o111  # executable
  assert (workspace / "nested/dir/parser.py").read_bytes() == b"print()"
  assert (workspace / "agent").read_bytes() == b"binary!"
  assert not (workspace / "agent").stat().st_mode & 0o100


def test_materialize_missing_source_raises(tmp_path: Path):
  with pytest.raises(SandboxError, match="does not exist"):
    materialize({"agent": Mount(source=tmp_path / "absent")}, tmp_path)
