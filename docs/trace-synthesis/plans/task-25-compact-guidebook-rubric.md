# Task 25: Compact guidebook rubric

## Objective

Complete issue #430 by adding a compact supervisor-facing rubric beside the
detailed guidebook tutorial. New Oracle output writes both, default supervisor
prompts consume the rubric, and supported legacy resume remains explicit and
compatible.

## Design

### One artifact, two readers

`guidebook.md` retains its complete staged tutorial and gains a single compact
rubric section. The rubric lists checkpoints, on-track evidence, disallowed
branches, off-track signals, self-correction signals, and the justification for
a safe hint. Phase B requires all six fields from new output. Phase C accepts a
tutorial-only legacy artifact, but a partial rubric is invalid.

The public guidebook parser exposes the compact section without mutating the
artifact. The default prompt builder sends that section under
`# Guidebook rubric`; a custom builder still receives the complete guidebook on
the established observation field. A legacy artifact follows the former full
tutorial prompt path.

### Make the compatibility branch observable

Live and segmented supervisor decision rows record
`guidebook_context_mode = rubric | legacy_tutorial | null`. The field makes the
temporary fallback a visible stratifying variable without adding a report
field or artifact. ADR-0021 owns the compatibility rationale and its exit
condition.

`Self-correction signals` explain recorded `self_correcting` telemetry only.
The implementation does not change the speaking state machine: `off_track`
remains its only verdict gate.

## Implementation order

1. Add failing schema tests for strict phase-B output, accepted legacy reads,
   and rejected partial rubrics.
2. Add the rubric template, parser, and strict-write / compatible-read checks.
3. Add failing prompt tests proving the rubric reaches the default supervisor
   while the tutorial remains complete in the artifact.
4. Add failing two-mode telemetry tests, then record the selected context mode
   on both supervisor carriers.
5. Reconcile the live spec and task index, then run the complete quality bar.

## Verification

- Removing the rubric from new Oracle output makes its phase-B validity test
  fail, while the legacy phase-C acceptance test remains green.
- Returning the complete tutorial instead of the extracted rubric makes the
  prompt-delivery test fail.
- Replacing or truncating the tutorial makes the coexistence test fail even
  when the rubric remains valid.
- Removing `guidebook_context_mode`, or giving both guided modes one value,
  makes the two-mode decision-row test fail.
- Restoring `self_correcting` as a speaking veto keeps its existing regression
  test red; this task does not alter that path.
- `git add -A && uv run pre-commit run --all-files`
- `uv run pytest -m 'not docker'`
