# ADR-0022: `self_correcting` leaves the verdict

## Status

Accepted. The owner decided on 2026-09-05 that the field has served its
purpose and is removed outright; the implementation, the trace-synthesis spec
reconciliation and the plan-index reconciliation land with this record.

This supersedes two earlier decisions:

- [ADR-0021](ADR-0021-compact-guidebook-rubric-has-a-legacy-read-path.md)'s
  *Self-correction signals remain diagnostic* decision, and the six-field
  rubric contract that rested on it. Everything else in ADR-0021 — strict
  write, compatible legacy read, rubric-over-tutorial delivery,
  `guidebook_context_mode` — stands unchanged.
- [ADR-0020](ADR-0020-running-state-is-a-required-judge-output.md)'s
  enumeration of the fields `running_state` sits alongside. The decision that
  `running_state` is required, bounded and non-falling-back is unchanged; only
  the list of its neighbours is.

## Date

2026-09-05

## Context

The field had three lives.

It began as the speak gate's second veto: a judge answered both *is the actor
off the criterion's path* and *would it come back by itself*, and a `True`
answer to the second withheld the correction. Issue #432 demoted it to
*recorded but never acted on*, leaving `off_track` as the only gate. What
justified keeping it in the contract at that point was named in ADR-0021: its
telemetry could support a later decision to keep or remove it, and removing
its evidence before that telemetry was read would have pre-decided the
question.

That telemetry has now been read. Downstream results on 0.3.4 — the first
release carrying the demoted field — put Opus at roughly 40% solve and a
weaker internal model at roughly 10%, well above what the two-veto gate
produced. The owner's conclusion is that the second question is not paying for
the contract surface it occupies.

Keeping a recorded-but-unused field is not free. It is a required output of
every judge call, so it consumes tokens and instruction budget on a question
whose answer is discarded; it appears in every decision row, where a reader
must be told to ignore it; and it keeps a rubric section in every guidebook
whose only purpose is to explain it.

## Decision

### The concept is removed, not demoted again

`self_correcting` leaves the judge tool schema (both `properties` and
`required`), the `Verdict` dataclass, both default judge instruction texts,
the supervisor and segmented-loop decision rows, and the writer's structured
verdict block. `Self-correction signals` leaves the guidebook rubric contract
and the Oracle's rubric rules.

The speaking gate is untouched: `off_track` remains the only field that opens
it, exactly as #432 settled.

### An answer that still carries the field is unusable

The judge tool declares `additionalProperties: false` and the local decoder
rejects unexpected fields, so a provider response written against the old
schema is rejected as an unusable judge answer, which the policy records as a
bounded lapse. This is a **breaking change to the judge contract** and is
deliberate: it is the direct consequence of the strict-answer rule, and an
accept-and-ignore path would make a stale provider indistinguishable from a
current one.

### A legacy rubric is still readable

Rubric validation is presence-checked per field, so a guidebook written under
ADR-0021 — carrying `Self-correction signals` as a sixth section — still
validates and is still delivered to the supervisor whole. Nothing about
ADR-0021's legacy read path changes, and no stored artifact is invalidated.

## Alternatives Considered

### Keep it for one more round of telemetry

Rejected. This is the round ADR-0021 was waiting for. Holding the field for a
further round would need a question the next round could answer that this one
did not, and there is none.

### Accept the field and ignore it

Rejected. Silently discarding a field means a judge answering under the old
schema and one answering under the new produce indistinguishable verdicts —
the observation would have no power to separate them, which is the property
that makes it worthless as a check.

### Keep `Self-correction signals` in the rubric

Rejected. The section exists to explain a recorded field. With the field gone
it instructs the Oracle to write signals that no output consumes, and it
directs the judge's attention at a question it is no longer asked.

## Consequences

- The judge answers one question and reports two supporting values, rather
  than answering two.
- Any deployment pinned to the pre-removal tool schema starts producing
  lapses instead of verdicts on upgrade; there is no compatibility flag.
- Guidebook rubrics shrink from six fields to five; existing six-field
  artifacts remain valid and readable.
- The frozen `n_batching_replay` experiment records keep the field. It is a
  legacy column there: `analyze.py` reads it where present, so the numbers in
  that experiment's `REPORT.md` remain reproducible from the committed
  `judgments.jsonl` files, and rows recorded after the removal fall through to
  the `off_track`-only gate.
