# Experiment playbook

This repo is an **ML / eval** project: a large part of the work is not "implement
a spec" but "**run an experiment to learn something**" — try a prompt, measure
variance, reproduce a failure, decide whether an approach is worth building. The
coding-lifecycle skills (`/spec → /plan → /build → /review → /ship`) don't cover
this mode. This playbook does.

It is **descriptive first**: the conventions below are the ones the repo's best
experiments already follow. Two exemplars to imitate:

- **A full experiment** — [`experiments/related_files/prompt_variance/`](../../experiments/related_files/prompt_variance/)
  (`README.md` + [`REPORT.md`](../../experiments/related_files/prompt_variance/REPORT.md)).
- **An investigation** — [`experiments/eval_issues/truncated_golden_test_names/`](../../experiments/eval_issues/truncated_golden_test_names/README.md).

## When you're in experiment mode

Reach for this playbook (not `/spec`) when the deliverable is **knowledge**, not
a feature:

- "Does prompt v3 reduce run-to-run variance?"
- "Why do these 3 gold patches fail to grade?"
- "Is sample-and-aggregate worth building?"
- "What's the memory ceiling before the box swap-thrashes?"

The output of an experiment is a **REPORT with a recommendation**. That report
then *feeds* the lifecycle — it becomes the evidence behind a `/spec`, a
[decision/ADR](../decisions/), or a "not worth it, dropped" note. Experiment
→ decide → *then* build.

## The loop: hypothesis → evidence → conclusion

Every experiment moves through the same stages. Write them down in this order;
don't skip straight to numbers.

1. **Hypothesis / question.** State what you expect and *why*, precisely enough
   to be wrong. "v3 will hold file-selection stable while tightening line ranges"
   beats "try to improve the prompt." A falsifiable claim is the whole point.
2. **Design / method.** What you'll vary (the independent variable — prompt
   version, model, concurrency), what you'll measure (the metrics — file
   agreement, line-IoU, cost), and the controls (seed, instance set, everything
   held fixed). Decide the metrics *before* looking at results, so you can't
   rationalize post hoc. Pick a sample and say why it's representative (and its
   limits — `n=3` steers a prompt but won't support firm per-file claims).
3. **Run — logged and timestamped.** Execute; capture **raw artifacts**, not just
   summary numbers (see [Logging discipline](#logging-discipline)). Every run is
   reproducible from what you saved.
4. **Empirical results.** Report what the data *says*, plainly, before
   interpreting — the tables of numbers, the captured outputs. Keep this separate
   from your reading of them.
5. **Analysis.** Interpret. Crucially, **separate signal from noise**: which
   conclusions are *attributable* to the variable you changed, and which are
   sampling noise or inherent ambiguity? (The prompt-variance report is careful
   here: "the *attributable* conclusions are the four fixes above" — the rest
   swung between rounds for reasons unrelated to the prompt.)
6. **Conclusion & recommendation.** A clear, actionable verdict: adopt / drop /
   needs-more-data, and what to do next. Distinguish "**fixable**" from
   "**inherent**" — residual variance that no prompt round will remove is a
   different recommendation (aggregate over samples) than a bug (fix it).
7. **Open questions.** What this run *couldn't* settle (small sample, one box,
   confounds), so the next session knows the boundary of what's proven.

## Directory & file conventions

```
experiments/<workstream>/<experiment>/
  README.md            # the design: hypothesis, method, how to run, naming
  REPORT.md            # the findings: results → analysis → conclusion → cost → open Qs
  <driver>.py          # the runner + analysis scripts, checked in and re-runnable
  runs/<variant>/      # raw artifacts per variant (never overwritten)
    <case>.json        # full per-case output
    summary.jsonl      # one compact line per run (append-only)
```

- **`README` vs `REPORT`.** The `README` is the *design and how-to-run* (stable);
  the `REPORT` is the *findings* (grows as rounds land). Small investigations may
  fold both into one `README` (see `eval_issues/`) — but keep the same logical
  sections.
- **One directory per experiment**, named for what it studies (`prompt_variance`,
  `truncated_golden_test_names`). Group under the workstream it serves.
- **Analysis is code, not a one-off.** Check in the `analyze.py` / `aggregate.py`
  that turn raw runs into the report's numbers, so any claim can be regenerated:
  `python .../analyze.py <round>`. Reviewers re-run, not re-trust.
- **`experiments/` is exempt from the code-quality hooks** — these are
  exploratory scripts, not shipped code. Keep them readable, but don't gold-plate.

## Logging discipline

The rule: **a run you can't reproduce or audit didn't happen.** Capture enough
that a fresh session can re-derive every number and inspect any single run.

- **Timestamps.** Every round/report records when it ran (the prompt-variance
  report timestamps each round to the minute, with timezone). Scripts in this
  repo can't call `Date.now()` in the workflow layer, but experiment *runners*
  should stamp real wall-clock time into `summary.jsonl` / the report.
- **Provenance per run.** Model id (exact, e.g. `claude-sonnet-4-6`), prompt/
  config **version**, seed, instance ids, the git commit, and the **exact
  command**. The report's header table is the canonical place (author, harness,
  model-under-test, final-prompt links, started/updated).
