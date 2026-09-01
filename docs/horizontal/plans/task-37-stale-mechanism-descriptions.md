# Task 37 — A mechanical guard for stale descriptions of decided mechanisms

**Status lives in [`plans/README.md`](README.md), not here.** This is a design
record: a proposal with its honest coverage analysis, written because the same
defect appeared three times in one day and "be more careful next time" has
therefore been falsified.

## The defect, and its three instances

A document describes a mechanism. The mechanism changes. The description does
not. To a reader the two are indistinguishable, and the reader acts on the
description — which is worse than no description, because a stale one reads as
verified.

Measured 2026-09-01, all three in the same week's PRs:

| # | Where | What the prose said | What was true |
| :---: | --- | --- | --- |
| 1 | `instance_screening/REPORT.md` (#291) | described the tautological assertion as the mechanism protecting the runnability table | that assertion had been replaced two rounds earlier |
| 2 | `honesty_scorer/README.md` (#292, round 2) | two sentences called the timeout-reporting rule an "open question" pending a ruling | the ruling was fixed, by a blind reviewer, three sections above |
| 3 | a `killpg` path (#290) | newly written prose endorsed the path as safe | the path was not safe |

They differ in an important way. **Instance 2 announces itself**: it contains
pending vocabulary ("open question"), which a script can see. **Instances 1 and
3 do not**: the prose is confident, well-formed, and simply wrong. Nothing in
the text signals staleness.

## Proposal A — a pygrep hook for unreferenced pending markers

Forbid pending vocabulary in `docs/**` and `experiments/**/*.md` unless the same
paragraph carries a `#NNN` issue or PR reference:

- vocabulary: `open question`, `pending decision`, `to be decided`, `TBD`,
  `待裁决`, `owed to`, `will be fixed later`;
- the required reference is a **`#NNN`**, not a section anchor. That distinction
  is what makes the hook fire on instance 2, whose stale sentences *did* carry
  links — to a heading in the same file.

**Acceptance criterion, as specified:** the hook must go red on #292's round-2
state (ruling fixed, sentences still present). It does, and it was checked
against that exact text rather than assumed.

**The false-positive question is real and the answer is a feature.** The
screening report has a legitimate `## Open questions` section; so do other
reports. The hook fires there too — and that is correct: **an open question with
no tracking issue is itself the defect**, and requiring `#NNN` in those sections
improves them. Note what this buys: no exemption list, therefore no knob, so the
rule cannot be quietly widened later.

**What the hook cannot do:** decide whether the referenced issue is still open.
That needs the network and belongs in CI or a periodic sweep, not in a
sub-second commit hook. The local hook enforces the *form* (a reference exists);
a separate check can enforce the *state* (it is still open). Splitting them this
way keeps the commit hook inside its budget.

## Proposal A covers one instance of three — so it is not the whole answer

This is the part worth being explicit about, because shipping A alone and
calling the family handled would itself be an instance of "a fix that covered
half".

For instances 1 and 3 there is no lexical signal at all, and no grep can
acquire one. The only mechanism that has actually worked on this family is the
one #291 landed: **make the assertion about the prose be produced by the
mechanism the prose describes** — `table_check.py` parses the paragraph's table
and checks it against the data, so editing the prose without the mechanism turns
it red. That is the general shape, and it is already written down as a
convention in [`conventions.md`](../../conventions.md) ("a check that guards a
committed artifact belongs under `tests/`").

## Proposal B — the review-time half

What remains is the case where the described mechanism is code and the
description is prose that no test reads. That is a review question, not a hook
question, and it takes the checklist form:

> **`SL10` (proposed): a PR that changes a mechanism must point at the paragraph
> describing it, or state that none exists.** Not "did you update the docs" —
> the specific, answerable form: *name the prose that describes what you just
> changed.* An unanswerable question is a finding.

The distinction from generic "update the docs" advice is that it is
**mechanically promptable** and has a definite failure state: a PR that changes
behavior and names no paragraph is either incomplete or is asserting something a
reviewer can check in one search.

## Recommendation

Ship A and B together, or neither. A alone hands back a green check that covers
a third of the family, which is the failure mode this whole task exists to
attack.
