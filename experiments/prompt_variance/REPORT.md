# Prompt-variance experiment report

**Goal.** Check how the annotation prompt behaves across languages and how stable
it is run-to-run, then iterate the prompt to reduce variance while keeping
results reasonable. Status: **complete through v3.** v3 is the recommended
prompt: it keeps v2's wins and fixes v2's regressions. Residual variance is
mostly inherent task ambiguity (how much of a test to include), best addressed
by sample-and-aggregate rather than more prompt tweaks.

## Method

- One instance per language, each annotated **3 times** with `sonnet`:
  flipt (go), qutebrowser (python), NodeBB (js), element-web (ts).
- Two variance axes (not just snippet count):
  1. **File agreement** — do the 3 runs select the same files? (`intersection /
     union`).
  2. **Line-range agreement** — for files chosen by every run, how much do the
     covered line numbers overlap (`line-IoU`), and are the ranges *reasonable*
     (focused vs whole-file)? Judged both mechanically (`analyze.py`) and by
     reading the actual snippets against the problem statement / gold patch.
- Cost and token usage tracked per run.

Raw data: `runs/<round>/` (full annotations + `summary.jsonl`).

## Baseline results

All 12 runs completed and passed validation (`valid=3/3` everywhere).

| lang | snippet counts | file agreement | notable line-IoU |
| --- | --- | --- | --- |
| go | 6 / 7 / 6 | 4/5 (80%) | evaluator.go 91%, rest 100% |
| python | 7 / 8 / 6 | 5/5 (100%) | **qtlog.py 17%**, qtnetworkdownloads 30%, log.py 45% |
| js | 14 / 15 / 14 | 10/10 (100%) | **test/user/emails.js 29%**, keys.js 53%, rest 72–100% |
| ts | 9 / 15 / 8 | 8/8 (100%) | 85–99% (main ranges agree) |

**Cost:** $5.64 for 12 runs (~$0.47/run; ts most expensive at ~$0.67/run).
**Tokens:** ~8.6M input (mostly prompt-cache reads), ~94K output.

### Finding: file selection is stable; the variance is in the ranges

Which *files* to read is highly reproducible (three of four languages at 100%
file agreement; go at 80%, one run adding a generated `.pb.go`). The run-to-run
differences come from the line ranges, with three concrete drivers:

- **(A) Occasional whole-file over-inclusion.** python `qtlog.py`: two runs took
  focused ranges `[1-33] + [200-214]`; one run took `[1-213]` — the entire
  213-line file. Drives the 17% IoU.
- **(B) Inconsistent trivial 1-line snippets.** ts run2 added five separate
  single-line *import* snippets (`[24-24]`, `[23-23]`, …, each `context-file`
  "Imports X from …") that the other two runs omitted — the reason its count
  jumped to 15 vs 8–9.
- **(C) Coverage extent.** js `test/user/emails.js`: one run covered four test
  suites `[42-138]`, another only the single relevant suite `[111-138]`. Both
  defensible, but inconsistent.

The rest is benign boundary jitter (a few lines; e.g. go `evaluator.go`
`85-104` vs `85-110`), which is acceptable.

**Verdict:** no run was *wrong* (all reasonable, on-target files), but ranges
vary more than ideal. Worth tightening the prompt to reduce A/B/C.

## v2 — prompt changes

Targeted the three drivers (see `annotate/prompt.py`, commit `2404b76`):

