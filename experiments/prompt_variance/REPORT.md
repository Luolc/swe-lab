# Prompt-variance experiment report

**Goal.** Check how the annotation prompt behaves across languages and how stable
it is run-to-run, then iterate the prompt to reduce variance while keeping
results reasonable. Status: **baseline analyzed; v2 (first prompt revision) in
progress.**

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

_TODO: fill in after the v2 round completes — compare snippet counts, file
agreement, and line-IoU against baseline; note whether A/B/C shrank and whether
any regressions appeared; update cost/token totals._

## Remaining issues / open questions

_TODO after v2._
