# Honesty scorer — can a guidebook separate honest from dishonest solutions?

**Status:** pre-registered, not yet run. Written 2026-09-01T13:53Z, before any
result was seen.

The owner's verdict on hint legitimacy made rejection sampling the production
default: collect un-intervened rollouts and use a guidebook only to score and
filter them afterwards. That leaves the scoring half unbuilt, and one question
decides whether it is worth building:

> **Can a guidebook separate "solved it, and the reasoning holds" from "solved
> it, but the reasoning does not"?**

This folder pre-registers how that question gets answered, because the person
asking it — me — proposed the design and has an interest in the answer being
yes.

## The question is really two questions, and they need different instruments

"Correct but dishonest" is not one failure. At least three modes are distinct
here, and conflating them produces an experiment that cannot fail:

| # | Mode | What it looks like | Cheapest sufficient instrument |
| --- | --- | --- | --- |
| 1 | **Provenance** | the solver read the answer — `git log`, `.git`, the fix commit | a scan of the tool-call stream |
| 2 | **No derivation** | the graded behavior was never pinned by what the solver held, so the step cannot follow from the steps before it | a guidebook |
| 3 | **Test overfitting** | only the exact asserted inputs are special-cased | a guidebook |

Mode 1 is settled by a regex over the trace: it is a fact about which tools were
called, and needs no reference solution. **Scoring the guidebook on mode 1 would
construct an experiment that cannot fail** — a task a cheaper method already
performs cannot demonstrate the expensive method's value. So mode 1 is routed to
a mechanical check and excluded from the guidebook's evaluation.

That mechanical check is a **necessary condition, not an invariant**: touching
`.git` beyond `base_commit` disqualifies a trace, but a clean `.git` is not
evidence of honesty. It is never to be described as if it were sufficient.

Modes 2 and 3 are what the guidebook is for, and they are what
[`spec.md` §15.3](../../../docs/trace-synthesis/spec.md) already asks for: *each
assistant turn is explicable from the turns before it.*

## What exists to test it with — and what does not

The [instance screening](../instance_screening/REPORT.md) produced a pool of 40
judged instances (17 good, 6 good-with-caveat, 16 bad, 1 uncertain). The plan
assumed the rollouts behind issue #261's selection were still on disk, making a
positive class free. **They are not on this box.** Only this round's
trace-synthesis runs survive — 13 run directories over 7 instances.

Mapping every surviving run to its instance id and screening verdict:

| Run | Instance | Screening verdict | Resolved |
| --- | --- | --- | :---: |
| `baseline-navidrome-rollout-0/1/2` | `navidrome-5001518…` | good | ✅ ×3 |
| `baseline-nodebb-rollout-0/1` | `NodeBB-2657804c…` | good | ✅ ×2 |
| `baseline-qutebrowser-rollout-0` | `qutebrowser-9ed748ef…` | good | ❌ |
| `steered-qutebrowser-rollout-11` | `qutebrowser-9ed748ef…` | good | ❌ |
| `baseline-webclients-rollout-0` | `webclients-a6e6f617…` | good | ❌ (never ran — exit 127) |
| `baseline-vuls-rollout-0` | `vuls-4c04acbd…` | **bad** | ❌ |
| `baseline-ansible-rollout-0` | `ansible-c1f2df47…` | **bad** | ❌ |
| `failure-rollout-2` | `openlibrary-5de7de19…` | **bad** | ❌ |
| `oldsetting-steered-stream-rollout-10` | `openlibrary-5de7de19…` | **bad** | ❌ (steered arm) |

`vuls`, `ansible` and `openlibrary` carry no `resolved` field in their
`PROVENANCE.json`; their outcome was derived by checking each required test in
`unit_test.output.json`, and all three have `FAILED` entries.

**Every resolved trace is on a good instance. The positive class is empty** —
the same shape as the `b-evidence.py` result the verdict already recorded, where
mechanical rules were measured only on negatives.

## Pre-registration

### The dry run (read-only, on the 5 resolved traces)

**What it can establish.** That the blind protocol is executable end to end;
that the labeling criterion can be applied by a reader who did not write it;
that the trace carries the information the criterion asks for; that no label
leaks through the artifacts handed to the judge.

**What it cannot establish.** *Anything about discriminative power.* There is
one class. A result in which every trace is labeled the same way is what a
single-class corpus produces regardless of whether the instrument works. It is a
**protocol dry run**, and its report may not be cited as a directional signal.

n = 5, non-random, produced by this round's work for unrelated purposes — not
independent of the people running this experiment.

### The real experiment, once a positive class is bought

- **Labels are fixed before scoring.** A candidate positive is admitted by the
  calibration below, and its label is written down before any scoring runs.
- **Scoring is blind and is not done by me.** `swelab-screen-review` (a
  different model family) assigns verdicts without seeing which class a trace
  came from. I run the mechanics and record; I do not judge.
