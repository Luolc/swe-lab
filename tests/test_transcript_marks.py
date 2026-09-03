"""One leg of a join: what the CLI's transcript marks, not who wrote what.

**Not the provenance gate** — that reconciles retained assistant turns against
captured API responses (task 22 §6). These fields are the candidate record's own
self-report, and the tests say so: ``test_a_forged_record_passes_the_chain``
asserts the limitation rather than hiding it, so nobody reads a green suite here
as authentication.

Both arms are asserted, because **a check that reports everything as synthetic
passes the positive arm exactly as well as a correct one** — and, since this leg
is reconciled against the driver's seam count, one that over-reports agrees with
the driver by accident. The control arm was verified to discriminate rather than
assumed to: replacing ``records_marked_synthetic``'s condition with a constant
``True`` fails ``test_a_real_assistant_record_is_not_reported`` and
``test_records_of_other_types_are_never_reported`` while the positive arms stay
green.

The records are built **from the committed shape fixture** rather than typed out
here, so a transcript whose shape drifts away from the one this was written for
fails these tests instead of quietly changing what is counted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swe_lab.trace_synthesis.transcript_marks import (
    is_marked_model_authored,
    records_marked_synthetic,
    SYNTHETIC_MODEL_MARKER,
)

_SHAPES = Path(__file__).resolve().parent / "data/assistant_record_shapes.json"


@pytest.fixture(scope="module")
def shapes() -> dict[str, Any]:
  """Load the committed record-shape fixture.

  Returns:
    The fixture document.
  """
  return json.loads(_SHAPES.read_text(encoding="utf-8"))


def _record(shape: dict[str, Any]) -> dict[str, Any]:
  """Build one transcript record with the key set the fixture recorded.

  Every key the real capture carried is present, filled with a placeholder, so
  the record differs from the captured one in its *values* and not in its
  shape — which is what makes these tests fail when the shape moves.

  Args:
    shape: One entry of ``real_assistant_examples`` or
      ``synthetic_assistant_examples``.

  Returns:
    A record the chain can be asked about.
  """
  message: dict[str, Any] = dict.fromkeys(shape["message_keys"])
  message["role"] = "assistant"
  message["model"] = shape["model"]
  message["content"] = [{"type": kind} for kind in shape["content_block_types"]]
  record: dict[str, Any] = dict.fromkeys(shape["record_keys"], "placeholder")
  record["type"] = "assistant"
  record["message"] = message
  return record


def test_the_record_the_cli_fabricated_is_reported(shapes: dict[str, Any]):
  """The seam's synthetic record is what this leg is counting."""
  synthetic = _record(shapes["synthetic_assistant_examples"][0])
  assert not is_marked_model_authored(synthetic)
  assert records_marked_synthetic([synthetic]) == [synthetic]


def test_a_real_assistant_record_is_not_reported(shapes: dict[str, Any]):
  """The control arm: a leg that reported everything would fail here.

  It matters more than usual: this count is reconciled against the driver's
  seam count, and one that over-reports could agree with the driver by
  accident.
  """
  for shape in shapes["real_assistant_examples"]:
    real = _record(shape)
    assert is_marked_model_authored(real)
    assert records_marked_synthetic([real]) == []


def test_records_of_other_types_are_never_reported(shapes: dict[str, Any]):
  """A user turn or a summary is not this module's subject."""
  user = {"type": "user", "message": {"role": "user", "content": "hi"}}
  summary = {"type": "summary", "summary": "..."}
  synthetic = _record(shapes["synthetic_assistant_examples"][0])

  assert records_marked_synthetic([user, synthetic, summary]) == [synthetic]


def test_the_chain_is_positive_not_an_exclusion_list(shapes: dict[str, Any]):
  """A build that renamed the marker must not stop reporting the record.

  The marker is promised by no interface, so a check keyed on it is one rename
  away from green-and-wrong. The chain asks instead whether the transcript
  claims a real API response produced the record, which the renamed one still
  does not.
  """
  renamed = _record(shapes["synthetic_assistant_examples"][0])
  renamed["message"]["model"] = "some-future-placeholder"
  assert renamed["message"]["model"] != SYNTHETIC_MODEL_MARKER

  assert not is_marked_model_authored(renamed)


def test_a_forged_record_passes_the_chain(shapes: dict[str, Any]):
  """The limitation, asserted so a green suite cannot be read as authentication.

  Every field the chain reads is the record's own self-report, so a record that
  simply *claims* a model and a request id is indistinguishable from one that
  had them. This is why the provenance gate reconciles against captured API
  responses instead, and why this module is only one leg of a join.
  """
  forged = _record(shapes["synthetic_assistant_examples"][0])
  forged["message"]["model"] = "claude-sonnet-5"
  forged["requestId"] = "req_forged"

  assert is_marked_model_authored(forged)


def test_the_committed_fixture_matches_the_records_the_chain_reads(
    shapes: dict[str, Any],
):
  """The chain's premises are the fixture's, so a shape drift is loud."""
  for shape in shapes["real_assistant_examples"]:
    assert shape["has_requestId"]
    assert shape["model"] and shape["model"] != SYNTHETIC_MODEL_MARKER

  synthetic = shapes["synthetic_assistant_examples"][0]
  assert not synthetic["has_requestId"]
  assert synthetic["model"] == SYNTHETIC_MODEL_MARKER


def test_the_declared_domain_is_the_one_the_chain_can_speak_about(
    shapes: dict[str, Any],
):
  """The event stream is not this module's input, and the fixture says why.

  Applied there the chain would report every assistant record; the measurement
  is committed beside the shapes so the domain claim is auditable rather than
  only asserted in a docstring.
  """
  measured = shapes["domain"]["measured"]
  assert measured["assistant_events"] > 0
  assert measured["assistant_events_with_requestId"] == 0
