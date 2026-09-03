"""What the transcript *says about itself* — a cross-check, never a proof.

**This does not establish provenance, and no rename will make it.** Every field
it reads is the candidate record's own self-report: a fabricated record carrying
``model: "claude-sonnet-5"`` and any non-empty ``requestId`` passes the chain
below. Provenance cannot be supplied by the object being authenticated.
Authorship is established elsewhere, by reconciling a retained assistant turn
against the **captured API response** that produced it — an independent source —
per the provenance gate in
``docs/trace-synthesis/plans/task-22-segmented-supervision-loop.md`` §6.

What this *is* good for is one cheap leg of a join. The segmented loop knows how
many seams it cut, because it cut them; the CLI's own session persistence
independently marks the records it fabricated at those seams. Agreement rules
out the failure family this repo keeps meeting — our own wiring narrating its
own success — because faking it would take the driver's count and the CLI's
session writer being wrong in step. A mismatch is a finding: either the driver's
record of its own seams is wrong, or the pinned build's seam behaviour is not
the one the feasibility report measured.

**The domain is the session transcript and nothing else.** These fields exist
only in the records the CLI writes under ``$CLAUDE_CONFIG_DIR/projects/``, which
:class:`~swe_lab.harnesses.claude_code.native_transcript.NativeTranscriptObserver`
archives. They do not exist downstream: measured on the first end-to-end run's
capture, **0 of 59 assistant events in the ``stream-json`` stream carry
``requestId``** while all 59 report a real model name, and by the time
``convert.py`` has produced a canonical
:class:`~swe_lab.conversation.Message` only ``role`` and ``content`` survive.
Applied to either, this chain would answer about every record identically —
which is why it is a cross-check on a count and never a filter on a corpus.

The chain is kept **positive** rather than keyed on the ``<synthetic>`` literal:
an exclusion list covers only the cases its author thought of, and that marker
is promised by no interface, so a build that renamed it would silently stop
marking the fabricated turn while every existing check stayed green.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: What the harness's resume repair puts in ``message.model`` on the record it
#: fabricates. Named so a reader can find it, and deliberately **not** the thing
#: the chain below tests for: see the module note on exclusion lists.
SYNTHETIC_MODEL_MARKER = "<synthetic>"

#: The record type this module speaks about. Every other type is not its
#: subject and is never reported.
ASSISTANT_RECORD_TYPE = "assistant"


def is_marked_model_authored(record: Mapping[str, Any]) -> bool:
  """Report whether a transcript record's own fields claim a model wrote it.

  The positive chain, in order — every link must hold:

  1. it is an ``assistant`` record at all;
  2. its ``message.model`` is present and a non-empty string;
  3. that model is not :data:`SYNTHETIC_MODEL_MARKER`;
  4. it carries ``requestId``, which the CLI writes on a record built from a
     real API response and omits on one it fabricated.

  Every link is the record's self-report. See the module note: this is a
  cross-check, not authentication.

  Args:
    record: One session-transcript record.

  Returns:
    Whether every link holds. ``False`` for anything that is not an assistant
    record, so this is a question about assistant records rather than a
    disposition for the whole transcript.
  """
  if record.get("type") != ASSISTANT_RECORD_TYPE:
    return False
  message = record.get("message")
  if not isinstance(message, Mapping):
    return False
  model = message.get("model")
  if not isinstance(model, str) or not model:
    return False
  if model == SYNTHETIC_MODEL_MARKER:
    return False
  return isinstance(record.get("requestId"), str)


def records_marked_synthetic(
    records: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
  """Return the assistant records the transcript does **not** claim as real.

  Returns the excluded records rather than the kept ones because the use is a
  count to reconcile against the driver's seam log, not a corpus to hand
  onward: the thing a reader wants is *which* records the CLI marked and how
  many.

  Args:
    records: The transcript, in order.

  Returns:
    The assistant records failing :func:`is_marked_model_authored`, in order.
    Records of any other type are never included — they are not this module's
    subject.
  """
  return [
      record
      for record in records
      if record.get("type") == ASSISTANT_RECORD_TYPE
      and not is_marked_model_authored(record)
  ]
