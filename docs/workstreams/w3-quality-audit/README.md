# Workstream 3 — Quality auditing / skew

> 📚 **Archive (2026-07) — an intent, never a plan; never started.** No code was
> ever written for this workstream. The nearest shipped work is
> **benchmark-integrity detection**, which landed *horizontally* as
> [`src/swe_lab/integrity/`](../../../src/swe_lab/integrity/)
> ([ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md)) — not under
> `pipelines/`, as the "planned directions" below guessed. **Live status lives
> elsewhere** — the workstream row in [`docs/README.md`](../../README.md). Read
> this for the framing, not for instructions.

Flag eval instances that no longer measure real capability — "skewed" examples:
ambiguous specs vs. overly-specific tests, broken environments, contamination,
brittle graders — in the spirit of OpenAI's
[*Separating signal from noise in coding evaluations*](https://openai.com/index/separating-signal-from-noise-coding-evaluations/).

## First tool — gold self-test sweep

A **gold self-test sweep** (grade every instance's own gold patch; any that does
*not* resolve is a broken/skewed instance) drops straight out of the
[W2](../w2-solve-eval/README.md) eval pipeline. It has already been run once across the
full dataset and surfaced exactly the expected class of issue: 3 instances that
looked like `GOLDEN_FAIL` turned out to be **upstream dataset defects** (test
names truncated by one character), not real capability signal — write-up in
[`experiments/eval_issues/truncated_golden_test_names/`](../../../experiments/eval_issues/truncated_golden_test_names/README.md).

That investigation is the template for this workstream: reproduce → diagnose →
cross-check against the reference implementation → classify (harness bug vs.
dataset defect vs. genuine) → record.

## Planned directions (not started)

- Turn the gold-sweep + eval-issue investigations into a **standing audit** that
  classifies every instance (healthy / dataset-defect / environment-broken /
  brittle-grader / contaminated).
- Where such an audit would live is **open** — the guess recorded here was
  `src/swe_lab/pipelines/`, and the integrity work that arrived first went
  horizontal instead.

## Related experiments

- [`experiments/eval_issues/`](../../../experiments/eval_issues/) — per-issue
  investigations (`truncated_golden_test_names/`,
  `shell_expansion_in_entryscript/`). Each is a reproduce → diagnose → fix
  write-up; see the [experiment playbook](../../experiments/playbook.md) for the
  investigation-write-up convention.
