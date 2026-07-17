"""Rollout: entryscript generation, prompt composition, patch read/empty guard.

These cover the logic that runs without Docker; the real container round-trip is
exercised on GitHub Actions (see .github/workflows/rollout.yml).
"""

from __future__ import annotations

from pathlib import Path

from swe_lab.rollout.constants import (
    CLAUDE_BIN_AT,
    MOUNT_AT,
    PATCH_NAME,
    PROMPT_NAME,
    RAW_PATCH_NAME,
    TRAJECTORY_NAME,
)
from swe_lab.rollout.entryscript import build_rollout_script
from swe_lab.rollout.prompt import build_solve_prompt
from swe_lab.rollout.runner import _read_patch, RolloutResult


def test_entryscript_runs_agent_then_extracts_against_base_commit() -> None:
  script = build_rollout_script(
      workdir="/app", base_commit="abc123", model="sonnet"
  )
  # agent invocation: mounted binary, prompt in, trajectory + stderr out
  assert CLAUDE_BIN_AT in script
  assert f'-p "$(cat {MOUNT_AT}/{PROMPT_NAME})"' in script
  assert "--model sonnet" in script
  assert f"> {MOUNT_AT}/{TRAJECTORY_NAME}" in script
  # a nonzero agent exit must not abort the script (we still extract edits)
  assert "|| true" in script
  # extraction: intent-to-add + text diff vs base_commit (no --binary/--cached),
  # raw bytes to the raw patch file (the runner strips + writes the clean patch)
  assert "add -N -- :/" in script
  assert "--binary" not in script
  assert "--cached" not in script
  assert script.rstrip().endswith(f"{MOUNT_AT}/{RAW_PATCH_NAME}")
  assert "abc123" in script


def test_entryscript_sets_writable_home_and_sandbox_flag() -> None:
  script = build_rollout_script(
      workdir="/app", base_commit="abc123", model="sonnet"
  )
  assert "export HOME=/tmp/rollout-home" in script
  assert "mkdir -p /tmp/rollout-home" in script
  # root sandbox bypass so --dangerously-skip-permissions works in-container
  assert "export IS_SANDBOX=1" in script
  assert "cd /app" in script


def test_entryscript_forwards_exclude_globs() -> None:
  script = build_rollout_script(
      workdir="/app",
      base_commit="abc123",
      model="sonnet",
      exclude_globs=("*.toml",),
  )
  assert ":(exclude)*.toml" in script


def test_solve_prompt_includes_problem_and_optional_sections() -> None:
  full = build_solve_prompt(
      "The widget crashes on empty input.",
      requirements="Must not raise on None.",
      interface="def render(widget) -> str",
  )
  assert "The widget crashes on empty input." in full
  assert "Must not raise on None." in full
  assert "def render(widget) -> str" in full
  assert "do not edit tests" in full

  minimal = build_solve_prompt("Just the statement.")
  assert "Just the statement." in minimal
  # optional section headers omitted when their content is empty
  assert "Requirements the fix must satisfy" not in minimal
  assert "Interface / API" not in minimal


def test_read_patch_missing_file_is_empty(tmp_path: Path) -> None:
  assert _read_patch(tmp_path / "nope.diff") == ""


def test_read_patch_decodes_raw_bytes_tolerantly(tmp_path: Path) -> None:
  patch_path = tmp_path / PATCH_NAME
  # a valid-looking diff header plus a stray non-UTF-8 byte must not crash
  _ = patch_path.write_bytes(b"diff --git a/x b/x\n+\xff\n")
  text = _read_patch(patch_path)
  assert text.startswith("diff --git a/x b/x")


def test_rollout_result_shape() -> None:
  result = RolloutResult(
      instance_id="x__y-1",
      patch="",
      is_empty=True,
      binary_stripped=False,
      complete=False,
      exchange={},
      exit_code=0,
      timed_out=False,
      workspace=Path("/tmp/ws"),
  )
  assert result.is_empty is True
  assert result.binary_stripped is False
  assert result.instance_id == "x__y-1"
