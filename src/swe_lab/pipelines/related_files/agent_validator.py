r"""Validate an annotation output file against the checked-out repo.

Dual-purpose and deliberately dependency-free (standard library only):

- The annotation agent runs it as a **standalone script** inside its working
  directory (``python3 .annotation_context/validate_annotation.py``) to check
  and self-correct its output until every snippet is valid, before finishing.
- The runner imports :func:`validate_output` for the final RunResult check, so
  there is a single source of truth for the rules.

Line numbering matches Claude Code's Read tool: a file is addressed as
``split("\n")`` lines, i.e. a trailing newline yields one extra (empty) final
line. So a 55-line file that ends in a newline has 56 addressable lines, and an
``end_line`` of 56 is valid — this is the trailing-newline convention the Read
tool shows the agent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

DEFAULT_OUTPUT = ".annotation_output.json"

# Keep in sync with schema.SnippetCategory (a unit test enforces this).
# Hardcoded so the script stays standalone, with no import of our package.
CATEGORIES: frozenset[str] = frozenset(
    {
        "referenced-function",
        "context-file",
        "useful-unit-test",
        "interface-contract",
        "similar-pattern",
    }
)

_REQUIRED_KEYS = ("file_path", "start_line", "end_line", "category")


@dataclass
class SnippetProblem:
  """Problems found with one snippet (by position in the output array)."""

  index: int
  file_path: str
  messages: list[str] = field(default_factory=list)


def count_addressable_lines(path: Path) -> int:
  r"""Count lines as the Read tool addresses them (``split("\n")``)."""
  data = path.read_bytes()
  if not data:
    return 0
  return data.count(b"\n") + 1


def _to_int(value: object) -> int:
  if isinstance(value, bool):
    raise ValueError("must be an integer, not a boolean")
  if isinstance(value, int):
    return value
  if isinstance(value, (str, float)):
    return int(value)
  raise ValueError(f"must be an integer, got {type(value).__name__}")


def validate_snippet_dict(
    raw: Mapping[str, object], repo_root: Path
) -> list[str]:
  """Return agent-readable problems with one snippet dict (empty if valid)."""
  problems: list[str] = []
  for key in _REQUIRED_KEYS:
    if key not in raw:
      problems.append(f"missing required key '{key}'")
  if problems:
    return problems

  category = str(raw["category"])
  if category not in CATEGORIES:
    problems.append(f"category '{category}' is not one of {sorted(CATEGORIES)}")

  try:
    start = _to_int(raw["start_line"])
    end = _to_int(raw["end_line"])
  except ValueError as exc:
    problems.append(f"start_line/end_line {exc}")
    return problems

  path = repo_root / str(raw["file_path"])
  if not path.is_file():
    problems.append(f"file not found: {raw['file_path']}")
    return problems

  last_line = count_addressable_lines(path)
  if start < 1:
    problems.append(f"start_line {start} must be >= 1")
  if end < start:
    problems.append(f"end_line {end} is before start_line {start}")
  if end > last_line:
    problems.append(f"end_line {end} is past the file's last line {last_line}")
  return problems


def validate_output(output_path: Path, repo_root: Path) -> list[SnippetProblem]:
  """Validate a whole output file; return one entry per problematic snippet.

  A missing file or malformed JSON is reported as a single problem at index -1.
  """
  if not output_path.is_file():
    return [
        SnippetProblem(-1, str(output_path), ["output file does not exist"])
    ]
  try:
    data = json.loads(output_path.read_text())
  except json.JSONDecodeError as exc:
    return [SnippetProblem(-1, str(output_path), [f"not valid JSON: {exc}"])]

  if isinstance(data, Mapping):
    snippets = data.get("snippets", [])
  elif isinstance(data, Sequence):
    snippets = data
  else:
    return [
        SnippetProblem(-1, str(output_path), ["must be a JSON object or list"])
    ]

  if not isinstance(snippets, Sequence):
    return [SnippetProblem(-1, str(output_path), ["'snippets' must be a list"])]

  problems: list[SnippetProblem] = []
  for i, snippet in enumerate(snippets):
    if not isinstance(snippet, Mapping):
      problems.append(SnippetProblem(i, "?", ["snippet must be an object"]))
      continue
    messages = validate_snippet_dict(snippet, repo_root)
    if messages:
      problems.append(
          SnippetProblem(i, str(snippet.get("file_path", "?")), messages)
      )
  return problems


def format_report(problems: list[SnippetProblem], snippet_count: int) -> str:
  """Format validation results as a human/agent-friendly report."""
  if not problems:
    return f"OK: all {snippet_count} snippet(s) are valid."
  lines = [f"FAILED: {len(problems)} snippet(s) have problems.", ""]
  for problem in problems:
    where = (
        problem.file_path
        if problem.index < 0
        else f"snippet[{problem.index}] ({problem.file_path})"
    )
    lines.append(f"- {where}:")
    lines.extend(f"    - {msg}" for msg in problem.messages)
  lines.append("")
  lines.append("Fix these and re-run this validator until it prints OK.")
  return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
  """Run the validator CLI; return 0 when every snippet is valid."""
  args = sys.argv[1:] if argv is None else argv
  output_path = Path(args[0]) if args else Path(DEFAULT_OUTPUT)
  repo_root = Path(args[1]) if len(args) > 1 else Path.cwd()

  problems = validate_output(output_path, repo_root)
  snippet_count = _snippet_count(output_path)
  print(format_report(problems, snippet_count))
  return 0 if not problems else 1


def _snippet_count(output_path: Path) -> int:
  try:
    data = json.loads(output_path.read_text())
  except (OSError, json.JSONDecodeError):
    return 0
  if isinstance(data, Mapping):
    snippets = data.get("snippets", [])
  elif isinstance(data, Sequence):
    snippets = data
  else:
    return 0
  return len(snippets) if isinstance(snippets, Sequence) else 0


if __name__ == "__main__":
  raise SystemExit(main())
