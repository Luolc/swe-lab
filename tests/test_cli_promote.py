"""Tests for the `promote` CLI: push a debug workspace into T1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.promote as promote_mod

runner = CliRunner()


def test_promote_uploads_workspace_and_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  ws = tmp_path / "ws"
  (ws / "diagnostics").mkdir(parents=True)
  _ = (ws / "patch.diff").write_text("DIFF")
  _ = (ws / "diagnostics" / "git.txt").write_text("clean")
  monkeypatch.setattr(promote_mod, "find_repo_root", lambda: tmp_path)

  result = runner.invoke(
      app,
      [
          "promote",
          "acme__widget-1",
          "--workspace",
          str(ws),
          "--sweep",
          "sw1",
      ],
  )

  assert result.exit_code == 0
  store = tmp_path / ".cache" / "store" / "runs" / "sw1" / "acme__widget-1"
  shards = list(store.rglob("run.json"))
  assert len(shards) == 1
  keys = json.loads(result.output)["keys"]
  assert "patch.diff" in keys and "diagnostics/git.txt" in keys


def test_promote_missing_workspace_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  monkeypatch.setattr(promote_mod, "find_repo_root", lambda: tmp_path)
  result = runner.invoke(
      app,
      ["promote", "x", "--workspace", str(tmp_path / "nope")],
  )
  assert result.exit_code != 0
