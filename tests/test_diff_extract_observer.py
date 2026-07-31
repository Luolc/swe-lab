"""Tests for the shared DiffExtractObserver (no Docker; extraction faked)."""

from pathlib import Path

from etils import epath

from swe_lab.sandbox import SandboxSpec
from swe_lab.sandbox.observers.diff_extract import (
    DiffExtractObserver,
    EXTRACT_SCRIPT_NAME,
    PATCH_NAME,
    RAW_PATCH_NAME,
)
from swe_lab.sandbox.testing import FakeSandbox


def _sandbox(workspace: Path) -> FakeSandbox:
  return FakeSandbox(
      spec=SandboxSpec("x", "img:tag", "/app", "base"),
      workspace=epath.Path(workspace),
  )


def test_extracts_cleans_and_registers(tmp_path: Path):
  # The fake sandbox does not run the extraction script, so pre-seed the raw
  # patch the in-container extraction would have written into the workspace.
  raw = "diff --git a/x b/x\n+hello\n"
  _ = (tmp_path / RAW_PATCH_NAME).write_text(raw)
  sb = _sandbox(tmp_path)
  obs = DiffExtractObserver()

  contribution = obs.before_destroy(sb)

  assert obs.patch == raw
  assert obs.is_empty is False
  assert obs.binary_stripped is False  # a pure-text patch
  assert contribution is not None
  # The raw diff came from the sandbox, so it is referenced by its in-sandbox
  # filename for the manager to fetch out.
  assert contribution.artifacts == {RAW_PATCH_NAME: RAW_PATCH_NAME}
  # The clean patch was derived here, so it travels inline — never written back
  # into the sandbox just to be fetched again.
  assert contribution.inline_artifacts[PATCH_NAME].decode() == raw
  assert not (tmp_path / PATCH_NAME).exists()  # no round trip through the box
  # the extraction script is staged (persisted for audit) and run
  extract = (tmp_path / EXTRACT_SCRIPT_NAME).read_text()
  assert 'cd "$SANDBOX_WORKSPACE"' in extract
  assert RAW_PATCH_NAME in extract  # git diff … > patch.raw.diff
  assert sb.scripts == [EXTRACT_SCRIPT_NAME]


def test_empty_patch(tmp_path: Path):
  _ = (tmp_path / RAW_PATCH_NAME).write_bytes(b"")
  obs = DiffExtractObserver()
  _ = obs.before_destroy(_sandbox(tmp_path))
  assert obs.is_empty is True
  assert obs.patch == ""


def test_absent_raw_patch_is_empty(tmp_path: Path):
  obs = DiffExtractObserver()  # no raw file written at all
  contribution = obs.before_destroy(_sandbox(tmp_path))
  assert obs.patch == ""
  assert obs.is_empty is True
  assert contribution is not None
  assert RAW_PATCH_NAME not in contribution.artifacts  # nothing produced
