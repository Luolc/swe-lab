"""Tests for the cc-reverse-proxy asset builder (no Go toolchain, no network).

The proxy binary is provisioned exactly like the agent binary — built/cached on
the host, mounted into the sandbox — so what is worth pinning here is the
resolution: where the source is looked for, what counts as its version, and
that a cached build is reused rather than rebuilt.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from swe_lab.harnesses.claude_code.proxy import (
    ensure_proxy_binary,
    proxy_binary_path,
    PROXY_SOURCE_ENV,
    proxy_source_path,
    proxy_source_version,
    SANDBOX_PLATFORM,
)
from swe_lab.pipelines.related_files.host_proxy import (
    proxy_binary_path as host_proxy_binary_path,
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


def test_an_existing_host_binary_does_not_wedge_the_first_proxied_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A file at `<cache>/bin/cc-reverse-proxy` neither blocks nor is disturbed.

  That file is `pipelines.related_files.host_proxy`'s property and may be in
  use, so this module builds elsewhere and leaves it alone.
  """
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  host = Path(str(host_proxy_binary_path(tmp_path)))
  host.parent.mkdir(parents=True, exist_ok=True)
  _ = host.write_text("the host-native build\n")

  def _fake_build(_source: object, binary: object) -> None:
    path = Path(str(binary))
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("#!/bin/true\n")

  monkeypatch.setattr("swe_lab.harnesses.claude_code.proxy._build", _fake_build)

  built = ensure_proxy_binary(repo_root=tmp_path)

  assert built.read_text() == "#!/bin/true\n"
  assert host.is_file() and host.read_text() == "the host-native build\n"


def test_the_sandbox_cache_never_collides_with_the_host_proxy_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The two proxy artifacts must not share a path.

  `host_proxy` writes a host-native binary as a *file* at
  `<cache>/bin/cc-reverse-proxy`; this module caches a cross-compiled
  linux/amd64 build under a versioned *directory* tree. Nesting the second
  under the first made one component's directory the other's file, so whichever
  ran second destroyed or was blocked by the first.

  Pinned as a relationship between the two paths rather than as a literal
  string, so renaming either one cannot quietly recreate the overlap.
  """
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  host = Path(str(host_proxy_binary_path(tmp_path)))
  sandbox = Path(
      str(proxy_binary_path(proxy_source_version(tmp_path), repo_root=tmp_path))
  )

  assert sandbox != host
  assert host not in sandbox.parents
  assert sandbox not in host.parents


def test_building_the_sandbox_binary_leaves_the_host_binary_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A first proxied run must not disturb a host-native build sitting there.

  The concrete failure this pins: W1's pipeline builds the host binary, a
  sandbox capture starts, and the host binary is gone before `Popen` reaches
  it.
  """
  source = _source(tmp_path, "package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))
  host = Path(str(host_proxy_binary_path(tmp_path)))
  host.parent.mkdir(parents=True, exist_ok=True)
  _ = host.write_text("#!/bin/true  <- the host-native build\n")

  cached = proxy_binary_path(proxy_source_version(tmp_path), repo_root=tmp_path)
  cached.parent.mkdir(parents=True, exist_ok=True)
  _ = cached.write_text("#!/bin/true\n")
  _ = ensure_proxy_binary(repo_root=tmp_path)

  assert host.is_file(), "the sandbox path removed the host proxy binary"
  assert host.read_text() == "#!/bin/true  <- the host-native build\n"
