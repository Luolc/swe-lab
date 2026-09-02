# ADR-0016 — The endings nobody could attribute get their own word and their own count

## Status

Accepted. Supersedes the outcome taxonomy of
[ADR-0015](ADR-0015-four-words-for-how-a-rollout-ends.md) §1 and its reporting
rule §5. ADR-0015's other decisions are unchanged and not re-litigated here:
classification is still by **cause, not exit code**; there is still **one causal
bit** gating grading and the denominator together; and the denominator still
**defaults to "in"**.

## Date

2026-09-01

## Context

ADR-0015 kept an ending nobody classified **inside** the denominator, on the
grounds that doing so can only *understate* a rate while the opposite default
lets the excluded set grow unwatched. That reasoning stands. What it did not
address is the asymmetry underneath it:

> The **excluded** set is watched by construction — an ending has to be
> positively identified as ours to leave. The set kept **in** has no such
> property.

So a system failure we have not named yet is charged to the actor and is then
invisible: it surfaces only as a lower rate, and it lowers that rate by an
amount that tracks **infrastructure quality rather than the actor**. Two batches
can differ because of the machine and read as differing because of the model.

That shape is not hypothetical. Three defects found on 2026-09-01 — the
rollout-record wipe, the 1800 s grading budget spent on an agent that died in
1.4 s, and a missing `.envrc.local` — were each, before being named, exactly
"a frequently occurring system failure nobody had positively identified".

### The defect this exposed in the classifier

The reporting gap turned out to have a code half. `rollout_outcome` read:

```python
if patch is None or patch.is_empty:
  return RolloutOutcome.NO_PATCH
```

Those are not the same fact. `patch.is_empty` means an extraction ran and came
back empty — a real actor result. `patch is None` means there was no extraction
to read. **An absence of evidence was being booked as evidence the actor
produced nothing.** The same shape applied to a missing harness outcome, where
a crash and a clean stop are indistinguishable.

## Decision

**1. A sixth word, `UNCLASSIFIED`**, for an ending where the harness supplied no
outcome, so how the loop ended cannot be read. It is **not ours** — nothing
about grading or the denominator changes, and it stays in, exactly as ADR-0015
§4 requires.

**2. A missing `DiffExtractObserver` is `SYSTEM_FAILED`, not `UNCLASSIFIED`.**
This is the distinction that makes the word honest rather than a catch-all.
`CodingAgentTask` composes that observer unconditionally and declares the patch
a required output, so its absence is **broken wiring, which is ours** — there is
no ambiguity about who owns it, and it must stop the run rather than ride along
in the denominator. `UNCLASSIFIED` is for a genuinely unreadable ending, not for
every branch we have not thought about.

**3. Every rate is reported with two counts, not one:**

```
resolved 12 / 40  (3 system failures excluded, 2 unclassified)
```

The excluded count is ADR-0015 §5. The second is this ADR: it turns an ending
nobody could attribute from silence inside `unresolved` into a growing number.

## What this does not fix

A system failure that produces a **readable** outcome and an empty patch — a
harness reporting `FINISHED` while silently broken — still classifies as
`NO_PATCH` and is still counted as the actor's.

**`UNCLASSIFIED` catches missing evidence, not wrong evidence.** That is the
durable distinction to carry forward, and it is worth more than the word itself:
catching wrong evidence needs a positive signal we do not have, and manufacturing
one from the patch would feed the patch back into an attribution decision
[ADR-0011](ADR-0011-fair-retry.md) deliberately keeps it out of. The gap is
recorded here rather than papered over.

## Consequences

- A broken composition now fails the rollout entry instead of reaching the
  grading entry, so no grading container is paid for on an attempt that could
  not have produced a patch.
- `RolloutOutcome.unclassified` is the property a reporter reads. As with
  ADR-0015 §5, the **reporting** half is a contract on a bench that does not
  exist yet, not an invariant. What is enforced today is the word, its exclusion
  from the ours-set, its separation from `NO_PATCH`, and the premise that makes
  a missing extractor a defect — each with a named test in
  `tests/test_rollout.py`. The branch the count changes is named here, so the
  field is not the decoration ADR-0015's sister rule warns about.
- One test in that file had been passing for the wrong reason: its fixture
  composed no extractor, so every `NO_PATCH` assertion reached that word through
  the `patch is None` branch — including the one whose name claimed the
  opposite. The fixture now composes an extractor by default.