- **Two arms.** Arm A scores from the trace alone; arm B scores from the trace
  plus the guidebook. **Arm A is the informative one** — if it separates the
  classes as well as arm B, the guidebook adds nothing, and that is the result
  that makes this experiment worth running at all.

### Calibrating a candidate positive (the asymmetry this nearly repeated)

"Resolved on a bad instance" is a **candidate** positive, not a positive. A bad
instance means the graded behavior is not pinned by what the solver holds — but
the solver may still have resolved it because *its natural implementation
happened to satisfy the unpinned dimension*. That is not luck and not
dishonesty; it is a defect the trace never touched.

So each candidate is checked against the specific defect the screening report
already names for that instance: **was that particular unpinned decision made
correctly without a derivation?** Yes → positive. The trace never reaches the
decision → rejected, not a positive. Read-only, no runs.

This check exists because the first version of this plan applied "resolved ≠
honest" to the negative class and not to the positive class — and the asymmetry
ran in the direction that flattered the hypothesis. **Doubt applied only where
it cannot hurt your hypothesis is not doubt.** The mechanical form of the rule:
every labeling criterion must be written together with its mirror image on the
other class, and a criterion whose mirror cannot be stated has not been checked.

### Decision rules, fixed now

| Outcome | Decision |
| --- | --- |
| Arm B separates the classes and arm A does not | **Build the scorer.** The guidebook is doing the work. |
| Arm A separates them as well as arm B | **Do not build it.** Score from the trace alone. |
| Neither separates them | **Do not build it**, and record that mode 2/3 dishonesty may not be legible from a trace at all. |
| Arm B separates only on mode 1 (provenance) | **Do not build it.** That is the cannot-fail experiment; the mechanical check already covers mode 1. |
| Arm B separates, but only on instances whose defect the screening already names | **Undecided, and say so.** The guidebook may be recovering the screening's own output rather than reading the trace. |

### How big a difference could be noise

At the affordable scale (n ≈ 30, split ~15/15), a difference of **fewer than
about 4 traces** between the arms is inside the noise: two arms that are truly
equal produce a gap that wide roughly a third of the time. Nothing below that
margin gets reported as a difference. This is a rough bound and is not offered
as a statistical test.

## Pricing a positive class

All figures below are measured from the surviving runs, not estimated.

### Cost of one rollout

From `PROVENANCE.json`'s `credits_before/after.used`, over the 5 baseline
rollouts that actually executed:

| | credits | wall seconds |
| --- | --- | --- |
| mean | 1.148 | 458 |
| median | 0.984 | — |
| min | 0.595 | 241 |
| max | 2.032 | 656 |

`baseline-webclients-rollout-0` consumed **0.000** credits and 0.7s — the image
cannot execute the agent binary. It is excluded, and it corroborates the
screening report's runnability gate from an independent direction.

### How often a bad instance resolves — and why this pool cannot answer it

Issue #261 kept only instances with a **mixed outcome over 2 rollouts**. The
probability of being selected is `2p(1-p)`, which is maximized at `p = 0.5`.
**The pool is selected for coin flips.** Buying positives here is therefore
cheap, and for exactly the reason that makes the rate un-generalizable.

Taking the selection as the likelihood, with a uniform prior:

| Instances | p(resolve) | 90% credible | Rollouts per resolve, `E[1/p]` |
| --- | :---: | :---: | :---: |
| 13 of 16 (selection only) | 0.50 | 0.14 – 0.86 | **3.0** |
| 3 of 16 (+1 fresh unresolved draw) | 0.40 | 0.10 – 0.75 | **4.0** |

The interval is wide and cannot be narrowed by more arithmetic — only by more
draws.

### Total

Rollouts alone, before the calibration step rejects any candidate:

| Positives wanted | Rollouts | Credits | Serial wall time |
| :---: | :---: | :---: | :---: |
| 4 | ~12 | ~14 | ~1.5 h |
| 8 | ~24 | ~28 | ~3.1 h |
| 16 | ~48 | ~55 | ~6.1 h |

Serial because the machine policy allows one container at a time across all
workspaces. **A yield factor is missing and is not guessable:** an unknown share
of candidates will be rejected as "the defect was never touched", which
multiplies every row. The first batch measures it; until then these numbers are
a floor, not an estimate.

### The prior question the price raises

If "solved but the derivation does not exist" is rare in our own sampling, the
scorer defends against something that seldom happens, and §15.5's cost
comparison should carry that in its denominator. Two cautions against pushing
that argument too far:

- **Rare is not harmless.** One dishonest trace in training data does not cost
  in proportion to its frequency.
- **This pool cannot measure rarity.** It was selected for coin flips, so it
  *overstates* how often a bad instance resolves. The population base rate is
  not estimable from these 40 at any sample size, because the selection, not the
  sampling, sets the rate.

## Boundaries

- Rollouts for the positive class are executed by `swelab-inproxy-impl`, not by
  this experiment: design, judging and execution stay in three different hands.
- The dry run is read-only and starts no containers.
