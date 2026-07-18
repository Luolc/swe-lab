"""The annotation record: one instance's list of relevant code snippets.

This is the ground-truth deliverable. The annotation agent writes
a JSON file of snippets; this module parses that into typed objects and wraps it
with run metadata into an :class:`Annotation`. Per-snippet validation against
the repo lives in :mod:`agent_validator` (standalone so the agent can run it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import json


class SnippetCategory(StrEnum):
  """Coarse, filterable label for why a snippet is relevant."""

  REFERENCED_FUNCTION = "referenced-function"
  CONTEXT_FILE = "context-file"
  USEFUL_UNIT_TEST = "useful-unit-test"
  INTERFACE_CONTRACT = "interface-contract"
  SIMILAR_PATTERN = "similar-pattern"


@dataclass(frozen=True, slots=True)
class Snippet:
  """One contiguous, inclusive line range in one file, with why it matters.

  Attributes:
    file_path: Path relative to the repo root.
    start_line: First line of the range (1-based, inclusive).
    end_line: Last line of the range (1-based, inclusive).
    category: Why the snippet is relevant (see :class:`SnippetCategory`).
    description: One or two sentences on the snippet's role in the task.
  """

  file_path: str
  start_line: int
  end_line: int
  category: SnippetCategory
  description: str

  @classmethod
  def from_dict(cls, raw: Mapping[str, object]) -> Snippet:
    """Build a snippet from a raw dict; raise ``ValueError`` if malformed."""
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
    """Return a JSON-ready dict with ``category`` as its string value."""
    data = asdict(self)
    data["category"] = self.category.value
    return data


@dataclass(frozen=True, slots=True)
class Annotation:
  """All snippets for one instance, plus how the annotation was produced.

  Attributes:
    instance_id: The annotated dataset instance.
    snippets: The ordered relevant-code snippets.
    metadata: How the annotation was produced (model, cost, turn count,
      validation summary, ...).
  """

  instance_id: str
  snippets: tuple[Snippet, ...]
  metadata: dict[str, object] = field(default_factory=dict)

  def to_dict(self) -> dict[str, object]:
    """Return the annotation as a JSON-ready dict."""
    return {
        "instance_id": self.instance_id,
        "snippets": [s.to_dict() for s in self.snippets],
        "metadata": self.metadata,
    }

  def to_json(self) -> str:
    """Serialize to indented JSON with a trailing newline."""
    return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

  @classmethod
  def from_dict(cls, raw: Mapping[str, object]) -> Annotation:
    """Build an annotation from a raw dict (the ``to_dict`` shape)."""
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


def _as_int(value: object) -> int:
  """Coerce a JSON scalar to int, rejecting bools and non-numeric values."""
  if isinstance(value, bool):
    raise ValueError(f"expected an integer, got bool {value!r}")
  if isinstance(value, int):
    return value
  if isinstance(value, (str, float)):
    return int(value)
  raise ValueError(f"expected an integer, got {type(value).__name__}")
