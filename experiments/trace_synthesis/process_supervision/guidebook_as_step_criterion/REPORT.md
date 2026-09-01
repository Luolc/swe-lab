# Can a guidebook be used as a step-level criterion? — REPORT

**Run 2026-09-01, on already-purchased traces, no rollouts, no containers.**
Judged with `anthropic/claude-sonnet-5` through OpenRouter. Total cost **$0.59**.

## What this run can establish, and what it cannot

**The direction is the whole point, so it goes first.** With **2 traces**, a
*failure* would falsify "a guidebook can be applied at step granularity"; a
*pass* establishes **nothing** about how often an oracle would reject.

> **No number in this report may be cited as a per-step rejection rate.** The
> rejection counts below are properties of *this guidebook, this judge, these
> two traces* — they are not an estimate of anything, and they carry no
> confidence interval because none would mean anything at this N.

Three further limits, each of which changes what the numbers refer to:

1. **This is a first-intervention rate on an unsupervised trajectory, not a
   steady-state rate.** Every step here was produced by an actor nobody
   corrected. Once interventions actually happen, later steps are not these
   steps. The two quantities are different and ordinary language does not
   distinguish them.
2. **It measures what a judge holding a guidebook rejects, not what a real
   oracle would reject.** No oracle specification exists; a hand-written
   guidebook is not one.
3. **Steps within a trace are not independent.** Report both N: **trace-level
   N = 2**, step-level N = 67. The step count is not a sample size — one trace
   contributes up to 36 of them.

## Design

| | |
| --- | --- |
| traces | `baseline-qutebrowser-rollout-0`, `steered-qutebrowser-rollout-11` |
| criterion | [`qutebrowser-qtcolor.md`](../../steered_rerun/guidebook/qutebrowser-qtcolor.md), 5 prose stages |
| step | one assistant message — the unit a per-step supervisor would hold |
| judge input | full guidebook + the step + up to 8 preceding steps, summarized |
| judge output | `adjudicable` / `stage` / `quote` / `verdict` / `reason`, JSON |

**"Silent" was made a first-class answer**, not a failure mode: a guidebook that
cannot speak to most steps fails this feasibility question regardless of how
good its verdicts are on the rest.

Auxiliary requests in the capture were excluded before judging — Claude Code's
conversation-title call asks for a JSON `title` and carries no tools, and
judging it as a trajectory step would measure a population the question is not
about. That removed 2 of 71 records.

## Results

| quantity | value | label |
| --- | --- | --- |
| steps judged | 69 | measured |
| parsed | 67 (2 unparseable) | measured |
| **adjudicable** | **20 / 67 = 30%** | measured, this guidebook + judge |
| silent | 47 / 67 = 70% | measured |
| verdicts among adjudicable | 16 on-track, **4 off-track** | measured |
| quotes traceable to the guidebook | **20 / 20** | measured |
| trace-level N | **2** | design |

Per trace: baseline 36 steps / 11 adjudicable / 3 off-track; steered 31 steps /
9 adjudicable / 1 off-track.

**Stage coverage is lopsided.** Of 20 adjudicable steps the cited stage was
5 (×12), 1 (×4), 4 (×3), 3 (×1) — and **stage 2 was never cited once**. A
guidebook's stages are not equally reachable at step granularity: stage 5 ("run
the neighbouring suite") maps onto observable single actions, stage 2 ("turn the
prose into a checklist") largely does not.

### The rejections are reviewable, which was the third question

All four cite a verbatim guidebook span and give a checkable reason. Two of them
catch **the exact trap the guidebook was written to prevent** — a stricter entry
regex that makes malformed strings miss the branch — in the baseline trace, at
steps 26 and 36. One catches a baseline-run ordering violation; one catches an
actor preparing to edit pre-existing tests to match its own implementation.

A human reading those four against the guidebook can agree or disagree on the
merits. That is the property this question was asking about.

## Two instrument defects, both found before they reached a conclusion

**`max_tokens = 700` truncated 10 of 69 judgements**, and the truncation was
**not random**: every one had `completion_tokens == 700` exactly, and they
clustered mid-trajectory. Re-judged at 2000 tokens, all 10 returned content and
**4 of 8 parsed retries were adjudicable — above the 30% overall rate**. So the
first pass would have computed coverage on a population that systematically
dropped the steps the guidebook engages with most. The figures above include the
re-judged steps.

**My quote checker reported 3 fabricated quotes that were not fabricated.** The
guidebook is hard-wrapped and uses backticks; an exact-substring test fails on a
quote spanning a line break or dropping markdown delimiters. After normalizing
whitespace and delimiters, **20 of 20 quotes are verbatim**. The instrument was
wrong, not the judge — checked before the claim was written down rather than
after.

## Conclusion, attributable

**A guidebook of this shape can be applied at step granularity, and the attempt
is falsifiable — it was not falsified here.** The evidence for that is narrow
and specific: a judge holding only the guidebook produced verdicts on 30% of
steps, cited real spans for all of them, and its rejections include the failure
the guidebook exists to prevent, found in the trace that was not steered.

**What is not established:** any rejection rate; anything about a real oracle;
anything that survives a change of instance, guidebook, or judge. The 70% silent
figure is the honest headline for *feasibility* — it says a guidebook adjudicates
a minority of steps, which is a fact about design cost, not about frequency of
error.

## Reproduce

```sh
python3 extract_steps.py baseline-qutebrowser-rollout-0 steered-qutebrowser-rollout-11 > steps.json
OPENROUTER_API_KEY=... python3 judge_steps.py
```

Raw judgements, run log and merged verdicts are off-repo at
`swe-lab-artifacts/process_supervision/guidebook_step_criterion/`.
