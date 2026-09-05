# ADR-0019: Complete bounded supervisor evidence

## Status

Accepted. Issue #430 is the owner's proposal for resolving issue #380; the
implementation and trace-synthesis spec reconciliation land with this record.

## Date

2026-09-04

## Context

The evidence filter admits assistant messages and the results of tools the
actor called. The Python prompt renderer retained only `TextBlock`, however,
so every admitted tool result and every tool-only assistant message rendered
empty. On the first recorded corpus, only 11 of 90 admitted records showed the
judge non-empty actor content (issue #380).

`SpeakWhenOffTrack.window` also sliced admitted `Message` records directly. A
window boundary could retain a tool result without the assistant call it
answers, or retain the call and discard its result. Increasing the window would
reduce but not remove that structural failure, while sending the full history
would make model input unbounded.

Tool inputs and outputs are actor-visible evidence. They can contain repository
content, command output, and errors discovered through the actor's own actions;
they are not a new channel for hidden tests or reference patches. Reasoning
blocks are different: they are not an observable action, can be redacted
upstream, and are not needed to establish what tool ran or what it returned.

## Decision

The default Python supervision path selects complete recent assistant turns.
One turn consists of an assistant message and the following tool-result records
before the next assistant message. The window counts turns rather than raw
messages. A genuinely unanswered call remains in the selected turn and is
rendered with an explicit missing-result marker.

The prompt renderer pairs calls and results by tool-use ID. It keeps the tool
name, ID, deterministically serialized input, success/error status, and result
content. It omits reasoning. Visible assistant text is independently
configurable and bounded.

Only the prompt representation is clipped. The complete typed conversation and
supervisor evidence remain unchanged for persistence and audit. Every clipped
value carries an explicit marker naming how many characters were not shown, so
a reader can distinguish clipped evidence from evidence that was originally
short.

Selection, rendering, and prompt assembly are public replaceable ABCs with
default implementations. The standard policy composes those defaults through
dependency injection; replacing one component does not require a replacement
segmented loop, intervention validator, or log.

The byte-identity compatibility requirement applies to default model
instructions and to Oracle and writer request behavior outside the repaired
evidence body. The judge's default evidence body intentionally changes: keeping
it identical would preserve issue #380.

## Alternatives Considered

### Keep rendering only visible text

Rejected. It preserves old prompt bytes by preserving the defect: tool-only
work remains invisible even though the filter admitted it.

### Slice raw records, then repair pairs in the renderer

Rejected. A renderer cannot recover the call or result already removed by the
window. Selection must use the semantic grouping before applying the limit.

### Send the complete history

Rejected. One file read or command result can dominate the model request, and
history grows with the run. Durable storage keeps the full value; the live
prompt gets a bounded, explicitly clipped view.

### Include reasoning blocks

Rejected. Reasoning may be redacted and is not required to show the actor's
observable tool choice or result. Its omission does not justify omitting the
positive tool evidence alongside it.

## Consequences

- Existing prompt-content expectations change where they represented the blind
  renderer; literal instruction tests remain unchanged.
- `window` now counts complete assistant turns. The same numeric value can
  include more raw records than before, but cannot split a call/result pair.
- Prompt size is bounded per rendered value and truncation is visible.
- The component seams are public API and must remain documented and tested.
- Running summaries, their persistence, and compact guidebook rubrics remain
  outside this decision.
