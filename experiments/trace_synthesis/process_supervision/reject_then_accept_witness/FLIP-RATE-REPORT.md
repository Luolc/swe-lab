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
verdict ran at 700, today's material-retired judgement at 2000) — **is not
recoverable from anything #305 retained.** That is a bounded claim, and this is
the audit behind it, over the run's artifact directory
(`swe-lab-artifacts/process_supervision/guidebook_step_criterion/`, 8 files):

- **The run-time script version is not on `main`, and that is checkable from
  `main`.** #305 squash-merged, so the revision that produced these files is not
  in the published history (it survives locally as the pre-squash commit
  `3e0ce33`, whose hard-coded `max_tokens: 700` matches the manifest and whose
  run window — 19:57:22Z–20:04:01Z in `run.log` — closes seven minutes before it
  was committed). A reader with only `main` reaches the same place from the
  merged version: `c1fd9e9`'s writer records `model` and `max_tokens` **in every
  record**, and the artifacts carry neither — which is how we know they predate
  it, and why the cap survives only as the manifest's operator assertion. In
  both versions `"model"` appears solely inside the **request payload**. Neither
  opens a surface that could hold a response beyond the verdict file:
  `3e0ce33` opens two, `run.log` and `verdicts.jsonl`; `c1fd9e9` opens one,
  `args.out`, and prints progress to stdout.
- **The two response-bearing files hold 79 records over a fixed key set** —
  `verdicts.jsonl` 69, `verdicts_retry.jsonl` 10; keys `rollout`, `position`,
  `step_index`, `tool_names`, `raw`, `usage`, `judged_at`, plus `wall_seconds`
  (69 records) and `retry_max_tokens` (10). `raw` is the answer **text** (`str`
  in 67 records, `null` in 12), not the response envelope; `usage` carries only
  token and cost fields. Neither file contains a model, provider, or sampling
  field.
- **Searching all 8 files** with
  `grep -c -E '"(model|provider|response_model|temperature|top_p|top_k|seed)"' *`
  (exit 0) matches only `attempt_manifest.json` and `summary.json`, 2 each — and
  both are the **requested alias**, asserted by the operator, not read back from
  a response. `summary.json` says so itself: `"recorded_in_responses": []`. The
  remaining files are judge **input** (`parsed.json`, `parsed_final.json`,
  `steps.json`) and the progress log (`run.log`, which records only timestamp,
  step, wall time and token count).

So what is gone is the **served** model id, the provider, and the sampling
actually applied; what survives is the requested alias as an assertion. That is
a permanent cost of the earlier run, and the instrumentation added in
[#316](https://github.com/Luolc/swe-lab/pull/316) is what stops it recurring.

**No design conclusion is drawn here**, by instruction: the witness experiment is
paused pending a redesign around distributional questions.
