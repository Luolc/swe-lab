"""The guidebook schema: stages, each carrying every required field."""

from __future__ import annotations

import pytest

from swe_lab.trace_synthesis import guidebook as guidebook_schema
from swe_lab.trace_synthesis.guidebook import (
    require_valid_guidebook,
    STAGE_FIELDS,
    validate_guidebook,
)


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
  text = "# Guidebook — a title\n\nPreamble.\n\n" + _stage(1) + _stage(2)
  assert validate_guidebook(text) == []


def test_a_colon_after_the_label_is_accepted_too():
  text = _stage(1).replace("**Goal.**", "**Goal:**")
  assert validate_guidebook(text) == []


def test_a_stage_missing_its_justification_is_rejected():
  # The load-bearing field: without a derivable reason the Supervisor has
  # nothing honest to say, so its absence is the failure the schema exists
  # to catch — named by stage, so the author knows where.
  text = _stage(1) + _stage(2, without="Justification") + _stage(3)
  assert validate_guidebook(text) == [
      "stage 2: missing the 'Justification' field"
  ]


def test_every_missing_field_is_named():
  text = _stage(1, without="Exit criteria").replace(
      "**Expected observations.**", "**Observations.**"
  )
  assert validate_guidebook(text) == [
      "stage 1: missing the 'Expected observations' field",
      "stage 1: missing the 'Exit criteria' field",
  ]


def test_a_document_with_no_stages_is_rejected():
  assert validate_guidebook("# Guidebook\n\nJust prose.\n") == [
      "no stages: no '## Stage N' heading found"
  ]


def test_a_legacy_tutorial_remains_a_valid_phase_c_input():
  """A supported resume may carry a guidebook written before rubrics."""
  legacy = "# Guidebook — legacy\n\n" + _stage(1)

  assert validate_guidebook(legacy) == []
  require_valid_guidebook(legacy)
  assert guidebook_schema.extract_guidebook_rubric(legacy) is None


def test_new_phase_b_output_requires_the_compact_rubric():
  """Compatible reads must not let a new Oracle omit its new output."""
  tutorial_only = "# Guidebook — new output\n\n" + _stage(1)

  assert validate_guidebook(tutorial_only, require_rubric=True) == [
      "missing the '## Supervisor rubric' section"
  ]


def test_a_partial_rubric_is_invalid_even_on_the_legacy_read_path():
  """A malformed new section cannot disguise itself as a legacy absence."""
  text = (
      "# Guidebook — partial rubric\n\n"
      + _rubric(without="Safe hint justification")
      + _stage(1)
  )

  assert validate_guidebook(text) == [
      "supervisor rubric: missing the 'Safe hint justification' field"
  ]
  with pytest.raises(guidebook_schema.GuidebookRejectedError):
    require_valid_guidebook(text)


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
