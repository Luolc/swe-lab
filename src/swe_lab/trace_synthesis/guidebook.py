"""The guidebook schema shared by phase B writers and phase C readers.

A guidebook is Markdown with two representations: a detailed tutorial for a
blind actor and a compact rubric for the Supervisor. New phase-B output must
contain both. Phase C also accepts a tutorial-only artifact from a supported
legacy resume, while any rubric that is present must contain every field.

The schema is deliberately light. It checks the presence of the tutorial's
stage fields and the rubric's supervisor-facing fields; whether their content
is genuinely derivable from observable evidence remains a reader's judgement.
"""

from __future__ import annotations

import re

GUIDEBOOK_NAME = "guidebook.md"
"""The store name of the Oracle's output: the guidebook, as Markdown."""

GUIDEBOOK_CONTEXT_RUBRIC = "rubric"
GUIDEBOOK_CONTEXT_LEGACY = "legacy_tutorial"

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

RUBRIC_FIELDS: tuple[str, ...] = (
    "Checkpoints",
    "On-track evidence",
    "Disallowed branches",
    "Off-track signals",
    "Self-correction signals",
    "Safe hint justification",
)

_STAGE_HEADING = re.compile(r"^## Stage (\d+)\b.*$", re.MULTILINE)
_RUBRIC_HEADING = re.compile(r"^## Supervisor rubric\s*$", re.MULTILINE)
_LEVEL_TWO_HEADING = re.compile(r"^## .+$", re.MULTILINE)


def _field_pattern(name: str) -> re.Pattern[str]:
  """Match one bold field label, with either terminal punctuation."""
  return re.compile(rf"\*\*{re.escape(name)}[.:]\*\*")


_FIELD_PATTERNS = {name: _field_pattern(name) for name in STAGE_FIELDS}
_RUBRIC_FIELD_PATTERNS = {name: _field_pattern(name) for name in RUBRIC_FIELDS}


class GuidebookRejectedError(RuntimeError):
  """Raised when a guidebook-guided run lacks a usable guidebook."""


def extract_guidebook_rubric(text: str) -> str | None:
  """Return the compact rubric body without the detailed tutorial.

  Args:
    text: The complete guidebook Markdown.

  Returns:
    The stripped rubric body, or ``None`` for a legacy guidebook.
  """
  heading = _RUBRIC_HEADING.search(text)
  if heading is None:
    return None
  next_heading = _LEVEL_TWO_HEADING.search(text, heading.end())
  end = next_heading.start() if next_heading is not None else None
  return text[heading.end() : end].strip()


def guidebook_context_mode(text: str | None) -> str | None:
  """Name which guidebook representation the default prompt consumes.

  Args:
    text: The complete guidebook, or ``None`` for an unguided run.

  Returns:
    ``"rubric"``, ``"legacy_tutorial"``, or ``None``.
  """
  if text is None:
    return None
  if extract_guidebook_rubric(text) is not None:
    return GUIDEBOOK_CONTEXT_RUBRIC
  return GUIDEBOOK_CONTEXT_LEGACY


def validate_guidebook(text: str, *, require_rubric: bool = False) -> list[str]:
  """Check a guidebook's structure; return every problem found.

  Args:
    text: The guidebook Markdown.
    require_rubric: Whether absence of the compact rubric is an error. Phase B
      sets this for new output; phase C leaves it false for legacy resume.

  Returns:
    Human-readable problems, one per missing piece, in document order. Empty
    means the guidebook has at least one complete stage and, when required or
    present, one complete rubric.
  """
  headings = list(_STAGE_HEADING.finditer(text))
  problems: list[str] = []
  if not headings:
    problems.append("no stages: no '## Stage N' heading found")
  else:
    for index, heading in enumerate(headings):
      end = headings[index + 1].start() if index + 1 < len(headings) else None
      body = text[heading.end() : end]
      number = heading.group(1)
      for name, pattern in _FIELD_PATTERNS.items():
        if pattern.search(body) is None:
          problems.append(f"stage {number}: missing the '{name}' field")

  rubric = extract_guidebook_rubric(text)
  if rubric is None:
    if require_rubric:
      problems.append("missing the '## Supervisor rubric' section")
  else:
    for name, pattern in _RUBRIC_FIELD_PATTERNS.items():
      if pattern.search(rubric) is None:
        problems.append(f"supervisor rubric: missing the '{name}' field")
  return problems


def require_valid_guidebook(text: str) -> None:
  """Reject a missing or structurally invalid guidebook.

  Args:
    text: The phase-B artifact about to be handed to phase C.

  Raises:
    GuidebookRejectedError: The artifact is missing or fails its schema check.
  """
  problems = validate_guidebook(text)
  if problems:
    raise GuidebookRejectedError(
        "guidebook rejected before actor start: " + "; ".join(problems)
    )
