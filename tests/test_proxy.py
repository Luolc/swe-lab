"""Tests for the reverse-proxy helpers (no network, no real proxy start)."""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_lab.harnesses.claude_code.proxy import (
    build_proxy,
    DEFAULT_BASE_PORT,
    port_for_index,
    proxy_binary_path,
    PROXY_SOURCE_ENV,
    proxy_source_path,
)


def test_port_for_index() -> None:
  assert port_for_index(0) == DEFAULT_BASE_PORT
  assert port_for_index(27) == DEFAULT_BASE_PORT + 27
  assert port_for_index(5, base_port=30000) == 30005


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


def test_proxy_binary_path(tmp_path: Path) -> None:
  path = proxy_binary_path(tmp_path)
  assert path == tmp_path / ".cache" / "bin" / "cc-reverse-proxy"


def test_build_proxy_skips_when_binary_exists(tmp_path: Path) -> None:
  # Pre-create the binary so build_proxy returns it without invoking `go`.
  binary = proxy_binary_path(tmp_path)
  binary.parent.mkdir(parents=True, exist_ok=True)
  _ = binary.write_text("#!/bin/true\n")

  assert build_proxy(tmp_path) == binary
