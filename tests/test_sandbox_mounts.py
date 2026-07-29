"""Tests for sandbox mounts + resources: merging, modes, and staging."""

from pathlib import Path

from etils import epath
import pytest

from swe_lab.sandbox import (
    Inline,
    LocalFile,
    merge_mounts,
    Mount,
    SandboxError,
    SandboxSpec,
)
from swe_lab.sandbox.testing import FakeSandbox

SPEC = SandboxSpec("inst", "img:tag", "/app", "abc123")


def test_merge_disjoint_and_duplicate():
  a = {"a.sh": Mount(Inline(b"a"))}
  b = {"b.sh": Mount(Inline(b"b"))}
  merged = merge_mounts(a, b)
  assert set(merged) == {"a.sh", "b.sh"}
  with pytest.raises(SandboxError, match="duplicate mount target 'a.sh'"):
    merge_mounts(a, {"a.sh": Mount(Inline(b"other"))})


def test_mount_mode_reflects_executable_and_read_only():
  assert Mount(Inline(b"x")).mode == 0o644
  assert Mount(Inline(b"x"), executable=True).mode == 0o755
  assert Mount(Inline(b"x"), read_only=True).mode == 0o444
  assert Mount(Inline(b"x"), executable=True, read_only=True).mode == 0o555


def test_mount_stages_inline_localfile_and_executable(tmp_path: Path):
  # Transfer is the sandbox's job now (Resource is pure data): mount() places
  # each resource, honoring the executable bit. FakeSandbox does this against a
  # real local workspace, so it exercises genuine filesystem behavior.
  src = tmp_path / "big.bin"
  _ = src.write_bytes(b"binary!")
  workspace = tmp_path / "ws"
  sb = FakeSandbox(spec=SPEC, workspace=epath.Path(workspace))
  sb.up()
  sb.mount(
      {
          "run.sh": Mount(Inline(b"#!/bin/bash\n"), executable=True),
          "nested/dir/parser.py": Mount(Inline(b"print()")),
          "agent": Mount(LocalFile(epath.Path(src))),
      }
  )
  assert (workspace / "run.sh").read_bytes() == b"#!/bin/bash\n"
  assert (workspace / "run.sh").stat().st_mode & 0o111  # executable
  assert (workspace / "nested/dir/parser.py").read_bytes() == b"print()"
  assert (workspace / "agent").read_bytes() == b"binary!"
  assert not (workspace / "agent").stat().st_mode & 0o100
