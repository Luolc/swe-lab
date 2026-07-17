"""Tests for the SWE-Bench Pro record type and its parsing rules."""

from __future__ import annotations

import pytest

from swe_lab.core.datasets.swebench_pro import (
    COLUMNS,
    SweBenchProInstance,
)


def _raw(**overrides: str) -> dict[str, str]:
  """A minimal valid raw row, with per-test overrides."""
  base = {
      "repo": "acme/widget",
      "instance_id": "instance_acme__widget-abc-vnan",
      "base_commit": "0" * 40,
      "patch": "diff --git a/x b/x\n",
      "test_patch": "diff --git a/t b/t\n",
      "problem_statement": '"**Title**\\n\\nBody"',
      "requirements": "plain requirements text",
      "interface": '"Type: Method"',
      "repo_language": "python",
      "fail_to_pass": "['a::t1', \"b::t2\"]",
      "pass_to_pass": '["c::t3"]',
      "issue_specificity": '["major_bug"]',
      "issue_categories": '["back_end_knowledge"]',
      "before_repo_set_cmd": "git reset --hard HEAD",
      "selected_test_files_to_run": '["test/a.py"]',
      "dockerhub_tag": "acme.widget-abc",
  }
  base.update(overrides)
  return base


def test_from_raw_parses_all_field_kinds() -> None:
  inst = SweBenchProInstance.from_raw(_raw())

  # Plain string columns pass through untouched.
  assert inst.repo == "acme/widget"
  assert inst.before_repo_set_cmd == "git reset --hard HEAD"
  assert inst.dockerhub_tag == "acme.widget-abc"

  # JSON-string-wrapped text columns are unwrapped.
  assert inst.problem_statement == "**Title**\n\nBody"
  assert inst.interface == "Type: Method"

  # Raw (non-wrapped) text columns are left as-is.
  assert inst.requirements == "plain requirements text"

  # List columns become tuples of str, including mixed-quote Python reprs.
  assert inst.fail_to_pass == ("a::t1", "b::t2")
  assert inst.pass_to_pass == ("c::t3",)
  assert inst.selected_test_files_to_run == ("test/a.py",)


def test_text_unwrap_leaves_raw_leading_quote_untouched() -> None:
  # A genuinely-raw statement that merely starts with a quote is not valid JSON
  # and must be preserved verbatim.
  inst = SweBenchProInstance.from_raw(_raw(problem_statement='"unterminated'))
  assert inst.problem_statement == '"unterminated'


def test_instance_is_frozen_and_hashable() -> None:
  inst = SweBenchProInstance.from_raw(_raw())
  with pytest.raises(AttributeError):
    inst.repo = "other"  # pyright: ignore[reportAttributeAccessIssue]
  assert hash(inst) == hash(inst)


def test_missing_columns_raise() -> None:
  row = _raw()
  del row["interface"]
  with pytest.raises(ValueError, match="missing expected columns"):
    SweBenchProInstance.from_raw(row)


def test_columns_constant_has_16_entries() -> None:
  assert len(COLUMNS) == 16
  assert len(set(COLUMNS)) == 16
