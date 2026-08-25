"""Tests for the shared DiffExtractObserver (no Docker; extraction faked)."""

from pathlib import Path

from etils import epath
import pytest

from swe_lab.sandbox import ExecResult, SandboxError, SandboxSpec
from swe_lab.sandbox.observers.diff_extract import (
    BASELINE_SCRIPT_NAME,
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


# ─── the opt-in pre-agent baseline (ADR-0001, 2026-08-25 amendment) ─────────


def test_the_default_base_is_still_the_instances_base_commit(tmp_path: Path):
  # The amendment's whole point: default off, current behavior byte-identical.
  _ = (tmp_path / RAW_PATCH_NAME).write_text("diff --git a/x b/x\n")
  sb = _sandbox(tmp_path)
  obs = DiffExtractObserver()

  obs.after_create(sb)  # a no-op — nothing staged, nothing run
  assert sb.scripts == []
  assert not (tmp_path / BASELINE_SCRIPT_NAME).exists()

  _ = obs.before_destroy(sb)
  assert obs.base_ref == "base"  # the spec's base_commit
  assert " base > " in (tmp_path / EXTRACT_SCRIPT_NAME).read_text()


def test_the_baseline_commits_the_tree_the_agent_found_and_diffs_that(
    tmp_path: Path,
):
  """For an image whose worktree ships already different from base_commit.

  The default would fold those build-time edits into every agent's patch, so
  an agent that changed nothing still produces a large one.
  """
  sb = _sandbox(tmp_path)
  # What the in-container script would leave behind.
  _ = (tmp_path / "patch.base.txt").write_text("cafe1234\n")
  _ = (tmp_path / RAW_PATCH_NAME).write_text("diff --git a/x b/x\n")
  obs = DiffExtractObserver(baseline=True)

  obs.after_create(sb)
  assert obs.base_ref == "cafe1234"

  script = (tmp_path / BASELINE_SCRIPT_NAME).read_text()
  # Everything present, so the base matches the tree the agent starts from —
  # a file left out would later look like the agent created it.
  assert "add -A -- :/" in script
  # A container often has no git identity, and `git commit` refuses without it.
  assert "user.email=" in script and "user.name=" in script
  # A clean worktree must still yield a baseline, or the base would depend on
  # whether the image happened to be dirty.
  assert "--allow-empty" in script
  # Pinned dates, so one instance's baseline sha is comparable across attempts.
  assert "GIT_AUTHOR_DATE=" in script and "GIT_COMMITTER_DATE=" in script

  _ = obs.before_destroy(sb)
  # The diff is taken against the baseline, not the spec's base_commit.
  assert obs.base_ref == "cafe1234"
  assert " cafe1234 > " in (tmp_path / EXTRACT_SCRIPT_NAME).read_text()


def test_a_baseline_that_cannot_be_made_aborts_rather_than_falling_back(
    tmp_path: Path,
):
  # Falling back to base_commit would silently produce exactly the
  # contaminated patch this mode exists to prevent, so it fails closed —
  # after_create's contract is that a raise aborts the run.
  sb = _sandbox(tmp_path)
  sb.run_results = [ExecResult(1, "", "fatal: not a git repository")]
  with pytest.raises(SandboxError, match="pre-agent baseline"):
    DiffExtractObserver(baseline=True).after_create(sb)


def test_a_baseline_that_produced_no_sha_aborts_too(tmp_path: Path):
  # The script exited 0 but wrote nothing readable back — still no base.
  sb = _sandbox(tmp_path)
  with pytest.raises(SandboxError, match="no sha"):
    DiffExtractObserver(baseline=True).after_create(sb)