- **Raw artifacts, preserved.** Save the full per-case output *and* an
  append-only `summary.jsonl`. Don't reduce to a mean and throw the runs away —
  the residual-variance analysis needs the individual runs.
- **Cost & tokens.** Track $ and input/output tokens per round; report a total
  and a per-run average. (Prompt-variance: "$24.18 / 56 runs (~$0.43/run)".)
- **Never overwrite a variant — add one.** Prompt versions live side by side:
  `runs/s1-baseline/`, `s1-v2/`, `s1-v3/`, `…-agg-llm-v0/`, `-v1/`. Comparisons
  need the old outputs intact. New idea → new variant dir.
- **Idempotent, resumable runners.** Skip cases whose output already exists so an
  interrupted or usage-limited round resumes cleanly (this is how the 731-instance
  batch survived repeated credit walls). Log what was skipped — never silently.
- **Seeds are recorded and reused.** Sampling draws record their seed
  (`Random(20260706)`) and how the draw was formed, so rounds are reconstructable
  and disjoint.

## From empirical to summary — the honest-reporting rules

The hardest part of an ML experiment is not running it, it's not fooling
yourself. Hold to these:

- **Ground every claim in raw data.** A conclusion points at the run(s) that
  support it. If you can't point, it's a hypothesis, not a finding.
- **Attributable vs. noise.** With small `n`, some movement is sampling noise.
  Say which conclusions survive that (the prompt-variance report only credits the
  changes it can attribute; it explicitly flags per-file IoUs that "swing between
  rounds for reasons unrelated to the prompt").
- **Inherent vs. fixable.** Some variance is genuine judgment ambiguity (how much
  of a test to include) that no amount of prompt tuning removes. Name it, and
  route it to the right lever (aggregation), rather than overfitting a prompt to
  it.
- **Beware overfitting to one example.** A rule tuned to nail a single case out
  of eight (the rejected aggregator v2 with its hard line-count threshold) is
  less trustworthy than a general, principled one. Prefer principles; distrust
  thresholds fit to one datapoint.
- **State the sample's limits.** "`n=3`, one instance per language" is enough to
  steer a prompt and too small for firm per-file claims — say so in the report.
- **Negative and null results are results.** "v2 was a net wash," "v2 aggregator
  rejected," "the 3 fails were dataset defects, not our bug" are first-class
  outcomes. Record them; they save the next round.

## Investigations (the lighter variant)

A failure investigation (why did *this* break?) is the same loop, compressed, and
adds a **reproduce → cross-check** spine. The `eval_issues/` write-ups are the
model. Structure:

1. **What & when** — the failing cases, the run that surfaced them, the date.
2. **Conclusion up front** — one paragraph: what it is (e.g. "all three are false
   negatives") and why.
3. **Method** — the exact repro (`investigate.py reproduce all`), what it
   captures, where.
4. **What we found → Root cause** — evidence, then the mechanism.
5. **The fix + verification** — and re-run to confirm (two independent checks,
   both green, beats one).
6. **Cross-check against the reference.** When you conclude "not our bug,"
   *prove* it against the reference implementation (the report ran Scale's own
   grader on the same data and got the same failure). This is what separates a
   diagnosis from a guess.
7. **A copy-pasteable `Reproduce` block** at the end.

## How an experiment plugs into the lifecycle

- **Before building:** an experiment validates (or kills) a hypothesis so `/spec`
  is grounded in evidence, not assumption. The prompt-variance report is *why*
  sample-and-aggregate became the production pipeline.
- **After deciding:** a settled outcome that shapes the architecture becomes a
  [decision/ADR](../decisions/); a REPORT that a workstream depends on gets linked
  from that [workstream doc](../workstreams/).
- **The report is the durable artifact.** Sessions end; `REPORT.md` is what the
  next one reads instead of re-running $24 of experiments.

## The experiments themselves

They live under [`../../experiments/`](../../experiments/) (exempt from the
code-quality hooks), grouped by workstream:

| Experiment | Kind | Serves |
| --- | --- | --- |
| [related_files/prompt_variance](../../experiments/related_files/prompt_variance/) | Full experiment (README + REPORT) | [W1](../workstreams/w1-related-files/) |
| [related_files/batch_annotation](../../experiments/related_files/batch_annotation/) | Batch run + QA log | [W1](../workstreams/w1-related-files/) |
| [eval_issues/truncated_golden_test_names](../../experiments/eval_issues/truncated_golden_test_names/) | Investigation | [W2](../workstreams/w2-solve-eval/) / [W3](../workstreams/w3-quality-audit/) |
| [eval_issues/shell_expansion_in_entryscript](../../experiments/eval_issues/shell_expansion_in_entryscript/) | Investigation | [W2](../workstreams/w2-solve-eval/) |

## Future: codify this as a skill

The installed `agent-skills` pack has no skill for empirical / experiment-driven
work — it's all coding-lifecycle. **We intend to author a local
`experiment-driven-development` skill** (a sibling under `.agents/skills/`) that
encodes this loop the way `test-driven-development` encodes RED→GREEN: hypothesis
→ logged run → empirical results → attributable conclusion → report, plus the
honest-reporting rules above. Until then, this playbook is the reference; follow
it by hand. Tracked in memory as `experiment-playbook`.
