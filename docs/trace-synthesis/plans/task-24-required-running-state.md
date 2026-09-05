# Task 24: Required running state

## Objective

Complete the running-state half of issue #430 without adding a model call or a
new artifact. The existing judge call returns a required bounded observational
state, the next boundary receives it with the newest selected segment, the
writer receives the same bounded context plus the structured verdict, and each
valid version is recorded on the existing supervisor decision row.

Compact guidebook-rubric support is a separate task because it changes the
Oracle output schema.

## Design

### State belongs to the judgement

`running_state` is a required string in the judge tool schema and local decoder.
The standard policy starts from a six-field empty template, hands the previous
valid state to each judge request, and advances only after a valid verdict. The
state is rejected when blank or over its prompt budget; it is never silently
truncated.

The update instruction is independently replaceable, but it is not a separate
summarizer or model request. Its observational and failure-retention wording is
an intended semantic constraint for human audit rather than an invariant a
local string predicate could enforce.

### Writer and persistence reuse existing seams

The writer receives the policy-selected evidence, previous running state, task,
guidebook, prior interventions, and a structured copy of the judge verdict. No
complete historical trace is added to its request.

Both Python supervisor carriers add a valid verdict's `running_state` to their
existing decision row. The row's `cursor` or segment coordinates are the state
boundary. No running-state artifact, terminal-summary field, run metric, or
report field is introduced; ADR-0020 records why this is an additive diagnostic
field rather than a report-contract change.

## Implementation order

1. Add failing tests for the required schema/decoder field, distinct lapse
   reasons, prior-state handoff, writer context, and both persistence paths.
2. Extend the observation/verdict context and default prompt contract.
3. Advance the state in `SpeakWhenOffTrack` and hand the structured verdict to
   the writer without widening `PromptBuilder.build()`.
4. Persist valid states in the live and segmented supervisor decision rows.
5. Reconcile the spec and task index, then run the complete quality bar.

## Verification

- Cutting the saved-state handoff makes the second-request sentinel test fail.
- A response missing only `running_state` and one with an invalid verdict both
  produce lapses whose recorded reasons differ.
- A writer request contains the updated state, structured verdict/reason,
  latest selected evidence, and prior intervention, but not older evidence.
- Two segmented boundaries retain cumulative cursors while each judge receives
  only the positive evidence from the segment that just completed.
- Silent and speaking rows in both Python carriers contain the valid state and
  their existing boundary coordinates.
- The exact native-output set remains unchanged, proving no state artifact was
  added.
- `uv run pytest tests/test_supervisor_judge.py tests/test_speak_policy.py
  tests/test_supervisor_component.py tests/test_segmented_loop.py`
- `git add -A && uv run pre-commit run --all-files`
- `uv run pytest -m 'not docker'`
