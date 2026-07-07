"""Tests for the annotation schema: parsing, serialization, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swebench_related_files_annotation.annotate.schema import (
    Annotation,
    parse_agent_output,
    Snippet,
    SnippetCategory,
    validate_snippet,
)


def _snippet(**overrides: object) -> dict[str, object]:
  base: dict[str, object] = {
      "file_path": "src/a.py",
      "start_line": 1,
      "end_line": 10,
      "category": "referenced-function",
      "description": "why",
  }
  base.update(overrides)
  return base


def test_snippet_from_dict_coerces_types() -> None:
  snip = Snippet.from_dict(_snippet(start_line="3", end_line=7.0))
  assert snip.start_line == 3
  assert snip.end_line == 7
  assert snip.category is SnippetCategory.REFERENCED_FUNCTION


def test_snippet_from_dict_rejects_bool_line() -> None:
  with pytest.raises(ValueError, match="bool"):
    Snippet.from_dict(_snippet(start_line=True))


def test_snippet_from_dict_missing_keys() -> None:
  raw = _snippet()
  del raw["end_line"]
  with pytest.raises(ValueError, match="missing keys"):
    Snippet.from_dict(raw)


def test_parse_agent_output_object_and_list_forms() -> None:
  snippet_json = json.dumps(_snippet())
  as_object = parse_agent_output(f'{{"snippets": [{snippet_json}]}}')
  as_list = parse_agent_output(f"[{snippet_json}]")
  assert len(as_object) == len(as_list) == 1
  assert as_object[0] == as_list[0]


def test_annotation_json_round_trip() -> None:
  ann = Annotation(
      "inst-1",
      (Snippet.from_dict(_snippet()),),
      {"model": "sonnet"},
  )
  restored = Annotation.from_dict(json.loads(ann.to_json()))
  assert restored.instance_id == "inst-1"
  assert restored.snippets == ann.snippets
  assert restored.metadata == {"model": "sonnet"}


def test_validate_snippet_ok(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text("x\ny\nz\n")
  snip = Snippet.from_dict(_snippet(file_path="a.py", start_line=1, end_line=3))
  assert validate_snippet(snip, tmp_path) == []


def test_validate_snippet_file_missing(tmp_path: Path) -> None:
  snip = Snippet.from_dict(_snippet(file_path="nope.py"))
  problems = validate_snippet(snip, tmp_path)
  assert any("file not found" in p for p in problems)


def test_validate_snippet_range_past_eof(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text("x\ny\n")  # 2 lines
  snip = Snippet.from_dict(_snippet(file_path="a.py", start_line=1, end_line=5))
  problems = validate_snippet(snip, tmp_path)
  assert any("exceeds file length 2" in p for p in problems)


def test_validate_snippet_end_before_start(tmp_path: Path) -> None:
  (tmp_path / "a.py").write_text("x\ny\nz\n")
  snip = Snippet.from_dict(_snippet(file_path="a.py", start_line=3, end_line=2))
  problems = validate_snippet(snip, tmp_path)
  assert any("< start_line" in p for p in problems)
