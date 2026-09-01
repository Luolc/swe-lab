# Scoring operations — who runs it, what they receive, what is still open

The scoring **criteria** are registered in [`README.md`](README.md) and are not
restated here: the unit of analysis, the class labels with their mirrors, the
blinding rule, the binary score scale, the primary endpoint, the tie rule, arm
B′ and its seeded derangement, and the decision table. This document adds only
the **operational layer** those leave undefined — who executes a scoring round,
how they are isolated, exactly what reaches them, and which choices are
deliberately not made here.

Written before the first batch finished, so that no part of it could be shaped
by results. What it cannot claim is that its author is blind: see
[Author standing](#author-standing).

## What a scoring round on the first batch can and cannot produce

**It cannot produce a Build decision.** That is settled by the registered
[decision table](README.md#decision-rules--the-target-design-not-reachable-from-this-document)
and [Scope](README.md#scope-this-document-registers-the-pilot-not-the-build-decision),
not decided here; the only thing this document adds is the arithmetic that puts
this batch on the wrong side of it — four cells at `k = 2` give **8 scored
traces** against a gate that requires 24.

So a scoring round run on this batch is a **rehearsal of an untested mechanism**,
and that is worth doing on its own terms: the [dry run](DRY-RUN.md) established
that the scoring leg has *never been exercised* — no judge has been handed a
bundle and no verdict collected. The rehearsal's deliverable is whether the
mechanism runs end to end, not what the verdicts were.

**The rehearsal's output must not be reported as an arm comparison**, not even
as a preliminary one, and not with a caveat. A number that exists is quoted
later; the caveat is not.

## Who scores, and how they are isolated

[Arm independence](README.md#the-scoring-protocol-fixed-now) is registered. What
it leaves undefined is **who assigns the judge and what that assigner knows** —
which matters because the assigner's knowledge reaches the judge through the
brief, a channel no session boundary closes.

**Requirements on the judge**, all mechanical rather than dispositional:

| Requirement | Why it is not satisfiable by instruction alone |
| --- | --- |
| No access to this repository | `candidates.json` maps instance → verdict, so a judge that can read the repo can look the label up after recovering the repository from the trace — which the [dry run](DRY-RUN.md) showed is always possible |
| No context from this workspace | The outcome values are in this workspace's conversations; an agent forked from one inherits them |
| A separate session per arm | Arm A's verdicts are context for arm B otherwise, and the comparison is between arms, not between a judge's first and second pass |
| Receives no message that carries an outcome value | The brief is the one channel the assigner controls; it is therefore the one that has to be checked |

**The assigner is not the judge, and the assigner is not blind.** The people who
have seen outcome values for this batch are, at time of writing, the pilot's
executor, the protocol's author, and the coordinator. None of them may score.
The assigner must state, in the brief itself, that the brief was checked
character by character for outcome values and found to contain none — so that
the judge is entitled to refuse if it finds one. A check only the assigner knows
about is one the judge cannot rely on.

## What reaches the judge, item by item

The registered [blinding rule](README.md#the-scoring-protocol-fixed-now) fixes
*what* a bundle contains and what is stripped. This table does not repeat that
list; it adds the two things the rule leaves out — **the reason each stripped
item has to go**, which is what an operator needs in order to recognise a
new field as belonging on the list, and **two fields the registered rule does
not name**.

| Stripped item | Why it must go |
| --- | --- |
| `base_commit` — *not named in the registered rule* | Identifies the instance as precisely as its id |
| Sweep / rollout id — *not named in the registered rule* | Recovers position in the buying order, which is ordered by class |
| `instance_id` | Directly keys the screening verdict |
| `resolved` flag | Is the label |
| Screening verdict | Is the label's source |
| Arm label (A / B / B′) | Reveals which condition is being scored, and B′ is only meaningful while indistinguishable from B at scoring time |

**The repository name is the one identifier that cannot be stripped** — it is in
file paths, imports, test names and the diff. That is why the protocol equalises
class counts *within* each repository: recovering the repo is expected, and made
uninformative rather than prevented.

**The check is `check_bundles.py`**, run before any bundle is handed over. It is
a gate: non-zero exit blocks the round. Two of its behaviours are deliberate and
come from failures this experiment already had —

- **a check that could not run is a failure, not a pass.** That covers finding
  no bundles, a bundle with no ground-truth entry, and a ground-truth row
  missing any field the gate is supposed to check for — a field absent from the
  truth cannot be searched for, and reporting `ok` for it is a claim the gate
  did not earn. A checker that says nothing is indistinguishable from one that
  found nothing wrong, and that is how the dry run's leak survived an audit
  that reported `ok`;
- **both shapes a label can take are checked.** A long identifier
  (`instance_id`, `base_commit`) leaks by appearing at all. The values that
  actually decide the experiment do not: `resolved` is boolean and `arm` is a
  single letter, so scanning for them bare would fire on ordinary prose. What
  is detectable, and what a serialised label looks like, is the **field beside
  its value** — `"resolved": true`, `arm = B`, `screening_verdict: good`;
- **the repository name is reported, never failed on.** Failing on something
  unremovable would make every bundle unshippable and train the operator to pass
  an override, which removes the gate for everything else too.

**A gate's claim cannot be downgraded.** `AGENTS.md` offers an escape hatch for
an unbacked invariant: add a test, or reword the sentence to "intended / not
enforced". That hatch is for **descriptive** assertions. It does not apply to a
gate, whose entire reason to exist is enforcement — softening what a gate claims
does not weaken a sentence, it **deletes the gate and leaves a script that still
exits 0**, which is worse than having neither, because the green is read as
evidence. If this gate cannot check something it is documented as checking, the
resolution is to check it or to remove the field from its list, never to soften
the wording.

**When a gate should fail, and when it should only report.** These two rules
read as contradictory — one says fail loudly on nothing, the other says never
fail on something plainly present — so the discriminant is written down rather
than left to taste:

> **A gate that alarms on a condition the operator cannot remove trains the
> operator to disable the gate.** And a disabled gate stops catching everything
> else it was there for, not just the unfixable thing.

The test is not the false-positive rate; it is whether the operator can make the
trigger go away. A missing bundle is fixable — produce it, or fix the path — so
failing is useful pressure. A repository name in a diff is not removable by any
action available at scoring time, so failing on it buys nothing and costs the
gate.

The gate checks the artifact. It does **not** check what the judge can otherwise
reach — the dry run's finding was precisely that the bundle was clean while the
label sat one lookup away. That is what the first table above is for, and it is
enforced by *choosing* a judge, not by scanning a file.

## Author standing

The author of this document has seen the per-slot `resolved` values for the
first two cells of the first batch. That is disclosed rather than argued away,
and it is why the sections above fix only *mechanisms* — who may score, what is
stripped, what the gate does — and settle **no numeric threshold, cut point, or
tie rule**. Those are already registered, and where anything remains open it is
handed over below rather than chosen here.

Disclosure is not neutrality. It is recorded so that a later reader can discount
this document, not so that it can be trusted.

## What is deliberately left open, and how it is handed over

One choice remains genuinely open: **whether a rehearsal round is run on the
first batch at all**, and if so what its record is allowed to say.

Three options, and the rule that produced them: they are the three positions on
the single axis *how much of the mechanism gets exercised before a cohort large
enough to conclude exists* — none, the mechanism only, or the mechanism plus a
recorded but unreportable verdict set. The axis is the derivation; the options
are its endpoints and midpoint, not a curated list.

1. **No rehearsal.** Wait for a cohort of 24. The scoring leg stays untested
   until the batch that also has to conclude with it.
2. **Rehearsal, verdicts discarded.** Run arms A, B and B′ end to end; record
   that the mechanism ran, the exclusion counts, and any operational defects;
   **destroy the verdicts** so no accuracy figure exists to be quoted later.
3. **Rehearsal, verdicts retained under seal.** As (2), but the verdicts are
   kept in the run record, marked non-reportable, and never aggregated.

**Relevant fact already recorded in this document**, carried here so the
deciding party sees it rather than having to find it — it is not a
recommendation, and it is stated above for its own reasons:

> A rehearsal's output must not be reported as an arm comparison, not even with
> a caveat, because **a number that exists gets quoted later and the caveat does
> not.**

It bears on this choice because the three options differ in precisely whether a
number from this batch exists at all.

**The blind party may reject all three and write its own.** A menu written by a
non-blind author bounds the outcome even when someone else picks from it — the
author of a menu is the author of the decision, not the person choosing. So the
handover is explicitly "here are three we thought of, and the axis we derived
them from; you may discard the menu entirely."

This choice is not made here, and no recommendation is offered, because every
option differs in exactly what a non-blind author would have a stake in: whether
a number from this batch exists at all.
