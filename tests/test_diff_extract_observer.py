"""Tests for the shared DiffExtractObserver (no Docker; extraction faked)."""

from pathlib import Path

from swe_lab.sandbox import Sandbox, SandboxSpec
from swe_lab.sandbox.observers.diff_extract import (
    DiffExtractObserver,
    EXTRACT_SCRIPT_NAME,
    PATCH_NAME,
    RAW_PATCH_NAME,
)
from swe_lab.sandbox.testing import FakeBackend


def _sandbox(workspace: Path, backend: FakeBackend) -> Sandbox:
  return Sandbox(
      label="x",
      spec=SandboxSpec("x", "img:tag", "/app", "base"),
      workspace=workspace,
      backend=backend,
      handle="fake",
  )


def test_extracts_cleans_and_registers(tmp_path: Path):
  # Simulate what the in-container extraction writes into the workspace.
  raw = "diff --git a/x b/x\n+hello\n"
  _ = (tmp_path / RAW_PATCH_NAME).write_text(raw)
  backend = FakeBackend()
  obs = DiffExtractObserver()

  contribution = obs.before_destroy(_sandbox(tmp_path, backend))

  assert obs.patch == raw
  assert obs.is_empty is False
  assert obs.binary_stripped is False  # a pure-text patch
  assert (tmp_path / PATCH_NAME).read_text() == raw
  assert contribution is not None
  assert contribution.artifacts["patch"] == tmp_path / PATCH_NAME
  assert contribution.artifacts["patch_raw"] == tmp_path / RAW_PATCH_NAME
  # the extraction script is staged (persisted for audit) and run
  extract = (tmp_path / EXTRACT_SCRIPT_NAME).read_text()
  assert 'cd "$SANDBOX_WORKSPACE"' in extract
  assert RAW_PATCH_NAME in extract  # git diff … > patch.raw.diff
  assert backend.scripts == [EXTRACT_SCRIPT_NAME]


def test_empty_patch(tmp_path: Path):
  _ = (tmp_path / RAW_PATCH_NAME).write_bytes(b"")
  obs = DiffExtractObserver()
  _ = obs.before_destroy(_sandbox(tmp_path, FakeBackend()))
  assert obs.is_empty is True
  assert obs.patch == ""


def test_absent_raw_patch_is_empty(tmp_path: Path):
  obs = DiffExtractObserver()  # no raw file written at all
  contribution = obs.before_destroy(_sandbox(tmp_path, FakeBackend()))
  assert obs.patch == ""
  assert obs.is_empty is True
  assert contribution is not None
  assert "patch_raw" not in contribution.artifacts  # nothing produced
