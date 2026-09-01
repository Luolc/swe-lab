"""The guidebook schema: stages, each carrying every required field."""

from __future__ import annotations

from swe_lab.trace_synthesis.guidebook import (
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
