# Can *this* guidebook be applied as a step-level criterion? — REPORT

**Run 2026-09-01, on already-purchased traces, no rollouts, no containers.**
Judge: `anthropic/claude-sonnet-5` via OpenRouter. Total cost **$0.643**
(recomputed from the preserved responses, retries included).

## Scope, and why this report renders no pass/fail

Everything below is about **one guidebook, one judge, two traces**. It is not
about guidebooks in general, and a result here could not have established
anything general even had it come out cleanly.

**No feasibility condition was declared before the run.** That is a defect in
this experiment, not a detail: a threshold chosen after seeing coverage is not a
test, and an earlier draft of this report did exactly that — it asserted that a
guidebook unable to speak to most steps fails the question, observed **70%
silent**, and still called the attempt "not falsified". Those cannot both stand.
Rather than pick whichever reading the data flatters, this report **withholds a
verdict** and states observations. The pass/fail belongs to a run whose
condition was fixed in advance.

> **No number here may be cited as a per-step rejection rate**, and none may be
> cited as evidence that the approach works. What the four rejections below
> establish is **existence** — that reviewable step-level verdicts can be
> produced from a guidebook at all — not frequency.

Three further limits decide what the numbers refer to:

1. **First-intervention on an unsupervised trajectory, not a steady-state
   rate.** Every step was produced by an actor nobody corrected; once
   interventions happen, later steps are not these steps.
2. **A judge holding a guidebook, not an oracle.** No oracle specification
   exists; a hand-written guidebook is not one.
3. **Steps are clustered within traces.** Both N: **trace-level N = 2**,
   step-level 67. The step count is not a sample size — one trace contributes 36.

## Design

| | |
| --- | --- |
| traces | `baseline-qutebrowser-rollout-0`, `steered-qutebrowser-rollout-11` |
| criterion | [`qutebrowser-qtcolor.md`](../../steered_rerun/guidebook/qutebrowser-qtcolor.md), 5 prose stages |
| step | one assistant message — the unit a per-step supervisor would hold |
| judge input | full guidebook + the step + up to 8 preceding steps, summarized |
| judge output | `adjudicable` / `stage` / `quote` / `verdict` / `reason`, JSON |

**"Silent" was a first-class answer**, not a failure mode. Auxiliary capture
records were excluded before judging — Claude Code's conversation-title call
asks for a JSON `title` and carries no tools — because judging them would
measure a population the question is not about. That removed 2 of 71.

## Results

