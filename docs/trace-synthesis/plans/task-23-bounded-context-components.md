# Task 23: Bounded context components

## Objective

Repair the Python supervisor's evidence path so model calls see complete recent
assistant turns, including their tool calls and results, while keeping the raw
conversation unchanged. Expose selection, rendering, and prompt assembly as
small replaceable components so policy experiments can change one concern
without copying the segmented loop, validation, or logging machinery.

This task implements the second stage of issue #430 and closes the renderer
defect described by issue #380. Running-state summarization, summary
persistence, and a compact guidebook rubric remain deferred until the report
contract is decided.

## Design

### Complete assistant turns are the selection unit

The default selector groups each assistant message with the following tool
result records up to the next assistant message. Its limit counts those groups,
not raw messages. A boundary therefore cannot retain a call while dropping its
result merely because the result was the next record. A call with no matching
result remains visible and is marked missing by the renderer.

The selected values retain the original typed blocks. Selection does not clip
or rewrite the durable evidence.

### Rendering pairs by stable tool-use ID

The default renderer emits one section per assistant turn. It omits reasoning
blocks, retains the tool name and tool-use ID, serializes tool input
deterministically, and pairs results by ID. Result status is explicit for both
success and error. Inputs, results, and visible text have independent character
budgets; exceeding a budget adds a marker with the omitted character count.

Visible assistant text is a renderer option. The default retains bounded text
from a turn that frames a tool call, a text-only turn, and the final selected
turn. Disabling it removes only visible prose, never tool evidence.

### Replaceable boundaries

Public ABCs define the selector, renderer, and prompt-builder contracts. Their
default implementations are injected into `SpeakWhenOffTrack`, `ModelJudge`,
and `ModelWriter`. The construction helper wires the defaults, while direct
construction can replace one collaborator without replacing the policy state
machine or the supervisor loop.

## Implementation order

1. Add discriminating tests for a raw-record boundary through a call/result
   pair, paired rendering with reasoning present, and explicit truncation.
2. Add the public assistant-turn record and selector ABC/default.
3. Add the renderer ABC/default and prompt-builder ABC/default.
4. Inject the selector into `SpeakWhenOffTrack` and prompt components into the
   two model calls; update the construction helper.
5. Add synthetic replacement tests for each component and retain the literal
   default-instruction tests.

## Verification

- The record-boundary test fails when selection is changed back to raw slicing.
- The reasoning test asserts the tool call and result are both present, and
  fails when rendering is changed back to `TextBlock` only.
- The long and short arms differ by an explicit truncation marker.
- A synthetic replacement of each component changes only its owned output.
- `uv run pytest tests/test_context_components.py tests/test_speak_policy.py
  tests/test_supervisor_judge.py`
- `git add -A && uv run pre-commit run --all-files`
- `uv run pytest -m 'not docker'`
