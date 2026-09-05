# ADR-0020: Running state is a required judge output

## Status

Accepted. The owner decided the output and persistence shape on 2026-09-04;
the implementation and trace-synthesis spec reconciliation land with this
record.

This decision resolves ADR-0018's open question about the persisted supervisor
audit shape. It also supersedes that ADR's exact five-field `Observation`
allowlist by adding the host-side `running_state` field; the privileged-input
boundary remains unchanged.

## Date

2026-09-04

## Context

The bounded evidence path in ADR-0019 prevents one judge request from growing
with the whole trace, but it also removes older work from the live prompt. A
judge that sees only the latest segment can otherwise lose established facts,
repeat disproven hypotheses, or miss an unresolved failure that began before
the evidence window.

Issue #430 proposes a bounded observational state carried between segments.
That state is part of the judgement, not a second model task: the same call can
decide whether the trajectory is off track and summarize what the observable
evidence now establishes. A separate summarizer call would add cost and permit
the verdict and state to disagree about the same segment.

The persistence shape was previously left open because the report contract is
an ask-first boundary. The existing `claude_code.supervisor.jsonl` rows are an
extensible diagnostic artifact, however: `_row()` and the segmented runtime's
decision writer already add fields by row kind, and issue #431 used that path
for `off_track`, `self_correcting`, and `reason`. The closed terminal-summary
and persisted run-record schemas do not consume these row details.

## Decision

### One required output from the existing judge call

The standard judge tool requires a non-empty string field named
`running_state` alongside `off_track`, `self_correcting`, and `reason`. The
provider schema and local decoder apply the same requirement. Omitting the
field is a bounded policy lapse; it does not fall back to an empty or previous
value.

The state is bounded at 4,000 characters, reusing the existing screenful-sized
limit for one rendered tool value. An overlong answer is rejected rather than
silently truncated: truncation could erase the unresolved fact whose retention
is the purpose of the state.

The first call receives a complete six-line empty state. Every later call
receives the last valid state plus the complete selected latest segment. The
state covers:

- current checkpoint;
- files inspected or changed;
- hypotheses tested and observed results;
- tests, errors, and other established facts;
- current plan; and
- unresolved contradictions or blockers.

The default update instructions are active on every standard judge call and
can be replaced independently of the judge's system instructions. They direct
the model to use observable evidence and retain unresolved failures. Those are
intended semantic properties, audited by a reader; no local predicate claims
to prove that prose is observational or complete.

### The writer receives the bounded context and structured verdict

When the judge reports an off-track trajectory, the writer receives the same
previous state, selected latest segment, guidebook, task, and prior delivered
interventions. It also receives the judge's structured verdict, including the
updated `running_state` and reason. The writer receives no earlier raw trace
outside the selected segment.

### Persist on the existing decision row

Every valid updated state is added as `running_state` to the existing
`claude_code.supervisor.jsonl` decision row. That row already carries `cursor`
or the segmented runtime's `segment` / `cut_at_turn`, so the field records both
the state version and its boundary without another artifact. A judge lapse
creates no new version; its existing lapse row records the distinct decoding
reason.

This is not a report-contract change. It extends the already declared,
open-shaped supervisor diagnostic row by the same mechanism used for issue
#431's verdict telemetry. It does not add or change terminal-summary fields,
run metrics, `AttemptRecord`, or any report output.

## Alternatives Considered

### Make running state optional

Rejected. Optional prompting or an optional tool field makes a missing update
look like a legitimate state transition. A schema is the provider's promise;
the local decoder enforces that promise rather than inventing a softer one.

### Use a separate summarizer model call

Rejected. It adds one paid call per boundary and lets two calls form competing
accounts of the same evidence. The verdict and update are one tool output from
one judge call.

### Write a separate running-state artifact

Rejected. The existing decision row already has the boundary and an extensible
payload. A second file would introduce a join and a new artifact lifecycle for
the same fact.

### Add running state to the report contract

Rejected. No current report consumer needs the prose state, and the existing
supervisor artifact already preserves it for audit and replay. Expanding the
closed run-record or terminal-summary schema would be a different decision.

## Consequences

- Default judge requests and their tool schema change intentionally; prompt
  byte pins must name the new required contract.
- A missing, blank, non-string, or overlong state is a loud bounded lapse.
- The policy advances state after every valid verdict, including silent ones
  and verdicts whose later writer call lapses.
- Writer requests remain bounded to the selected segment and prior
  interventions while gaining the structured verdict and updated state.
- The state stays host-side and is persisted only inside the existing
  supervisor decision log; no sandbox file or report field is added.
- Compact guidebook-rubric support remains a separate change because it alters
  the Oracle output schema.
