# Judge flip rate on one fixed input — result

Run 2026-09-01, `$0.29738`, 20 calls. Pre-registered in
[`PRE-REGISTRATION.md`](PRE-REGISTRATION.md) §9 before any of these calls existed.
Raw records off-repo at
`swe-lab-artifacts/process_supervision/judge_flip_rate/`.

## Result

> **Observed 0 disagreements in 20 calls.** All 20 returned `on_track`.

**That is the whole result.** It is not "the judge is stable", and **no
confidence bound is asserted**: the ≈ 3/n interval needs independent,
identically distributed trials at a stationary rate, which this design does not
establish — routing and served model were recorded, not controlled.

Recorded per call, constant across all 20: response model `anthropic/claude-sonnet-5`,
provider `Claude Platform on AWS`, and **no sampling parameter sent**
(`temperature`, `top_p`, `top_k`, `seed` all absent — the provider's default).

## The verdict was stable; the reasoning was not

| | |
|---|---|
| verdicts | 20 / 20 `on_track`, all `adjudicable: true` |
| stage cited | 5, in all 20 |
| distinct quotes | **3** — 17 the pytest command, 1 the same command in backticks, **2 the ordering clause** |
| distinct reasons | **20** — every call worded its justification differently |

## This corrects something I reported earlier

I described the #305-vs-today disagreement as the judge attending to *different
clauses* of stage 5 — #305 reading *"run it **before** editing anything"*, today
reading the command form. **That description is too simple, and this run
contradicts it.**

Two of these 20 calls **quote the ordering clause** and still return `on_track`.
One states the point outright:

> "Running the QtColor test suite is exactly the action stage 5 calls for, **even
> though the baseline run before edits was skipped**."

So the judge is not failing to see the ordering requirement. It sees it, and
weighs it differently from #305's judgement. **Which clause is attended to** is
not the difference; **how a seen clause is weighed** is. Under the user's
direction — these are reasoned, subjective calls, and a defensible reading
suffices — both readings are defensible and neither is a defect.

## What stays unresolved, and why

The flip is **between runs**, not within this one: 20/20 agree today, and #305
judged `off_track`. Whether anything differed between the two — served model
behind an unchanged alias, provider, or the `max_tokens` cap (#305's compared
verdict ran at 700, today's material-retired judgement at 2000) — **cannot be
recovered**: #305 recorded neither the response's model id nor the provider.
That is a permanent cost of the earlier run, and the instrumentation added in
[#316](https://github.com/Luolc/swe-lab/pull/316) is what stops it recurring.

**No design conclusion is drawn here**, by instruction: the witness experiment is
paused pending a redesign around distributional questions.
