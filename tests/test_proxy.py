"""Tests for the reverse-proxy helpers (no network, no real proxy start)."""

from __future__ import annotations

from pathlib import Path

from swe_lab.core.agent.proxy import (
    build_proxy,
    DEFAULT_BASE_PORT,
    port_for_index,
    proxy_binary_path,
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