Every figure is recomputed offline by `aggregate.py` from the preserved raw
responses; see [Reproduce](#reproduce).

| quantity | value | label |
| --- | --- | --- |
| steps judged | 69 | measured |
| parsed | 67 | measured |
| unparseable | 2 | measured |
| **adjudicable** | **20 / 67 = 30%** | measured, this guidebook + judge |
| silent | 47 / 67 = 70% | measured |
| verdicts among adjudicable | 16 on-track, **4 off-track** | measured |
| quoted span found **literally** in the guidebook | **15 / 20** | measured |
| …found after normalizing whitespace and markdown delimiters | 20 / 20 | measured |
| of the 4 off-track verdicts, quotes found literally | **1 / 4** | measured |
| trace-level N | **2** | design |

Per trace: baseline 36 steps / 11 adjudicable / 3 off-track; steered 31 / 9 / 1.

**Stage coverage is lopsided.** Cited stages: 5 (×12), 1 (×4), 4 (×3), 3 (×1);
**stage 2 never**. Stages differ in how well they map onto one observable
action — "run the neighbouring suite" does, "turn the prose into a checklist"
does not. This is a property of the guidebook's shape, and it is the finding
most likely to survive a change of instance.

### The rejections are reviewable

All four cite a guidebook span and give a checkable reason, and a reader can
agree or disagree on the merits. Two of them catch **the trap this guidebook was
written to prevent** — a stricter entry regex that makes malformed strings miss
the branch — in the **unsteered** trace, at steps 26 and 36. One catches a
baseline-run ordering violation; one catches an actor preparing to edit
pre-existing tests to match its own implementation.

**Only 1 of those 4 quotes is a literal substring**; the other three match after
normalization. They are checkable, not copy-pasteable.

## Three instrument defects

**The output limit selects which steps get an answer, and it still does.** A
judgement needing more room than the limit returns no content at all.

What the preserved responses show, on their own: **10 of 69 first-pass
judgements came back empty, every one stopping at `completion_tokens == 700`
exactly**; those 10 were re-judged, and **8 returned text while 2 came back
empty again, stopping at `completion_tokens == 2000` exactly** — those two are
the 2 unparseable rows above. So the effect was **reduced, not removed**, and it
is directional: 4 of the 8 recovered judgements were adjudicable, above the 30%
overall rate, so the truncated steps are ones this guidebook engages with more
often than average.

**The cap values themselves are asserted, not recoverable from these records.**
These verdict files predate `judge_steps.py` recording `max_tokens` inline, so
the caps (700, then 2000) live in `attempt_manifest.json` as operator-asserted
provenance; `aggregate.py` reads it and reports `max_tokens_recorded` and
`max_tokens_asserted` as **separate fields**, the first empty for this data. The
stopping points above are measured; the caps that produced them are not. Records
written by the current script carry the value inline and do not depend on the
manifest.

**A quote checker reported fabrications that were not fabrications.** The
guidebook is hard-wrapped and uses backticks, so exact-substring matching fails
on a faithful quote spanning a line break. The literal and normalized counts are
therefore both reported above rather than collapsed into one.

**An earlier cost figure omitted the retry calls** ($0.59 against the correct
$0.643). The aggregate is now computed from the same records that produce every
other number, so the two cannot drift apart again.

## What can be said

- Reviewable step-level verdicts **can** be produced from this guidebook by this
  judge: 20 exist, and 4 of them are rejections a human can check.
- This guidebook adjudicates a **minority** of steps, and its stages are not
  equally reachable at step granularity.
- Neither observation is a pass or a fail, because no condition was set in
  advance to decide which it would be.

## Reproduce

Offline, from the preserved responses — this recomputes every table above:

```sh
A=~/dev/swe-lab-artifacts/process_supervision/guidebook_step_criterion
python3 aggregate.py \
  --verdicts $A/verdicts.jsonl --verdicts $A/verdicts_retry.jsonl \
  --guidebook experiments/trace_synthesis/steered_rerun/guidebook/qutebrowser-qtcolor.md \
  --manifest $A/attempt_manifest.json
```

Later verdict files override earlier ones for the same step, so a re-judged step
replaces its truncated first answer.

The paid steps, optional and not needed to check any figure:

```sh
python3 extract_steps.py --out $A/steps.json \
  baseline-qutebrowser-rollout-0 steered-qutebrowser-rollout-11
OPENROUTER_API_KEYS=$(op read <reference>) \
  python3 judge_steps.py --steps $A/steps.json --out $A/verdicts.jsonl --max-tokens 700
OPENROUTER_API_KEYS=$(op read <reference>) \
  python3 judge_steps.py --steps $A/steps.json --out $A/verdicts_retry.jsonl \
  --max-tokens 2000 --only baseline-qutebrowser-rollout-0:11 ...   # the empty ones
```

**The credential field is passed in whole and divided inside `judge_steps.py`,
which selects the first key.** The field holds more than one, and splitting it
in a shell would route the value through argv or parameter expansion — the
channel that must not carry it. So there is no shell recipe here to copy: the
environment variable receives the field verbatim and the consuming program does
the rest.

Check that the selected key authenticates before a run: a reference resolving
successfully does not mean the value it returns is usable as-is.

Raw responses, run log and the merged summary are off-repo at
`swe-lab-artifacts/process_supervision/guidebook_step_criterion/`.
