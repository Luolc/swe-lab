"""Tests for the cc-reverse-proxy asset builder (no Go toolchain, no network).

The proxy binary is provisioned exactly like the agent binary — built/cached on
the host, mounted into the sandbox — so what is worth pinning here is the
resolution: where the source is looked for, what counts as its version, and
that a cached build is reused rather than rebuilt.
"""

from __future__ import annotations

import hashlib
import itertools
import os
from pathlib import Path
import threading

from etils import epath
import pytest

from swe_lab.harnesses.claude_code.proxy import (
    _clear_legacy_cache_entry,
    ensure_proxy_binary,
    proxy_binary_path,
    PROXY_SOURCE_ENV,
    proxy_source_path,
    proxy_source_version,
    SANDBOX_PLATFORM,
)


def _source(tmp_path: Path, body: str) -> Path:
  """Plant a stand-in reverse_proxy.go and point the env override at it."""
  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text(body)
  return source


def test_proxy_source_defaults_to_sibling_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
  # cc-reverse-proxy is a standalone project, not a submodule: default is a
  # sibling of the repo, so ../cc-reverse-proxy/reverse_proxy.go.
  monkeypatch.delenv(PROXY_SOURCE_ENV, raising=False)
  assert proxy_source_path(Path("/x/y/swe-lab")) == Path(
      "/x/y/cc-reverse-proxy/reverse_proxy.go"
  )


def test_proxy_source_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv(PROXY_SOURCE_ENV, "/opt/rp/reverse_proxy.go")
  # the override wins and the repo root is irrelevant
  assert proxy_source_path(Path("/anywhere")) == Path(
      "/opt/rp/reverse_proxy.go"
  )


def test_the_version_is_the_source_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # There is no release string to pin — a single unversioned Go file — so the
  # content hash stands in for one.
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  assert (
      proxy_source_version(tmp_path)
      == hashlib.sha256(b"package main\n").hexdigest()
  )


def test_a_missing_source_says_how_to_supply_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(tmp_path / "nope.go"))
  with pytest.raises(FileNotFoundError, match=PROXY_SOURCE_ENV):
    _ = proxy_source_version(tmp_path)


def test_editing_the_source_invalidates_the_cached_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # The guard the old fixed cache path did not have: a binary built from an
  # earlier source must not be silently reused as if it were the current one.
  source = _source(tmp_path, "package main // v1\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  first = proxy_binary_path(proxy_source_version(tmp_path), repo_root=tmp_path)
  _ = source.write_text("package main // v2\n")
  second = proxy_binary_path(proxy_source_version(tmp_path), repo_root=tmp_path)
  assert first != second
  # …and the platform is in the path too: the sandbox is linux/amd64 whatever
  # the host that built it is.
  assert first.parent.name == SANDBOX_PLATFORM


def test_a_cached_build_is_reused_without_invoking_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  cached = proxy_binary_path(proxy_source_version(tmp_path), repo_root=tmp_path)
  cached.parent.mkdir(parents=True, exist_ok=True)
  _ = cached.write_text("#!/bin/true\n")  # stand in for a real build

  assert ensure_proxy_binary(repo_root=tmp_path) == cached


def test_a_dest_gets_its_own_executable_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # The other half of the materializer contract: `dest=None` caches, a path
  # installs exactly there (what a sandbox that *is* the filesystem wants).
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  cached = proxy_binary_path(proxy_source_version(tmp_path), repo_root=tmp_path)
  cached.parent.mkdir(parents=True, exist_ok=True)
  _ = cached.write_text("#!/bin/true\n")

  dest = tmp_path / "sandbox" / "cc-reverse-proxy"
  assert ensure_proxy_binary(dest=dest, repo_root=tmp_path) == dest
  assert dest.read_text() == "#!/bin/true\n"
  assert dest.stat().st_mode & 0o111  # executable, or the sandbox cannot run it


def test_a_pre_sandbox_cache_file_does_not_wedge_the_first_proxied_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # Before the proxy moved into the sandbox, the build was cached as a *file*
  # at `<cache>/bin/cc-reverse-proxy`. Today's version-keyed layout needs that
  # exact path to be a *directory*, so every machine that ran the host-side
  # proxy had a first proxied run that died in `mkdir` with NotADirectoryError.
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  legacy = tmp_path / ".cache" / "bin" / "cc-reverse-proxy"
  legacy.parent.mkdir(parents=True, exist_ok=True)
  _ = legacy.write_text("a binary built by the pre-#264 host-side proxy\n")

  def _fake_build(_source: object, binary: object) -> None:
    path = Path(str(binary))
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("#!/bin/true\n")

  monkeypatch.setattr("swe_lab.harnesses.claude_code.proxy._build", _fake_build)

  built = ensure_proxy_binary(repo_root=tmp_path)

  assert built.read_text() == "#!/bin/true\n"
  assert legacy.is_dir()  # the squatting file is gone, the namespace is a dir


def _peer_wins(monkeypatch: pytest.MonkeyPatch, then_mkdir: bool) -> None:
  """Make the removal lose the race to a peer that already did the work.

  Both removal seams are hooked -- `os.remove` and the path object's own
  `unlink` -- because which one the implementation calls is exactly the kind of
  detail that changes; hooking only today's gives a test that keeps passing
  while quietly testing nothing.

  Each hook then delegates to *its own* original, never to the other. That
  matters: routing `unlink` through `os.remove` would hide the difference
  between them, and the difference is the point -- `os.remove` refuses a
  directory, `epath`'s `unlink` deletes an empty one.
  """
  real_remove = os.remove
  real_unlink = type(epath.Path("/tmp")).unlink

  def peer_did_the_work(path: str | os.PathLike[str]) -> None:
    real_remove(path)
    if then_mkdir:
      Path(str(path)).mkdir(parents=True, exist_ok=True)

  def hooked_remove(path: str | os.PathLike[str]) -> None:
    peer_did_the_work(path)
    real_remove(path)

  def hooked_unlink(self: epath.Path) -> None:
    peer_did_the_work(self)
    _ = real_unlink(self)

  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.proxy.os.remove", hooked_remove
  )
  monkeypatch.setattr(type(epath.Path("/tmp")), "unlink", hooked_unlink)


