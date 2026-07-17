# Annotation Prompt Variance Experiment Report

| Field | Value |
| --- | --- |
| Author | Liangchen Luo |
| Harness | Claude Code — orchestrating model Claude Opus 4.8 (`claude-opus-4-8[1m]`) |
| Annotation model under test | Claude Sonnet 4.6 (`claude-sonnet-4-6`) |
| Final annotation prompt | [`tasks/related_files/annotation_prompt.py`](../../../src/swe_lab/tasks/related_files/annotation_prompt.py) |
| Final aggregator prompt | [`tasks/related_files/aggregator.py`](../../../src/swe_lab/tasks/related_files/aggregator.py) |
| Started | 2026-07-06 20:51 PDT |
| Last updated | 2026-07-07 00:55 PDT |

## Contents

- [Conclusions and Recommendations](#conclusions-and-recommendations)
- [Method](#method)
- [Baseline Results](#baseline-results)
- [v2 Results](#v2-results)
- [v3 Results (Recommended Prompt)](#v3-results-recommended-prompt)
- [Generalization: Second Suite](#generalization-second-suite)
- [Aggregate Experiment](#aggregate-experiment)
  - [Aggregator Prompt Iteration](#aggregator-prompt-iteration)
- [Post-Experiment: Batch-QA Coverage Refinement](#post-experiment-batch-qa-coverage-refinement)
- [Cost](#cost)
- [Remaining Issues and Open Questions](#remaining-issues-and-open-questions)

## Conclusions and Recommendations

- **Adopt the v3 prompt.** It gives stable file selection and tight,
  reproducible line ranges, with the v2 regressions fixed, and it **generalizes**
  — validated across two suites of four languages each (8 instances / 8 repos),
  all runs valid. The three levers that mattered: (1) no trivial one-line import
  snippets; (2) take a small fully-relevant file whole instead of over-splitting
  it; (3) don't pad with peripheral files (localization / generated / schema).
- **Residual variance is mostly inherent** — chiefly *how much of a test to
  include* (one suite vs several). Prompt wording alone will not drive this to
  zero.
- **Sample-and-aggregate works (LLM reconciler preferred), but is optional.**
  An LLM aggregator over the 3 runs produces a clean single answer that keeps
  genuinely-relevant regions, drops over-broad/peripheral ones, and tightens
  ranges — beating both a single run and the mechanical majority (which loses
  recall). See [Aggregate Experiment](#aggregate-experiment). Cost ~4×
  single-run per instance. Use it for higher per-instance reliability on the
  ambiguous minority; v3 single-run is good enough (and cheaper) for a first
  pass.
- **Validate at scale on more instances.** `n = 3` × one-instance-per-language
  is enough to steer the prompt but too small for firm per-file claims; a
  broader, more diverse sample would confirm.

## Method

- **Prompt versions.** The prompt is iterated across rounds (baseline → v2 →
  v3); each round re-runs the full sample so versions can be compared directly.
- **Instances.** One instance per language, annotated **3 times** each with
  Sonnet 4.6. Two suites, for diversity:
  - `s1` — flipt (go), qutebrowser (python), NodeBB (js), element-web (TS code,
    but labeled `js` in the dataset).
  - `s2` — different repos: navidrome (go), openlibrary (python), webclients
    (js), tutanota (the only true `ts` repo).
- **Variance axes** (not just snippet count):
  1. **File agreement** — do the 3 runs select the same files? (`intersection /
     union`).
  2. **Line-range agreement** — for files chosen by every run, the line-coverage
     overlap across runs (`line-IoU`) *and* whether the ranges are reasonable
     (focused vs whole-file, enough vs too little). This second, range-level
     axis is essential: the runs often agree on the files but differ on the
     ranges, which is where the real variance lives (drivers A/B/C below).
  Judged both mechanically (`analyze.py`) and by reading the actual snippets
  against the problem statement / gold patch.
- **Aggregation.** A majority-consensus aggregate over a round's 3 runs
  (`aggregate.py`) is analyzed as a cheap proxy for sample-and-aggregate — does
  combining independent samples beat a single run?
- **Cost and tokens** tracked per run. Raw data: `runs/<round>/` (full
  annotations + `summary.jsonl`); regenerate metrics with [`analyze.py`](analyze.py) `<round>`
  and [`aggregate.py`](aggregate.py) `<round>`.

## Baseline Results

_Run: 2026-07-06 20:51–21:22 PDT · [raw data](runs/s1-baseline/)._

All 12 runs completed and passed validation (`valid = 3/3` everywhere).

| lang | snippet counts | file agreement | notable line-IoU |
| --- | --- | --- | --- |
| go | 6 / 7 / 6 | 4/5 (80%) | evaluator.go 91%, rest 100% |
| python | 7 / 8 / 6 | 5/5 (100%) | **qtlog.py 17%**, qtnetworkdownloads 30%, log.py 45% |
| js | 14 / 15 / 14 | 10/10 (100%) | **test/user/emails.js 29%**, keys.js 53%, rest 72–100% |
| ts | 9 / 15 / 8 | 8/8 (100%) | 85–99% (main ranges agree) |

**Finding: file selection is stable; the variance is in the ranges.** Which
*files* to read is highly reproducible. Run-to-run differences come from the
line ranges, with three concrete drivers:

- **(A) Occasional whole-file over-inclusion.** python `qtlog.py`: two runs took
  focused ranges `[1-33] + [200-214]`; one took `[1-213]` — the entire 213-line
  file.
- **(B) Inconsistent trivial 1-line snippets.** ts run2 added five separate
  single-line *import* snippets that the other runs omitted — the reason its
  count jumped to 15 vs 8–9.
- **(C) Coverage extent.** js `test/user/emails.js`: one run covered four test
  suites `[42-138]`, another only the single relevant suite `[111-138]`.

The rest is benign boundary jitter (a few lines), which is acceptable. No run
was *wrong* — all reasonable, on-target files — but ranges vary more than ideal.

## v2 Results

_Run: 2026-07-06 21:29–21:38 PDT · [raw data](runs/s1-v2/)._

Changes: added an explicit JSON output example; "range = the enclosing unit,
never select an entire file"; "cover the whole relevant unit, not a sub-slice";
"no trivial single-line snippets". Effect was **mixed — one clear win, partial
wins, two regressions**:

| lang | file agree | counts | key line-IoU changes |
| --- | --- | --- | --- |
| go | 80 → 100 | 6/7/6 → 8/7/8 | evaluator.go 91→**100** ✅; **errors.go 100→20** ❌; server.go 100→72 ❌ |
| python | 100 → 100 | 7/8/6 → 7/8/7 | log.py 45→**100** ✅; qtnetworkdownloads 30→68 ✅; qtlog.py 17→12 ❌ |
| js | **100 → 69** ❌ | 14/15/14 → **20/16/12** ❌ | mongo/postgres →100 ✅; delete.js 80→21 ❌; emails 29→37 |
| ts | 100 → 100 | **9/15/8 → 8/9/8** ✅ | mostly →**100%** ✅ |

- **Win:** (B) fixed — ts no longer emits import snippets (count 15→9, IoU
  ~100%). Cleanest, most attributable improvement. Coverage (C) also improved in
  places (python `log.py`, `qtnetworkdownloads`; go `evaluator.go`).
- **Regression:** "never select an entire file" was too absolute. go `errors.go`
  is a small 56-line file of error-type definitions that all baseline runs
  sensibly took whole (`[1-56]`, 100%); v2 split it inconsistently (20%). js
  also got less stable (peripheral files included inconsistently; wider count
  spread).
- **Verdict:** net wash. Lesson: forbid grabbing a *large* file when only part
  is relevant, but allow a *small, fully-relevant* file whole.

## v3 Results (Recommended Prompt)

_Run: 2026-07-06 21:41–21:50 PDT · [raw data](runs/s1-v3/)._

Changes: kept v2's wins; softened to "don't grab a whole *large* file when only
part is relevant; a *small, fully-relevant* file may be taken whole"; added
"select only files a solver must read; don't pad with peripheral files". All 12
runs valid/complete.

Evolution of the headline numbers (baseline → v2 → v3):

| metric | go | python | js | ts |
| --- | --- | --- | --- | --- |
| file agreement | 80 → 100 → 75 | 100 → 100 → 100 | 100 → **69 → 90** | 100 → 100 → 100 |
| snippet counts | 6/7/6 → 8/7/8 → 6/5/6 | 7/8/6 → 7/8/7 → 5/6/7 | 14/15/14 → 20/16/12 → 12/12/16 | 9/15/8 → 8/9/8 → **8/8/8** |

Targeted fixes landed:

- **go `errors.go`: 20% → 98%** — the small fully-relevant file is taken whole
  by all runs again; the v2 regression is fixed.
- **js file agreement: 69% → 90%** — "don't pad peripheral files" removed the
  inconsistent localization / OpenAPI / error-json inclusions.
- **python `qtlog.py`: 12% → 100%** — runs converged on consistently taking the
  fully-relevant file whole; the previously least-stable file is now stable.
- **ts stayed excellent** — counts `8/8/8`, line-IoU ~100%.

Still weak / noisy:

- **Test-coverage extent** on js `test/user/emails.js` (29%), `test/database/
  keys.js` (40%), `user/delete.js` (21%): runs disagree on *how much* of a test
  to include. Genuine ambiguity, not a format problem.
- **Sampling noise (n=3):** some per-file IoUs swing between rounds for reasons
  unrelated to the prompt (python `log.py` 45 → 100 → 45; go `evaluator.go`
  91 → 100 → 57). Fine-grained cross-round per-file comparisons are therefore
  not fully reliable; the *attributable* conclusions are the four fixes above.

## Generalization: Second Suite

_Run: [`s2-v3`](runs/s2-v3/), 2026-07-06 22:19–22:33 PDT. v3 prompt on four **different** repos: navidrome
(go), openlibrary (python), webclients (js), tutanota (the real `ts` repo)._

The point: does v3 generalize beyond the first sample? Yes. All 12 runs
valid/complete. File agreement: go 78%, js 85%, python 100%, ts 100% (the sub-100
cases are a few test files not in every run). Line ranges are mostly **100%** —
navidrome and webclients are almost entirely consistent; openlibrary is very
tight (3/3/3 snippets). Cost $5.32.

The only low-IoU cases are the *same inherent ambiguities* seen in s1, not new
problems:

- **Whole-large-file vs focus boundary.** ts `BlobAccessTokenFacade.ts`: two
  runs took the whole 141-line file, one focused on `[58-109]` (IoU 36%). "A
  small fully-relevant file may be taken whole" is ambiguous at the boundary —
  141 lines is neither clearly small nor clearly large.
- **Test-coverage extent.** ts `BlobAccessTokenFacadeTest.ts` (55%), go
  `spotify/client_test.go` (81%).

**Takeaway:** v3 generalizes well (stable file selection, tight ranges, all
valid across 8 instances / 8 repos). The residual variance is inherent judgment
ambiguity, so it is not worth another prompt round — a hard size threshold might
marginally help the whole-vs-focus boundary but risks new boundary effects.
Aggregation is the better lever for these cases (below).

## Aggregate Experiment

_Post-processing of the s1-v3 runs (2026-07-06 PDT); no new sampling._

Question: is sample-and-aggregate worth building? As a cheap lower bound, a
**majority-consensus** aggregate over the 3 v3 runs ([`aggregate.py`](aggregate.py)): keep a file
if ≥ 2 runs chose it; keep a line if ≥ 2 runs covered it, then re-form ranges.
This deterministically drops single-run outliers and resolves disagreements to
the majority — no LLM needed.

What it produced, versus individual runs:

- **Removes single-run outliers.** js `user/delete.js`: run2's over-broad
  `[86-156]` collapses to the consensus `[140-155]`; the single-run peripheral
  file `views/admin/manage/users.tpl` is dropped. python `log.py`: run1's extra
  whole-block lines drop to `[362-371, 404-419]`.
- **Resolves coverage-extent disagreement to the majority.** js
  `test/user/emails.js` → `[111-138]`, `test/database/keys.js` → `[8-37]`.
- **No harm where runs already agree.** ts is essentially unchanged (its runs
  were already ~100% consistent).

**Mechanical majority — assessment.** Even this simple aggregate is clearly
*more reasonable and stable* than a single run on the residual-variance cases.
But it is conservative: a genuinely-relevant region only one run found is dropped
(a recall cost), and it cannot improve on what the runs collectively found.

**LLM aggregator** ([`aggregate_llm.py`](aggregate_llm.py), 2026-07-06 22:29–22:39 PDT; data [s1](runs/s1-v3-agg-llm-v0/) · [s2](runs/s2-v3-agg-llm-v0/)). The real version: feed the
3 run annotations + task context + the checkout to an aggregator agent that
synthesizes one reconciled annotation (union the correct, drop over-broad /
peripheral, pick tight consistent ranges), self-validated. All 4 s1 instances
succeeded; ~$0.35 and ~90s each. Versus mechanical majority it makes *judgment
calls* rather than intersecting:

- **Rescues relevant single-run regions that majority-vote drops** (recall win):
  python `qtnetworkdownloads.py [31-36]`, js `mongo/main.js [14-36]` and
  `users.tpl` — kept because judged relevant, whereas mechanical dropped them.
- **Still drops over-broad outliers:** js `user/delete.js [86-156] → [141-155]`.
- **Tightens to unit boundaries:** go `errors.go [1-55]` (drops the trailing
  empty line), `evaluator.go` starts at the function signature (42 vs 43).
- **No harm where already stable** (ts unchanged).

So the LLM reconciler gives aggregation's stability *without* the mechanical
majority's recall loss — the better of the two aggregation forms.

Run on the s2 suite too, it resolved the inherent-ambiguity cases directly. For
ts `BlobAccessTokenFacade.ts` the three runs split 2× whole-file `[1-141]` vs 1×
focused `[58-109]`; the aggregator **overrode the majority** and chose the
focused `[58-86, 88-109]` — rejecting the whole-file over-inclusion — and settled
the test-coverage range to `[91-150]`. This is the strongest evidence: the
reconciler makes the judgment call that no single-run prompt could stabilize.

**Verdict.** Sample-and-aggregate works and, with an LLM reconciler, produces a
clean single answer that is at least as good as the best of N runs. Cost is ~4×
single-run per instance (3 samples + 1 aggregate ≈ $1.7 vs $0.45). Recommended
as the quality/reliability path for the ambiguous minority — not required for a
first pass, where v3 single-run is already good and ~4× cheaper.

### Aggregator Prompt Iteration

The aggregator has its own prompt ([`tasks/related_files/aggregator.py`](../../../src/swe_lab/tasks/related_files/aggregator.py)),
iterated separately from the annotation prompt. Outputs are preserved per
version — v0 ([s1](runs/s1-v3-agg-llm-v0/), [s2](runs/s2-v3-agg-llm-v0/)),
v1 ([s1](runs/s1-v3-agg-llm-v1/), [s2](runs/s2-v3-agg-llm-v1/)), finalized
([s1](runs/s1-v3-agg-llm/), [s2](runs/s2-v3-agg-llm/)).

- **v0 — "tightest".** The first version told the aggregator to choose "the
  tightest contiguous range … from signature to closing line". It produced clean
  results and happened to *focus* the ambiguous ts `BlobAccessTokenFacade.ts`
  (candidates split 2× whole-file vs 1× focused → v0 picked focused). But
  "tightest" is too absolute — sometimes a little more context is better.
- **v1 — "most appropriate" + judgment.** Softened to the *most appropriate*
  range (not mechanically tightest) and to decide by judgment against the task
  ground truth rather than by vote. On the same candidates it mostly matched v0,
  with a few benign broadenings (including a full set of related test suites for
  js `emails.js` — arguably better context). **Problem it surfaced:** on the
  whole-vs-focus call it under-trimmed — `BlobAccessTokenFacade.ts` came back as
  the whole 141-line file where v0 had focused it. "Most appropriate" relaxed the
  trimming too much on that decision.
- **v2 — size threshold + disagreement rule (rejected).** Added "take a file
  whole only up to ~50 lines" and "when candidates split whole vs focused, prefer
  focused". This re-focused `BlobAccessTokenFacade.ts`, but it was **overfitting
  to one example out of eight**: a hard line-count threshold would mishandle a
  genuinely large, fully-relevant file, and the disagreement rule targeted a rare
  pattern. Discarded.
- **Finalized — principle-based (chosen).** Keep v1's "most appropriate" +
  judgment, and decide file scope purely by *task-relevance*: "take a file whole
  only when the whole file is genuinely relevant; otherwise select the relevant
  unit(s)". **No line-count threshold, no disagreement rule.** All 8 aggregates
  were valid; `BlobAccessTokenFacade.ts` came out whole — the agent's honest
  judgment that the whole facade is relevant here, left unforced.

**Why this version.** With only eight examples, a prompt tuned to nail a single
case (v2) is less trustworthy than a general, principled one. The finalized
prompt honors the "most appropriate, not always tightest" point and leaves the
whole-vs-focus judgment to the agent rather than hard-coding an answer — the
right call until a larger, more diverse sample says otherwise.

## Post-Experiment: Batch-QA Coverage Refinement

_2026-07-07 PDT._ After finalizing, the pipeline was run on 20 random instances
([`../batch_annotation/`](../batch_annotation/), seed `20260706`) with
per-instance manual QA ([qa_log.md](../batch_annotation/qa_log.md)). All 20 were
valid; the annotator reliably covered the **existing** gold-patch code files +
tests, and correctly excluded files the patch *creates* (new files), docs, and
dependency manifests (`go.mod` / `go.sum`). The one recurring gap: existing
UI-template files a fix edits were sometimes omitted (1 of 20).

Refinement (both prompts): a coverage line — include every file the gold patch
modifies that **already exists** in the checkout (a UI template or config it
edits counts); never include files it only *creates*. A 2-example spot-check
confirmed it: the gap case (openlibrary safe-mode) now covers all 4 gold files
incl. the two templates, and a clean/focused case (teleport) was unchanged (no
over-inclusion).

## Cost

| round | runs | cost |
| --- | --- | --- |
| baseline (s1) | 12 | $5.64 |
| v2 (s1) | 12 | $5.12 |
| v3 (s1) | 12 | $5.34 |
| v3 LLM-aggregate (s1) | 4 | $1.42 |
| s2-v3 | 12 | $5.32 |
| s2-v3 LLM-aggregate | 4 | $1.34 |
| **total** | **56** | **$24.18** (~$0.43/run) |

Tokens per annotation round ≈ 7–8M input (mostly prompt-cache reads) / ≈ 90–110K
output.

## Remaining Issues and Open Questions

- **Inherently ambiguous coverage.** Test files especially have no single
  "right" extent; this is the main residual variance and the strongest argument
  for sample-and-aggregate.
- **Small sample.** `n = 3`, one instance per language; file-agreement swings on
  js may be partly noise. A larger, more diverse sample (different repos per
  language) would firm up conclusions.
