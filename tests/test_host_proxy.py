"""Tests for the host-side proxy the W1 annotation pipeline still runs.

That pipeline runs its agent as a host subprocess, so its proxy stays a host
process on a per-run port. (The engine's rollout path runs both in the sandbox
— see ``test_proxy.py``.)
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from etils import epath
import pytest

from swe_lab.pipelines.related_files.host_proxy import (
    build_proxy,
    DEFAULT_BASE_PORT,
    port_for_index,
    proxy_binary_path,
    ReverseProxy,
)


def test_port_for_index() -> None:
  assert port_for_index(0) == DEFAULT_BASE_PORT
  assert port_for_index(27) == DEFAULT_BASE_PORT + 27
  assert port_for_index(5, base_port=30000) == 30005


def test_proxy_binary_path(tmp_path: Path) -> None:
  path = proxy_binary_path(tmp_path)
  assert path == tmp_path / ".cache" / "bin" / "cc-reverse-proxy"


def test_build_proxy_skips_when_binary_exists(tmp_path: Path) -> None:
  # Pre-create the binary so build_proxy returns it without invoking `go`.
  binary = proxy_binary_path(tmp_path)
  binary.parent.mkdir(parents=True, exist_ok=True)
  _ = binary.write_text("#!/bin/true\n")

  assert build_proxy(tmp_path) == binary


def _residue(binary: object) -> None:
  """Leave the state a machine has after running the old sandbox cache layout.

  That layout nested `<version>/<platform>/cc-reverse-proxy` under this exact
  path, so what is left behind is a *directory* where this pipeline needs a
  file.
  """
  path = Path(str(binary)) / "abc123" / "linux-amd64"
  path.mkdir(parents=True, exist_ok=True)
  _ = (path / "cc-reverse-proxy").write_text("#!/bin/true\n")


def test_build_proxy_clears_a_sandbox_cache_directory_left_at_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A directory at the binary path must be cleared, not built into.

  `go build -o <dir>` does not fail — it writes *into* the directory and
  reports success — so leaving it would hand back a path that is still a
  directory, and the error would surface much later and somewhere else.
  """
  binary = proxy_binary_path(tmp_path)
  _residue(binary)
  assert binary.is_dir()  # the state the machine is actually in

  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text("package main\n")
  monkeypatch.setenv("CC_REVERSE_PROXY_SRC", str(source))

  def _fake_go(
      argv: list[str], **_kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    _ = Path(argv[argv.index("-o") + 1]).write_text("#!/bin/sh\nexit 0\n")
    return subprocess.CompletedProcess(argv, 0, "", "")

  monkeypatch.setattr(
      "swe_lab.pipelines.related_files.host_proxy.subprocess.run", _fake_go
  )

  built = build_proxy(tmp_path)

  assert Path(str(built)).is_file(), "build_proxy returned a directory"
  assert not Path(str(built)).is_dir()


def test_the_start_path_spawns_a_file_after_the_residue_is_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Pinned where the failure actually appears: `Popen`, not the build.

  Handed a directory, `ReverseProxy.__enter__` raises `PermissionError` far
  from the cache layout that caused it. Both halves are asserted here — the
  raw failure, and that the built path no longer produces it.
  """
  binary = proxy_binary_path(tmp_path)
  _residue(binary)

  # The failure as it reaches a caller today, given the residue directory.
  with (
      pytest.raises(PermissionError),
      ReverseProxy(
          port=39997,
          output_path=epath.Path(tmp_path / "a.jsonl"),
          binary=binary,
      ),
  ):
    pass

  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text("package main\n")
  monkeypatch.setenv("CC_REVERSE_PROXY_SRC", str(source))

  def _fake_go(
      argv: list[str], **_kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    out = Path(argv[argv.index("-o") + 1])
    _ = out.write_text("#!/bin/sh\nsleep 30\n")
    out.chmod(0o755)
    return subprocess.CompletedProcess(argv, 0, "", "")

  monkeypatch.setattr(
      "swe_lab.pipelines.related_files.host_proxy.subprocess.run", _fake_go
  )

  def _skip_wait(_self: ReverseProxy) -> None:
    """Skip the listen wait: the stub never listens, only the spawn matters."""

  monkeypatch.setattr(ReverseProxy, "_wait_until_listening", _skip_wait)

  built = build_proxy(tmp_path)
  with ReverseProxy(
      port=39998,
      output_path=epath.Path(tmp_path / "b.jsonl"),
      binary=built,
  ):
    pass  # Popen succeeded: the same start path no longer hits a directory


def test_a_peer_clearing_the_same_residue_first_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Two runs reach the migration together; the loser must not fail.

  The window is injected rather than raced for. A thread barrier was tried
  first and did **not** reproduce this reliably -- both threads reached the
  check, and the removals still serialised -- so a barrier here would have been
  a test that looks concurrent and passes either way. Injecting the peer's
  removal into the window makes the interleaving happen on every run.

  Driven through the real `build_proxy`: an earlier version inlined the removal
  and passed against a non-idempotent implementation, because it was exercising
  its own copy of the logic rather than the shipped one.
  """
  binary = proxy_binary_path(tmp_path)
  _residue(binary)

  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text("package main\n")
  monkeypatch.setenv("CC_REVERSE_PROXY_SRC", str(source))

  def _fake_go(
      argv: list[str], **_kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    out = Path(argv[argv.index("-o") + 1])
    _ = out.write_text("#!/bin/sh\nexit 0\n")
    return subprocess.CompletedProcess(argv, 0, "", "")

  monkeypatch.setattr(
      "swe_lab.pipelines.related_files.host_proxy.subprocess.run", _fake_go
  )

  real_rmtree = type(epath.Path("/tmp")).rmtree

  def peer_removed_it_first(self: epath.Path, missing_ok: bool = False) -> None:
    real_rmtree(self, missing_ok=True)  # the peer wins inside the window
    real_rmtree(self, missing_ok=missing_ok)  # our call now finds nothing

  monkeypatch.setattr(type(epath.Path("/tmp")), "rmtree", peer_removed_it_first)

  _ = build_proxy(tmp_path)  # must not raise

  assert not os.path.isdir(str(binary))
  assert os.path.isfile(str(binary))
