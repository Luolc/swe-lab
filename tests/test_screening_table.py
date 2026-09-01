"""The screening report's runnability table must agree with the screening data.

The table is hand-written prose and it shipped once listing 34 of 40 instances,
because two families were missing outright. The check that catches that lived
in `verdicts.py`, which needs the dataset and therefore only ran when a human
ran it — so a docs-only edit could break the table and still ship green. These
tests run the same check against the committed artifacts, and pin the three
ways the check has already been wrong.

The checker lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path.
"""

from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCREENING = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/instance_screening"
)


def _load_checker() -> ModuleType:
  """Import the table checker by path.

  Returns:
    The loaded `table_check` module.
  """
  spec = importlib.util.spec_from_file_location(
      "instance_screening_table_check", _SCREENING / "table_check.py"
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
  return _load_checker()


def _report(rows: str) -> str:
  """Wrap table rows in enough of a report for the checker to find them.

  Args:
    rows: the body rows of the runnability table, newline-separated.

  Returns:
    Report text containing exactly that table.
  """
  return (
      "prose before\n\n"
      "| family | instances in #261 | status |\n"
      "| --- | --- | --- |\n"
      f"{rows}\n\n"
      "prose after\n"
  )


# Three repos, so a single row can be dropped, duplicated or renamed.
_DATA = Counter({"a/one": 3, "b/two": 3, "c/three": 1})


@pytest.fixture()
def named(checker: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
  """Point the checker at a family->repo map matching `_DATA`.

  Args:
    checker: the loaded module.
    monkeypatch: pytest's patcher.

  Returns:
    The same module, with `FAMILY_REPO` replaced for the duration of a test.
  """
  monkeypatch.setattr(
      checker,
      "FAMILY_REPO",
      {"one": "a/one", "two": "b/two", "three": "c/three"},
  )
  return checker


def test_a_correct_table_passes(named: ModuleType) -> None:
  summary = named.check_runnability_table(
      _report(
          "| `one` (python) | 3 | proven |\n"
          "| `two` (go) | 3 | untested |\n"
          "| `three` (js) | 1 | un-runnable |"
      ),
      _DATA,
      7,
  )
  assert "7 of 7 instances" in summary
  assert "3 families each named once" in summary


def test_a_row_may_cover_several_families(named: ModuleType) -> None:
  """The real table groups families by language, so grouped counts must add."""
  summary = named.check_runnability_table(
      _report("| `one`, `two` | 6 | proven |\n| `three` | 1 | untested |"),
      _DATA,
      7,
  )
  assert "3 families each named once" in summary


def test_a_wrong_count_fails(named: ModuleType) -> None:
  with pytest.raises(AssertionError, match=r"says 4, data has 3"):
    named.check_runnability_table(
        _report(
            "| `one` | 4 | proven |\n"
            "| `two` | 3 | untested |\n"
            "| `three` | 1 | untested |"
        ),
        _DATA,
        7,
    )


def test_a_deleted_row_fails(named: ModuleType) -> None:
  """The original defect: a family silently absent from the table."""
  with pytest.raises(AssertionError, match=r"never names these repos.*b/two"):
    named.check_runnability_table(
        _report("| `one` | 3 | proven |\n| `three` | 1 | untested |"),
        _DATA,
        7,
    )


def test_a_duplicated_row_masking_a_missing_one_fails(
    named: ModuleType,
) -> None:
  """The permutation blind spot: `two` rewritten as a second `one`.

  Every per-row count is right and the rows still total 7, which is why summing
  them cannot catch this. Only exact-once coverage does.
  """
  with pytest.raises(AssertionError, match=r"more than once.*a/one"):
    named.check_runnability_table(
        _report(
            "| `one` | 3 | proven |\n"
            "| `one` | 3 | untested |\n"
            "| `three` | 1 | untested |"
        ),
        _DATA,
        7,
    )


def test_an_unknown_family_name_fails(named: ModuleType) -> None:
  """A renamed family must fail loudly, not quietly match nothing."""
  with pytest.raises(AssertionError, match=r"unknown family name.*onetwo"):
    named.check_runnability_table(
        _report(
            "| `onetwo` | 3 | proven |\n"
            "| `two` | 3 | untested |\n"
            "| `three` | 1 | untested |"
        ),
        _DATA,
        7,
    )


def test_the_shipped_report_agrees_with_the_shipped_verdicts(
    checker: ModuleType,
) -> None:
  """The point of all of the above: check the artifacts that actually shipped.

  `candidates.json` is committed, so this needs neither the dataset nor a
  container — which is what makes the check reachable from CI, where the
  docs-only edits that break the table are made.
  """
  rows = json.loads((_SCREENING / "candidates.json").read_text())
  checker.check_runnability_table(
      (_SCREENING / "REPORT.md").read_text(),
      Counter(row["repo"] for row in rows),
      len(rows),
  )
