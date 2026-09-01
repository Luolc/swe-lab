"""The guidebook schema: the shape phase B's artifact must have to be usable.

A guidebook is Markdown — a tutorial for a blind actor, not a data record — so
the schema is deliberately light: it is a sequence of stages, and every stage
carries the fields the spec's Phase B table names. The one that matters is
``Justification``: a stage without a derivable reason leaves the Supervisor
nothing honest to say, so its absence is the failure this check exists to
catch. Whether the reason is *genuinely* derivable is a reader's judgement;
only presence is mechanical.
"""

from __future__ import annotations

import re

GUIDEBOOK_NAME = "guidebook.md"
"""The store name of the Oracle's output: the guidebook, as Markdown."""

# The per-stage fields, in the order a stage states them. Each appears as a
# bold label (``**Goal.**``) at the start of its paragraph — the shape of the
# hand-written guidebooks the schema was distilled from.
STAGE_FIELDS: tuple[str, ...] = (
    "Goal",
    "Actions",
    "Expected observations",
    "Justification",
    "Exit criteria",
)

_STAGE_HEADING = re.compile(r"^## Stage (\d+)\b.*$", re.MULTILINE)


def _field_pattern(name: str) -> re.Pattern[str]:
  """Match one bold field label, with either terminal punctuation."""
  return re.compile(rf"\*\*{re.escape(name)}[.:]\*\*")


_FIELD_PATTERNS = {name: _field_pattern(name) for name in STAGE_FIELDS}


def validate_guidebook(text: str) -> list[str]:
  """Check a guidebook's structure; return every problem found.

  Args:
    text: The guidebook Markdown.

  Returns:
    Human-readable problems, one per missing piece, in document order. Empty
    means the guidebook has at least one stage and every stage carries every
    field in :data:`STAGE_FIELDS`.
  """
  headings = list(_STAGE_HEADING.finditer(text))
  if not headings:
    return ["no stages: no '## Stage N' heading found"]
  problems: list[str] = []
  for index, heading in enumerate(headings):
    end = headings[index + 1].start() if index + 1 < len(headings) else None
    body = text[heading.end() : end]
    number = heading.group(1)
    for name, pattern in _FIELD_PATTERNS.items():
      if pattern.search(body) is None:
        problems.append(f"stage {number}: missing the '{name}' field")
  return problems
