"""The one hard constraint: a synthetic assistant record must never be trained on.

The owner relaxed criterion (b) on 2026-09-03 — a segmented trace need not match
the shape an interactive user produces, because this is SFT data generation and
post-processing is available. **What was not relaxed** is that the model must
not take loss on tokens it never generated, and the resume seam inserts exactly
such a record: an `assistant` message reading "No response requested." that no
model wrote (REPORT.md §6.1).

So this filter is now load-bearing rather than a convenience, and it is written
as a **positive chain** rather than as a list of things to exclude. Exclusion
lists only ever cover the cases their author thought of, and the case they miss
looks exactly as green as the ones they catch.

A record is kept only if **all** of these hold:

1. it is an `assistant` record at all;
2. its `message.model` is present, a string, and **not** the synthetic marker;
3. it carries `requestId` — the field a record produced by a real API response
   has and a fabricated one does not.

Anything failing the chain is dropped. That ordering matters: the filter never
asks "does this look synthetic?", it asks "can I show this came from a model?"

**This lives in the experiment, not in the product path.** Phase 1 must move it
into the trace-synthesis code, and the two-armed test in
`tests/test_resume_loop_evidence.py` must move with it — a filter whose test
stayed behind is a filter nobody will notice breaking.
"""

from __future__ import annotations

SYNTHETIC_MODEL_MARKER = "<synthetic>"


def is_model_authored(record: dict[str, object]) -> bool:
  """Reports whether an assistant record can be shown to come from a model.

  Args:
    record: one session-transcript record.

  Returns:
    True when the positive chain holds; False for anything else, including
    records that are not assistant records at all.
  """
  if record.get("type") != "assistant":
    return False
  message = record.get("message")
  if not isinstance(message, dict):
    return False
  model = message.get("model")
  if not isinstance(model, str) or not model:
    return False
  if model == SYNTHETIC_MODEL_MARKER:
    return False
  return isinstance(record.get("requestId"), str)


def strip_synthetic_assistants(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
  """Drops assistant records that cannot be shown to be model-authored.

  Non-assistant records pass through untouched: this filter's job is the
  synthetic assistant turn, and silently reshaping the rest of the transcript
  would make its effect hard to audit.

  Args:
    records: the transcript, in order.

  Returns:
    The transcript with unauthored assistant records removed.
  """
  return [
      record
      for record in records
      if record.get("type") != "assistant" or is_model_authored(record)
  ]
