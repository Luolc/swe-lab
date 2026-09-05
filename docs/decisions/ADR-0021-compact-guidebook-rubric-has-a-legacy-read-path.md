# ADR-0021: Compact guidebook rubric has a legacy read path

## Status

Accepted. The owner approved the compatibility transition and telemetry shape
on 2026-09-04; the implementation and trace-synthesis spec reconciliation land
with this record.

## Date

2026-09-04

## Context

Issue #430 adds a compact supervisor-facing rubric alongside the detailed
guidebook tutorial. The rubric must carry checkpoints, on-track evidence,
disallowed branches, off-track signals, self-correction signals, and a safe
hint justification. It is a second representation for a different reader, not
a replacement for the tutorial.

Making that section unconditionally required would be a breaking read change.
The one-off CLI reruns by default, but its supported `--resume` path trusts an
existing terminal marker and reads the producer's final shard. Workflow edge
materialization then fetches that shard's recorded `guidebook.md` for the
rollout. Programmatic `Workflow.execute()` defaults to the same resume behavior.
Consequently, a pre-rubric guidebook can reach phase C without phase B running
again, where the pre-actor validation gate would reject it if the new section
were required.

A 2026-09-04 local inventory found no guidebook or Oracle-analysis artifact in
the current checkout cache, the primary checkout cache, or `~/corpora`. That
inventory first checked that the current cache directory was absent, then used
`find` over both existing roots for matching artifact names and
`rg --hidden --no-ignore` for `guidebook` / `oracle analysis` references in
file contents. `find` emitted no matching names, and both `rg` calls exited 1
(no match), not 2 (search failure). The absence does not remove the supported
reuse path and therefore does not make a breaking schema change safe.

The compatibility path creates a second risk: a rubric-backed run and a legacy
tutorial-backed run otherwise produce traces with the same outward shape even
though their supervisors received different inputs. Any comparison mixing the
two would have an invisible stratifying variable.

## Decision

### Write strictly and read compatibly

New phase-B output must contain both representations in one `guidebook.md`:

- the complete staged tutorial; and
- one `## Supervisor rubric` section with `Checkpoints`, `On-track evidence`,
  `Disallowed branches`, `Off-track signals`, `Self-correction signals`, and
  `Safe hint justification` fields.

The phase-B observer validates the rubric as required. Phase C accepts a legacy
guidebook with no rubric so supported resume remains possible. A rubric that is
present is always validated in full; an incomplete rubric is never treated as
legacy.

The compatibility path ends when every supported resume source either was
produced by rubric-aware phase B or has been migrated. That future change may
make the rubric unconditionally required; until then, absence is a recognized
legacy version rather than malformed new output.

### The default supervisor consumes the compact representation

For a rubric-aware guidebook, the default prompt builder sends the extracted
rubric under `# Guidebook rubric` and does not send the detailed tutorial. For a
legacy guidebook, it preserves the existing full `# Guidebook` prompt as the
fallback. The complete artifact remains on disk and in `Observation.guidebook`,
so a custom prompt builder keeps ownership of how it uses the public input.

Each supervisor decision row adds `guidebook_context_mode`, whose value is
`rubric`, `legacy_tutorial`, or `null` for an unguided run. This is an additive
diagnostic field on the same open row used for issue #431 verdict telemetry and
ADR-0020 running state. It changes no terminal summary, run record, report
schema, or artifact set.

### Self-correction signals remain diagnostic

The rubric retains `Self-correction signals` because `self_correcting` remains
recorded telemetry that can support a later decision to keep or remove that
field. Those signals do not drive speech. ADR-0020 is unchanged: `off_track` is
the only verdict field that opens the speaking path.

## Alternatives Considered

### Require the rubric from every reader immediately

Rejected. It makes a valid pre-rubric shard fail at phase C solely because a
supported resume path reused it.

### Make the rubric optional for new phase-B output too

Rejected. Read compatibility should not let a new Oracle silently omit the
feature. The phase-B writer has the new contract and is checked strictly.

### Replace the tutorial with the rubric

Rejected. The tutorial and rubric serve different readers, and issue #430
requires them alongside one another. Removing detail also destroys the durable
human-audit source from which the compact claims should be checked.

### Drop self-correction signals

Rejected. The value is not trusted as a gate, but its telemetry is deliberately
retained. Removing its evidence before that telemetry is read would pre-decide
the later removal question.

## Consequences

- New Oracle output grows by one compact Markdown section while retaining the
  tutorial byte-for-byte as part of the same artifact.
- Legacy resume remains runnable and explicitly distinguishable in every
  supervisor decision row.
- A malformed partial rubric fails both write-time and read-time validation.
- Default judge and writer prompts use the compact rubric when available;
  legacy prompts retain the detailed tutorial fallback.
- `self_correcting` stays recorded but cannot veto or authorize speech.
