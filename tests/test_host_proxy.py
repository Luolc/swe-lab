"""Tests for the host-side proxy the W1 annotation pipeline still runs.

That pipeline runs its agent as a host subprocess, so its proxy stays a host
process on a per-run port. (The engine's rollout path runs both in the sandbox
— see ``test_proxy.py``.)
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from etils import epath
import pytest

from swe_lab.pipelines.related_files.host_proxy import (
    build_proxy,
    DEFAULT_BASE_PORT,
    port_for_index,
    ReverseProxy,
)


def test_port_for_index() -> None:
  assert port_for_index(0) == DEFAULT_BASE_PORT
  assert port_for_index(27) == DEFAULT_BASE_PORT + 27
  assert port_for_index(5, base_port=30000) == 30005


def _plant_source(
    tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
  """Plant a stand-in reverse_proxy.go and point the env override at it."""
  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text(body)
  monkeypatch.setenv("CC_REVERSE_PROXY_SRC", str(source))
  return source


def _go_writing_the_source_back(
    monkeypatch: pytest.MonkeyPatch, builds: list[str]
) -> None:
  """Stub `go build` with one that stamps its source into the binary.

  Makes "which revision is this binary" observable without a Go toolchain.
  """

  def _fake_go(
      argv: list[str], **_kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    source = Path(argv[-1])
    out = Path(argv[argv.index("-o") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    _ = out.write_text(source.read_text())
    builds.append(source.read_text())
    return subprocess.CompletedProcess(argv, 0, "", "")

  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.proxy.subprocess.run", _fake_go
  )


def test_the_binary_path_is_keyed_by_the_source_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Editing the source moves the path, which is *how* staleness is caught."""
  builds: list[str] = []
  _go_writing_the_source_back(monkeypatch, builds)
  _ = _plant_source(tmp_path, "package main // v1\n", monkeypatch)
  first = build_proxy(tmp_path)
  _ = _plant_source(tmp_path, "package main // v2\n", monkeypatch)
  second = build_proxy(tmp_path)

  assert first != second
  assert first.name == second.name == "cc-reverse-proxy"


def test_a_binary_built_from_an_older_source_is_not_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A changed source must not be served the build made for the old one.

  Asserted on the bytes of the returned binary rather than on its path: the
  claim is that the caller executes the current source, and a path that merely
  differs would not establish it.
  """
  builds: list[str] = []
  _go_writing_the_source_back(monkeypatch, builds)

  _ = _plant_source(tmp_path, "package main // before redaction\n", monkeypatch)
  first = build_proxy(tmp_path)
  assert first.read_text() == "package main // before redaction\n"

  _ = _plant_source(tmp_path, "package main // redacts\n", monkeypatch)
  second = build_proxy(tmp_path)

  assert second != first, "the stale binary's path was handed back"
  assert second.read_text() == "package main // redacts\n"
  assert len(builds) == 2, "the changed source did not trigger a rebuild"


def test_an_unchanged_source_reuses_the_cached_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The converse: an unchanged source is reused, not rebuilt.

  Without it, a function that rebuilt unconditionally would satisfy the test
  above while defeating the point of a cache.
  """
  builds: list[str] = []
  _go_writing_the_source_back(monkeypatch, builds)
  _ = _plant_source(tmp_path, "package main\n", monkeypatch)

  first = build_proxy(tmp_path)
  second = build_proxy(tmp_path)

  assert first == second
  assert len(builds) == 1, "an unchanged source was rebuilt"


def test_a_missing_source_says_how_to_supply_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("CC_REVERSE_PROXY_SRC", str(tmp_path / "nope.go"))
  with pytest.raises(FileNotFoundError, match="CC_REVERSE_PROXY_SRC"):
    _ = build_proxy(tmp_path)


def test_the_start_path_spawns_the_binary_it_was_handed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """`build_proxy` -> `ReverseProxy` is the call sequence every caller uses."""
  _ = _plant_source(tmp_path, "package main\n", monkeypatch)

  def _fake_go(
      argv: list[str], **_kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    out = Path(argv[argv.index("-o") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    _ = out.write_text("#!/bin/sh\nsleep 30\n")
    out.chmod(0o755)
    return subprocess.CompletedProcess(argv, 0, "", "")

  monkeypatch.setattr(
      "swe_lab.harnesses.claude_code.proxy.subprocess.run", _fake_go
  )

  def _skip_wait(_self: ReverseProxy) -> None:
    """Skip the listen wait: the stub never listens, only the spawn matters."""

  monkeypatch.setattr(ReverseProxy, "_wait_until_listening", _skip_wait)

  with ReverseProxy(
      port=39998,
      output_path=epath.Path(tmp_path / "b.jsonl"),
      binary=build_proxy(tmp_path),
  ):
    pass  # Popen succeeded on the path the builder returned
