"""The guidebook schema: stages, each carrying every required field."""

from __future__ import annotations

from swe_lab.trace_synthesis import guidebook as guidebook_schema
from swe_lab.trace_synthesis.guidebook import STAGE_FIELDS, validate_guidebook


def _stage(number: int, *, without: str = "") -> str:
  """Render one stage in the hand-written guidebooks' shape."""
  fields = "\n\n".join(
      f"**{name}.** something about {name.lower()}."
      for name in STAGE_FIELDS
      if name != without
  )
  return f"## Stage {number} — a title\n\n{fields}\n\n---\n\n"


def _rubric(*, without: str = "") -> str:
  """Render one complete compact rubric, optionally missing one field."""
  fields = "\n\n".join(
      f"**{name}.** something about {name.lower()}."
      for name in guidebook_schema.RUBRIC_FIELDS
      if name != without
  )
  return f"## Supervisor rubric\n\n{fields}\n\n---\n\n"


def test_a_guidebook_in_the_handwritten_shape_is_valid():
  text = "# Guidebook — a title\n\n" + _rubric() + _stage(1) + _stage(2)
  assert validate_guidebook(text) == []


def test_a_colon_after_the_label_is_accepted_too():
  text = _rubric() + _stage(1).replace("**Goal.**", "**Goal:**")
  assert validate_guidebook(text) == []


def test_a_stage_missing_its_justification_is_named():
  # The load-bearing field: without a derivable reason the Supervisor has
  # nothing honest to say, so its absence is what the measurement exists to
  # surface — named by stage, so a reader knows where.
  text = _rubric() + _stage(1) + _stage(2, without="Justification") + _stage(3)
  assert validate_guidebook(text) == [
      "stage 2: missing the 'Justification' field"
  ]


def test_every_missing_field_is_named():
  text = _rubric() + _stage(1, without="Exit criteria").replace(
      "**Expected observations.**", "**Observations.**"
  )
  assert validate_guidebook(text) == [
      "stage 1: missing the 'Expected observations' field",
      "stage 1: missing the 'Exit criteria' field",
  ]


def test_a_document_with_no_stages_is_named_on_both_counts():
  assert validate_guidebook("# Guidebook\n\nJust prose.\n") == [
      "no stages: no '## Stage N' heading found",
      "missing the '## Supervisor rubric' section",
  ]


def test_a_legacy_tutorial_still_names_its_representation():
  """A pre-rubric artifact reads as legacy; the measurement still says so."""
  legacy = "# Guidebook — legacy\n\n" + _stage(1)

  # ADR-0021's read path is the mode, not a validity verdict — and since
  # ADR-0027 phase C reads without validating at all, so this text is usable
  # while the measurement records what it lacks.
  assert guidebook_schema.guidebook_context_mode(legacy) == "legacy_tutorial"
  assert guidebook_schema.extract_guidebook_rubric(legacy) is None
  assert validate_guidebook(legacy) == [
      "missing the '## Supervisor rubric' section"
  ]


def test_a_partial_rubric_is_named_field_by_field():
  """A malformed new section cannot disguise itself as a legacy absence."""
  text = (
      "# Guidebook — partial rubric\n\n"
      + _rubric(without="Safe hint justification")
      + _stage(1)
  )

  assert guidebook_schema.guidebook_context_mode(text) == "rubric"
  assert validate_guidebook(text) == [
      "supervisor rubric: missing the 'Safe hint justification' field"
  ]


def test_the_compact_rubric_is_extracted_without_the_tutorial():
  """The prompt representation is bounded without mutating the artifact."""
  rubric = _rubric()
  tutorial = _stage(1) + _stage(2)
  text = "# Guidebook — both\n\n" + rubric + tutorial

  extracted = guidebook_schema.extract_guidebook_rubric(text)

  assert extracted is not None
  assert "**Checkpoints.**" in extracted
  assert "## Stage 1" not in extracted
  assert tutorial in text
