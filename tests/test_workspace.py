"""Tests for per-instance workspace preparation."""

from __future__ import annotations

from pathlib import Path
from typing import final

from swe_lab.core.datasets.swebench_pro import (
    COLUMNS,
    SweBenchProInstance,
)
from swe_lab.core.repo.provider import RepoInstance
from swe_lab.tasks.related_files.workspace import (
    ANNOTATION_OUTPUT,
    CONTEXT_DIR,
    prepare_workspace,
)


@final
class _StubProvider:
  """Returns a fixed checkout dir instead of cloning."""

  def __init__(self, checkout: Path) -> None:
    self._checkout = checkout

  def provision(self, instance: RepoInstance, *, variant: str = "") -> Path:
    _ = (instance, variant)
    return self._checkout


def _instance() -> SweBenchProInstance:
  raw = dict.fromkeys(COLUMNS, "")
  raw.update(
      repo="acme/widget",
      instance_id="inst-1",
      base_commit="0" * 40,
      problem_statement="the problem",
      requirements="the requirements",
      interface="the interface",
      patch="gold patch body",
      test_patch="test patch body",
      fail_to_pass="[]",
      pass_to_pass="[]",
      issue_specificity="[]",
      issue_categories="[]",
      selected_test_files_to_run="[]",
  )
  return SweBenchProInstance.from_raw(raw)


def test_prepare_workspace_writes_context(tmp_path: Path) -> None:
  checkout = tmp_path / "checkout"
  checkout.mkdir()
  provider = _StubProvider(checkout)

  ws = prepare_workspace(_instance(), provider)

  assert ws.checkout == checkout
  context = checkout / CONTEXT_DIR
  assert (
      (context / "problem_statement.md").read_text().startswith("the problem")
  )
  assert (context / "gold_patch.diff").read_text().startswith("gold patch")
  assert (context / "test_patch.diff").is_file()
  assert (context / "git_log.txt").is_file()


def test_prepare_workspace_clears_stale_output(tmp_path: Path) -> None:
  checkout = tmp_path / "checkout"
  checkout.mkdir()
  stale = checkout / ANNOTATION_OUTPUT
  _ = stale.write_text("old")

  ws = prepare_workspace(_instance(), _StubProvider(checkout))

  assert not ws.output_path.exists()