def test_a_peer_clearing_the_same_legacy_file_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # Two first proxied runs on one machine both see the legacy file and both
  # decide to remove it. Whichever loses the race finds it already gone, and
  # must carry on: it is racing a peer doing exactly the right thing, and the
  # post-condition it wanted -- no file at that path -- already holds.
  #
  # The race window is injected rather than raced for. A thread barrier would
  # reproduce it only sometimes, and a test that reproduces a race
  # intermittently reports "fixed" most of the time either way.
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  legacy = tmp_path / ".cache" / "bin" / "cc-reverse-proxy"
  legacy.parent.mkdir(parents=True, exist_ok=True)
  _ = legacy.write_text("built by the pre-sandbox host-side proxy\n")

  _peer_wins(monkeypatch, then_mkdir=False)

  _clear_legacy_cache_entry(tmp_path)  # must not raise


def test_a_peer_that_already_rebuilt_the_directory_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # The other losing interleaving: the peer removed the file *and* created the
  # versioned directory before this process reached its own removal.
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  legacy = tmp_path / ".cache" / "bin" / "cc-reverse-proxy"
  legacy.parent.mkdir(parents=True, exist_ok=True)
  _ = legacy.write_text("built by the pre-sandbox host-side proxy\n")

  _peer_wins(monkeypatch, then_mkdir=True)

  _clear_legacy_cache_entry(tmp_path)

  # The peer's directory must survive. This is the sharper half of the bug:
  # `epath`'s unlink deletes an empty directory rather than refusing, so a
  # loser using it would silently destroy the cache the winner just built.
  assert legacy.is_dir()


def test_two_concurrent_first_runs_do_not_wedge_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Two real threads, both past the check before either removes.

  The interleaving is forced rather than hoped for: a barrier inside the first
  two `is_dir` calls holds both threads until both have decided the legacy file
  is there. A test that merely starts two threads and hopes reproduces this
  perhaps one run in a thousand, and reports "fixed" the rest of the time.
  """
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  namespace = tmp_path / ".cache" / "bin" / "cc-reverse-proxy"
  namespace.parent.mkdir(parents=True, exist_ok=True)
  _ = namespace.write_text("built by the pre-sandbox host-side proxy\n")

  barrier = threading.Barrier(2, timeout=10)
  checks = itertools.count()
  real_is_dir = type(epath.Path("/tmp")).is_dir

  def synced_is_dir(self: epath.Path) -> bool:
    result = bool(real_is_dir(self))
    # Only the two pre-removal checks synchronise; the post-condition check
    # must not block on a barrier whose parties have already gone.
    if str(self) == str(namespace) and next(checks) < 2:
      _ = barrier.wait()
    return result

  monkeypatch.setattr(type(epath.Path("/tmp")), "is_dir", synced_is_dir)

  failures: list[BaseException] = []

  def clear() -> None:
    try:
      _clear_legacy_cache_entry(tmp_path)
    except BaseException as error:  # noqa: BLE001 — the point is to catch any
      failures.append(error)

  threads = [threading.Thread(target=clear) for _ in range(2)]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=15)

  assert not failures, f"a racing peer raised: {failures!r}"
  # os.path, not the patched is_dir: the assertion must not re-enter the hook.
  assert not os.path.exists(namespace) or os.path.isdir(namespace)
