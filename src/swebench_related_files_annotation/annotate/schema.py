"""The annotation record: one instance's list of relevant code snippets.

This is the ground-truth deliverable (see PLAN.md). The annotation agent writes
a JSON file of snippets; this module parses that into typed objects, wraps it
with run metadata into an :class:`Annotation`, and validates each snippet
against the checked-out repo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json
from pathlib import Path


class SnippetCategory(StrEnum):
  """Coarse, filterable label for why a snippet is relevant."""

  REFERENCED_FUNCTION = "referenced-function"
  CONTEXT_FILE = "context-file"
  USEFUL_UNIT_TEST = "useful-unit-test"
  INTERFACE_CONTRACT = "interface-contract"
  SIMILAR_PATTERN = "similar-pattern"


@dataclass(frozen=True, slots=True)
class Snippet:
  """One contiguous, inclusive line range in one file, with why it matters."""

  file_path: str
  start_line: int
  end_line: int
  category: SnippetCategory
  description: str

  @classmethod
  def from_dict(cls, raw: Mapping[str, object]) -> Snippet:
    missing = [
        k
        for k in ("file_path", "start_line", "end_line", "category")
        if k not in raw
    ]
    if missing:
      raise ValueError(f"Snippet is missing keys: {missing}")
    return cls(
        file_path=str(raw["file_path"]),
        start_line=_as_int(raw["start_line"]),
        end_line=_as_int(raw["end_line"]),
        category=SnippetCategory(str(raw["category"])),
        description=str(raw.get("description", "")),
    )

  def to_dict(self) -> dict[str, object]:
    data = asdict(self)
    data["category"] = self.category.value
    return data


@dataclass(frozen=True, slots=True)
class Annotation:
  """All snippets for one instance, plus how the annotation was produced."""

  instance_id: str
  snippets: tuple[Snippet, ...]
  metadata: dict[str, object] = field(default_factory=dict)

  def to_dict(self) -> dict[str, object]:
    return {
        "instance_id": self.instance_id,
        "snippets": [s.to_dict() for s in self.snippets],
        "metadata": self.metadata,
    }

  def to_json(self) -> str:
    return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

  @classmethod
  def from_dict(cls, raw: Mapping[str, object]) -> Annotation:
    snippets_raw = raw.get("snippets", [])
    if not isinstance(snippets_raw, Sequence):
      raise ValueError("'snippets' must be a list.")
    snippets = tuple(
        Snippet.from_dict(s) for s in snippets_raw if isinstance(s, Mapping)
    )
    metadata_raw = raw.get("metadata", {})
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    return cls(
        instance_id=str(raw.get("instance_id", "")),
        snippets=snippets,
        metadata=metadata,
    )


def parse_agent_output(text: str) -> tuple[Snippet, ...]:
  """Parse the JSON the agent wrote into snippets.

  Accepts either a bare list of snippets or an object with a ``snippets`` key,
  so the agent has a little leeway in shape.
  """
  data = json.loads(text)
  if isinstance(data, Mapping):
    items = data.get("snippets", [])
  elif isinstance(data, Sequence):
    items = data
  else:
    raise ValueError("Agent output must be a JSON list or object.")
  if not isinstance(items, Sequence):
    raise ValueError("'snippets' must be a list.")
  return tuple(Snippet.from_dict(s) for s in items if isinstance(s, Mapping))


def validate_snippet(snippet: Snippet, repo_root: Path) -> list[str]:
  """Return a list of human-readable problems with ``snippet`` (empty if OK)."""
  problems: list[str] = []
  path = repo_root / snippet.file_path
  if not path.is_file():
    problems.append(f"file not found: {snippet.file_path}")
    return problems

  if snippet.start_line < 1:
    problems.append(f"start_line {snippet.start_line} < 1")
  if snippet.end_line < snippet.start_line:
    problems.append(
        f"end_line {snippet.end_line} < start_line {snippet.start_line}"
    )

  line_count = _count_lines(path)
  if snippet.end_line > line_count:
    problems.append(
        f"end_line {snippet.end_line} exceeds file length {line_count}"
    )
  return problems


def _count_lines(path: Path) -> int:
  with path.open("rb") as handle:
    return sum(1 for _ in handle)


def _as_int(value: object) -> int:
  """Coerce a JSON scalar to int, rejecting bools and non-numeric values."""
  if isinstance(value, bool):
    raise ValueError(f"expected an integer, got bool {value!r}")
  if isinstance(value, int):
    return value
  if isinstance(value, (str, float)):
    return int(value)
  raise ValueError(f"expected an integer, got {type(value).__name__}")
