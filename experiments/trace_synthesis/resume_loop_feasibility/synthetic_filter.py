"""A **prototype** for dropping the synthetic assistant record — not a gate.

The owner's one unrelaxed requirement is that the model must not take loss on
tokens it never generated, and the resume seam inserts exactly such a record: an
`assistant` message reading "No response requested." that no model wrote
(REPORT.md §6.1).

**This module does not enforce that requirement, and an earlier revision of the
report wrongly said it did.** Two limits, both measured:

1. **It operates on the wrong artifact.** Training uses `conversation.json`,
   produced by `ClaudeCodeHarness.to_conversation()` /
   `proxy_log_to_conversation()`, whose canonical `Message` carries **only**
   `role` and `content`. The provenance fields this module reads — `type`,
   `message.model`, `requestId` — **do not exist there**, so applying it to the
   canonical conversation returns the input unchanged. Measured on the real
   dirty-seam capture: 10 canonical messages, the synthetic assistant at index
   7, still present afterwards. The native transcript this module *can* read is
   collected by an advisory `required=False` observer that never fails a run.
2. **Its fields are self-asserted.** All three live in the same harness-written
   record, so a fabricated record carrying a plausible `model` and any non-empty
   `requestId` passes. Authorship is not established by asking the candidate
   record about itself.

What it is good for: dropping the record from a **native transcript**, on the
samples measured here. That is a useful prototype and it is not the invariant.

**What a real gate requires** (Phase 1 acceptance condition, not satisfied
here): filter at the trace → `conversation.json` boundary **before provenance is
discarded**; **fail the run** when provenance cannot be established rather than
passing the record through; and establish authorship against an **independent**
source — reconcile kept assistant turns against the captured API responses
instead of trusting fields on the candidate record. Its test must start from the
real dirty-seam fixture, produce the final canonical conversation, and prove
both that the synthetic assistant is absent and that real assistant responses
remain.
"""

from __future__ import annotations

SYNTHETIC_MODEL_MARKER = "<synthetic>"


def is_model_authored(record: dict[str, object]) -> bool:
  """Reports whether a *transcript* record carries model-authorship fields.

  The name is deliberately narrow: this checks that a record's own fields say it
  came from a model, which is weaker than establishing that it did. See the
  module docstring for what it does not do.

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
  request_id = record.get("requestId")
  return isinstance(request_id, str) and bool(request_id.strip())


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