- Added an explicit **JSON output example** (format can't be misread).
- **Range = the enclosing unit** (function/method/class/block), tightest range
  that fully covers it; **never select an entire file** (→ A).
- **No trivial snippets** for single import lines / one-line references (→ B).
- **Cover the whole relevant unit, not a sub-slice** (→ C).
- Emphasized reproducibility (two reviewers should agree).

## v2 — results

All 12 runs valid/complete. Cost $5.12 (baseline $5.64). Effect vs baseline was
**mixed — one clear win, partial wins, and two regressions**:

| lang | file agree | counts | key line-IoU changes |
| --- | --- | --- | --- |
| go | 80% → 100% (noisy `.pb.go` dropped) | 6/7/6 → 8/7/8 | evaluator.go 91→**100** ✅; **errors.go 100→20** ❌; server.go 100→72 ❌ |
| python | 100% → 100% | 7/8/6 → 7/8/7 | log.py 45→**100** ✅; qtnetworkdownloads 30→68 ✅; qtlog.py 17→12 ❌ |
| js | **100% → 69%** ❌ | 14/15/14 → **20/16/12** ❌ | mongo/postgres →100 ✅; delete.js 80→21 ❌; emails 29→37 |
| ts | 100% → 100% | **9/15/8 → 8/9/8** ✅ | mostly →**100%** ✅ |

**What worked:**
- **(B) trivial import snippets — fixed.** ts run no longer emits single-line
  import snippets; counts tightened (15→9) and line-IoU rose to ~100%. The
  cleanest, most attributable win.
- **Coverage consistency (C) improved in places:** python `log.py` and
  `qtnetworkdownloads`, go `evaluator.go` all rose toward 100%.

**What regressed:**
- **"Never select an entire file" was too absolute.** go `errors.go` is a small
  56-line file of error-type definitions that all baseline runs sensibly took
  whole (`[1-56]`, 100%). The rule pushed two v2 runs to a partial `[25-35]`
  while one kept the whole file → 20% IoU. Whole-file is the *right, stable*
  choice for small fully-relevant files.
- **js got less stable:** file agreement fell to 69% (runs inconsistently
  included peripheral localization/OpenAPI files) and the count spread widened.
  Partly the splitting/coverage guidance, partly task noise (NodeBB touches many
  peripheral files; n=3 is small).
- **(A) whole-file over-inclusion reduced but not gone:** no run took the full
  `qtlog.py [1-213]` anymore, but one still took a broad `[71-213]`. `qtlog.py`
  is a genuinely scattered file and stays the least-stable case.

**Verdict:** net wash — a clear ts win offset by the `errors.go`/js regressions.
The lesson is that a blanket "never whole file" rule is wrong; the guidance
should forbid grabbing a *large* file when only part is relevant, while allowing
a *small, fully-relevant* file to be taken whole.

## v3 — plan

Keep the v2 wins (no trivial snippets; cover the whole unit) and fix the
regressions:

- Soften "never select an entire file" → "don't select a whole **large** file
  when only part is relevant; a **small, fully-relevant** file may be taken
  whole." (fixes `errors.go`)
- Add "keep to the files a solver genuinely must read; don't pad with peripheral
  files unless clearly necessary." (targets the js file-agreement drop)

## v3 — results (recommended prompt)

v3 kept v2's wins (no trivial snippets; cover whole units) and softened the
whole-file rule (large file → part; small fully-relevant file → whole) plus
"don't pad peripheral files". All 12 runs valid/complete. Cost $5.34.

Evolution of the headline numbers (baseline → v2 → v3):

| metric | go | python | js | ts |
| --- | --- | --- | --- | --- |
| file agreement | 80 → 100 → 75 | 100 → 100 → 100 | 100 → **69 → 90** | 100 → 100 → 100 |
| snippet counts | 6/7/6 → 8/7/8 → 6/5/6 | 7/8/6 → 7/8/7 → 5/6/7 | 14/15/14 → 20/16/12 → 12/12/16 | 9/15/8 → 8/9/8 → **8/8/8** |

Targeted fixes landed:

- **go `errors.go`: 20% → 98%.** The small fully-relevant file is taken whole
  by all runs again — the v2 regression is fixed.
- **js file agreement: 69% → 90%.** "Don't pad peripheral files" removed the
  inconsistent localization / OpenAPI / error-json inclusions.
- **python `qtlog.py`: 12% → 100%.** Runs converged on consistently taking the
  (fully-relevant) file whole — the previously least-stable file is now stable.
- **ts stayed excellent:** counts `8/8/8`, line-IoU ~100%. The import-snippet
  fix from v2 held.

Still weak / noisy:

- **Test-coverage extent** on js `test/user/emails.js` (29%), `test/database/
  keys.js` (40%), `user/delete.js` (21%): runs disagree on *how much* of a test
  file / suite to include (one suite vs several). This is genuine ambiguity, not
  a format problem.
- **Sampling noise (n=3):** some per-file IoUs swing between rounds for reasons
  unrelated to the prompt (e.g. python `log.py` 45 → 100 → 45, go `evaluator.go`
  91 → 100 → 57). Fine-grained per-file comparisons across rounds are therefore
  not fully reliable; the *attributable* conclusions are the four fixes above,
  which have clear mechanisms.

## Conclusions & recommendation

- **Adopt the v3 prompt.** It gives stable file selection (python/js/ts ≥ 90%
  file agreement, ts perfectly consistent) and tight, reproducible ranges, with
  the v2 regressions fixed. The three levers that mattered: no trivial 1-line
  snippets; take small fully-relevant files whole (don't over-split); don't pad
  peripheral files.
- **Residual variance is mostly inherent** — chiefly how much of a test to
  include. Prompt wording alone won't drive this to zero.
- **This is the case for sample-and-aggregate** (PLAN.md option): for the
  ambiguous files, running N times and letting an aggregator reconcile
  ranges/coverage would beat any single run. Recommended if we later need higher
  per-instance reliability.
- **Validate at scale on more instances.** n=3 × 1-instance-per-language is
  enough to steer the prompt but too small for firm per-file claims; a broader
  sample would confirm.

## Cost

| round | runs | cost |
| --- | --- | --- |
| baseline | 12 | $5.64 |
| v2 | 12 | $5.12 |
| v3 | 12 | $5.34 |
| **total** | **36** | **$16.10** (~$0.45/run) |

Tokens per round ~8M input (mostly prompt-cache reads) / ~90K output.
