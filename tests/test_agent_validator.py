"""Tests for the standalone annotation validator."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from swebench_related_files_annotation.annotate import agent_validator
from swebench_related_files_annotation.annotate.agent_validator import (
    CATEGORIES,
    count_addressable_lines,
    validate_output,
)
from swebench_related_files_annotation.annotate.schema import SnippetCategory


def _snippet(**overrides: object) -> dict[str, object]:
  base: dict[str, object] = {
      "file_path": "a.py",
      "start_line": 1,
      "end_line": 2,
      "category": "referenced-function",
      "description": "why",
  }
  base.update(overrides)
  return base


def _write_output(path: Path, snippets: list[dict[str, object]]) -> Path:
  path.write_text(json.dumps({"snippets": snippets}))
  return path


def test_categories_match_enum() -> None:
  assert {c.value for c in SnippetCategory} == CATEGORIES


def test_count_addressable_lines_trailing_newline(tmp_path: Path) -> None:
  # 3 content lines + trailing newline => Read tool shows 4 lines.
  f = tmp_path / "nl.txt"
  f.write_text("x\ny\nz\n")
  assert count_addressable_lines(f) == 4

  # No trailing newline => exactly the content-line count.
  g = tmp_path / "no_nl.txt"
  g.write_text("x\ny\nz")
  assert count_addressable_lines(g) == 3

  empty = tmp_path / "empty.txt"
  empty.write_text("")
  assert count_addressable_lines(empty) == 0


def test_trailing_newline_end_line_is_valid(tmp_path: Path) -> None:
  # Regression: a 2-line file ending in newline is addressable up to line 3
  # (the empty final line the Read tool shows). end_line 3 must be accepted.
  (tmp_path / "a.py").write_text("a\nb\n")
  out = _write_output(
      tmp_path / "out.json",
      [_snippet(start_line=1, end_line=3)],
  )
  assert validate_output(out, tmp_path) == []


def test_end_line_past_eof_is_flagged(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text("a\nb\n")  # 3 addressable lines
  out = _write_output(tmp_path / "o.json", [_snippet(end_line=4)])
  problems = validate_output(out, tmp_path)
  assert len(problems) == 1
  assert any("past the file's last line 3" in m for m in problems[0].messages)


def test_file_not_found_is_flagged(tmp_path: Path) -> None:
  out = _write_output(tmp_path / "o.json", [_snippet(file_path="nope.py")])
  problems = validate_output(out, tmp_path)
  assert any("file not found" in m for m in problems[0].messages)


def test_bad_category_and_range(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text("a\nb\nc\n")
  out = _write_output(
      tmp_path / "o.json",
      [_snippet(category="nonsense", start_line=3, end_line=2)],
  )
  problems = validate_output(out, tmp_path)
  msgs = problems[0].messages
  assert any("category" in m for m in msgs)
  assert any("before start_line" in m for m in msgs)


def test_missing_output_file(tmp_path: Path) -> None:
  problems = validate_output(tmp_path / "absent.json", tmp_path)
  assert len(problems) == 1
  assert problems[0].index == -1
  assert any("does not exist" in m for m in problems[0].messages)


def test_malformed_json(tmp_path: Path) -> None:
  out = tmp_path / "o.json"
  out.write_text("{ not json")
  problems = validate_output(out, tmp_path)
  assert any("not valid JSON" in m for m in problems[0].messages)


def test_runs_as_standalone_script(tmp_path: Path) -> None:
  # Proves the agent can run it with a plain python3, no package on the path.
  (tmp_path / "a.py").write_text("a\nb\n")
  out = _write_output(tmp_path / "out.json", [_snippet(end_line=2)])

  ok = subprocess.run(
      [sys.executable, agent_validator.__file__, str(out), str(tmp_path)],
      capture_output=True,
      text=True,
      check=False,
  )
  assert ok.returncode == 0
  assert "OK" in ok.stdout

  bad = _write_output(tmp_path / "bad.json", [_snippet(end_line=99)])
  failed = subprocess.run(
      [sys.executable, agent_validator.__file__, str(bad), str(tmp_path)],
      capture_output=True,
      text=True,
      check=False,
  )
  assert failed.returncode == 1
  assert "FAILED" in failed.stdout
