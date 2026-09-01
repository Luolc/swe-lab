# Eval Issue: truncated golden test names cause false `GOLDEN_FAIL`

**Date:** 2026-07-16
**Instances under study:** the 3 `GOLDEN_FAIL` from the full-dataset golden
patch validation (run `29463094538`, 728/731 OK):

- `instance_NodeBB__NodeBB-00c70ce7…-vnan` (Mocha / JS)
- `instance_ansible__ansible-de5858f4…-v1055803c…` (pytest / Python)
- `instance_future-architect__vuls-bff6b755…-v1151a632…` (Go)

**Conclusion:** all three are **false negatives**. The golden patch is correct
and every required test actually **runs and passes**; grading fails only because
a few entries in the dataset's `fail_to_pass` don't *string-match* the name the
instance's own `parser.py` emits for the (passing) test. In every case the
dataset name is the parser's name **minus one trailing character** (a `"` or a
space). Correcting those entries makes all three grade `resolved = true`, with no
change to anything that runs in the container.

---

## 1. How grading decides `resolved`

`resolved ⇔ (fail_to_pass ∪ pass_to_pass) ⊆ passed`, where `passed` is the set of
test **names** that the instance's `parser.py` tags `PASSED` in `output.json`
(see `core/datasets/swebench_pro/grading.py`). It is **exact string set
membership** — a required name that isn't byte-identical to a passed name counts
as missing, even if that very test passed under a slightly different name string.

## 2. Method

`investigate.py` runs, **locally in Docker** (amd64 under emulation on this Mac),
the same two graded runs the validator does, per instance:

- **base** — no patch, golden tests restored → must *not* resolve;
- **golden** — the dataset patch, golden tests restored → must resolve.

It captures `output.json` + the entryscript + `run_script.sh` + `parser.py` +
grepped log excerpts under `results/<key>/`, then for the golden run computes
`missing = required − passed` and, for each missing name, the **unique** passed
name it is a strict prefix of (the "real" name). See `results/<key>/analysis.json`.

## 3. What we found

| instance | base resolved | golden resolved (as-is) | golden tests passed / required | missing (name mismatch) | ambiguous |
|---|---|---|---|---|---|
| vuls    | false ✓ | **false** (repro) | 7 / 7      | 3 | 0 |
| ansible | false ✓ | **false** (repro) | 58 / 58    | 1 | 0 |
| nodebb  | false ✓ | **false** (repro) | 681 / 681  | 4 | 0 |

Key point: **golden passed exactly as many tests as are required** (7/7, 58/58,
681/681) — nothing actually failed. Base correctly fails (the bug tests don't
pass without the fix). So each `GOLDEN_FAIL` is purely a handful of unmatchable
*names*.

Every missing required name is a strict prefix of a genuinely-`PASSED` name,
differing by **one trailing character**:

```
vuls    (Go, quoted params)   ..._"rhui-REGION-rhel-server-releases    →  + "
ansible (pytest dict param)   ...cache_content[{"de                    →  + "
nodebb  (Mocha titles)        ...ACP default "day                      →  + "
nodebb  (Mocha title)         ...big arrays (length > 100)             →  + (space)
```

Proof (verbatim from the captured golden `output.json`):

- vuls — `PASSED :: ..._"rhui-REGION-rhel-server-releases"` (closing `"` present)
- ansible — the parser itself emits mangled ids: `PASSED :: ...content[{"de"` and
  `PASSED :: ...content[{"version":`. The dataset matches 4/5 of these but stores
  the 5th as `...content[{"de` (one `"` short) → mismatch.

## 4. Root cause

The dataset's `fail_to_pass` entries are **not byte-identical to what the
instance's `parser.py` outputs** for tests whose names contain special characters
(`"`, spaces, `[](){}`, commas). Specifically the stored name drops a trailing
character. For ansible the parser *also* mangles the id (splits on whitespace),
so the two disagree by one `"` on one entry. Because grading is exact-match, the
test — though it passed — is scored missing → false `GOLDEN_FAIL`.

This is upstream **dataset data**, not our harness: base/golden run, the parser,
and the grader all behave correctly; the required-name strings are simply wrong.

## 5. The fix

Align each mismatched required name to the **exact string the instance's parser
emits** for that passing test (found as the unique passed name it prefixes). The
corrected rows are in [`fixed_rows.json`](fixed_rows.json) — full records with
**only** `fail_to_pass` changed, everything else untouched:

- nodebb: 4 entries (`+"` ×3, `+space` ×1)
- ansible: 1 entry (`+"`)
- vuls: 3 entries (`+"` ×3)

## 6. Verification

Two independent checks, both green:

1. **Re-grade** the *real captured* golden `output.json` against the corrected
   required set → `resolved = true` for all three (`regrade_resolved_with_fix` in
   each `analysis.json`).
2. **Re-run golden in Docker with the fixed spec** (`investigate.py verify all`)
   — identical container run, now grades `resolved = true`. (The fix is
   grading-side; the container output is unchanged, which is why re-grading is
   sufficient and the Docker re-run just confirms it end-to-end.)

## 7. Cross-check: Scale's *original* reference code fails the same way

To rule out our harness, we checked Scale's own
`swe_bench_pro_eval.py` (clone `3p/scaleapi/SWE-bench_Pro-os`, the pinned
commit). Its grading is line-for-line ours (`:555-558`):

```python
passed_tests = {x["name"] for x in output["tests"] if x["status"] == "PASSED"}
f2p = set(eval(raw_sample["fail_to_pass"]))
p2p = set(eval(raw_sample["pass_to_pass"]))
result = (f2p | p2p) <= passed_tests
```

Same `parser.py` (we fetch it verbatim), same dataset `fail_to_pass`, same
exact-set-membership. Two confirmations:

1. **Scale's grading lines on our real `output.json` + the real dataset row** →
   `result = False` for all three (vuls, ansible, nodebb).
2. **Full end-to-end run of `swe_bench_pro_eval.py --use_local_docker` for vuls**
   (same jefzda image) → `eval_results.json = {vuls: false}`, accuracy `0.0%`.

So the false `GOLDEN_FAIL` is inherent to the reference method + the dataset, not
our implementation. (ansible/nodebb weren't run through Scale's full pipeline, but
its grading is identical and uses the same parser + data, and confirmation #1
covers them.)

## 8. Recommendation

Correct these 3 rows' `fail_to_pass` in the dataset (content in
`fixed_rows.json`); that takes golden validation to **731/731**. The deeper,
optional follow-up is that test-name extraction (and ansible's `parser.py`) are
fragile for names with quotes/spaces/brackets — a canonical pytest-nodeid /
reporter-based extraction would prevent this class — but that's out of scope; the
3-row correction restores correct grading today.

## Reproduce

> **These commands no longer run.** Both scripts import `swe_lab.core.*`, a
> package dissolved on 2026-07-25 by #53; they raise `ModuleNotFoundError` on
> import. They are kept as evidence of the method behind the conclusion
> above, and are exempted by name from the `no-stale-module-refs` pre-commit
> hook — see the dated exemption list in `.pre-commit-config.yaml`. Reviving one
> means porting it to today's module layout, not deleting the guard entry.

```bash
# base + golden in Docker, capture + analyze + derive fix
python experiments/eval_issues/truncated_golden_test_names/investigate.py reproduce all
# emit the corrected rows
python experiments/eval_issues/truncated_golden_test_names/make_fixed_rows.py
# re-run golden in Docker with the fixed specs (expect resolved=true)
python experiments/eval_issues/truncated_golden_test_names/investigate.py verify all
```
