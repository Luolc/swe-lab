# Experiment playbook

This repo is an **ML / eval** project: a large part of the work is not "implement
a spec" but "**run an experiment to learn something**" — try a prompt, measure
variance, reproduce a failure, decide whether an approach is worth building. The
coding-lifecycle skills (spec → plan → build → review → ship) don't cover this
mode. This playbook does.

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
- **Report every rate with the count it excluded.** `resolved 12 / 40 (3 system
  failures excluded)`, never `12/40` — an unstated exclusion set is an invisible
  knob on the result. And **default to keeping a run in**: exclude only what is
  positively identified as your own breakage, so an unclassified ending can only
  *understate* the rate. The opposite default lets the excluded set grow
  unwatched, in the direction that flatters the result
  ([ADR-0015](../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md)).
- **A broken system and a hard task look identical in the data** unless you
  spend a word separating them. Both arrive as a zero. Give the infrastructure
  failure its own name at the point it happens, or it is counted as difficulty.
- **Negative and null results are results.** "v2 was a net wash," "v2 aggregator
  rejected," "the 3 fails were dataset defects, not our bug" are first-class
  outcomes. Record them; they save the next round.

## Pre-registration and blinding — what a running experiment may still change

Registered before the run, an experiment's rules are protocol; changed during
it, they are results in disguise. These are the distinctions that survived a
pilot's twelve amendments.

- **Separate the operational half from the analytical half, because they have
  different authors.** *Operational* rules fix what the machine does — how many
  executions happen, whether a failure is retried, what gets recorded. They are
  prospective, they remove discretion, and an author who has seen partial
  results may still fix them when the fix reduces the remaining degrees of
  freedom to zero. *Analytical* rules decide what may be concluded, and a
  non-blind author must not choose their free parameters. When an amendment
  touches both, split it: register the operational half, and **leave the
  analytical half explicitly open with a gate** — "no analysis may run until a
  party that has seen no outcome fixes this in writing" — rather than settling
  it once the numbers are in.
- **"My choice cannot exploit my knowledge" is an argument about a mechanism,
  not a property of a person.** It must be re-checked for every clause it is
  applied to, and it fails first on the clauses that decide what gets
  concluded. It is also not an argument its author may certify: **self-certified
  blindness is the human version of an assertion that cannot fail.**
- **Explaining an isolation rule can breach it.** Stating *why* someone is
  disqualified from ruling ("they have seen the outcomes, which are X") hands
  the disqualifying information to the next reader. Name the disqualification;
  do not reproduce what caused it. **And write the remedy over *channels*, not
  over the one place the leak was noticed** — "the values do not go in the
  document" protects a channel that never leaked, when the actual leak was an
  inter-agent message. Quantify: *no outcome value reaches a designated blind
  ruler by any route — document, PR description, agent message, spoken relay —
  until the rule they must fix is settled.* Its partner clause — anyone who has
  seen results recuses themselves from ruling — is **containment of a
  *disclosed* breach, not detection**: it takes effect only once a leak is
  known. Be exact about that, because "prevention + detection" is a claim of
  coverage the pair does not have. **The gap is that an undisclosed leak through
  an unlogged channel — an agent message, a spoken relay — is not mechanically
  detectable at all**; what surfaces one is the leaking party volunteering it,
  which is a norm rather than a mechanism. Name the gap instead of writing a
  third check over it: a check that cannot observe its subject is the
  cannot-fail shape from the hazards list. And **owning a mistake is not a
  mechanism either** — an acknowledgement is not enforceable, and treating it as
  the remedy is how the same breach recurs.

  A worked instance, because it caught two people who had spent the day naming
  this exact failure: the summary sentence "one prevents, one detects, and only
  both together are a mechanism" **was itself a coverage claim whose coverage
  had not been checked** — two clauses pointing different directions were
  declared jointly complete without anyone asking what would observe a breach.
  It is the same object as a green tautological assertion: **an announcement
  that a check was performed, whose effect is that nobody performs it.** A
  review, not the authors, found the missing half. Expect this shape in your own
  summaries first, where it is cheapest to write and hardest to see: **a summary
  is the natural habitat of a coverage claim**, because its whole genre is
  gathering several things into one complete-sounding statement, and
  *completeness* is the part nobody checks. **Being able to name a failure
  confers no immunity to it.**
- **A defect with a reason written beside it reads as a decision.** The same
  shape one level down: a filter, an exemption or a threshold that carries a
  comment explaining why it is there stops being read as a hole and starts
  being read as a judgement — and **the explanation is why the next reader does
  not look.** Three instances here in one day: a "safe to delete" argument whose
  ownership premise was never checked, a wall-clock estimate whose honest caveat
  supplied a sufficient-sounding reason for its being wrong, and a detector's
  length filter annotated "short values match everywhere; the rest are excluded
  by construction" — where the label values were short. The transferable detail
  is the **conjunction**: these rationales are half true. Spot-checking the
  first clause confirms it, and a reader almost never splits a sentence to check
  each half. **Verify the clause asserting that nothing else can be here, not
  the one you can already see is true.**
- **A reading carries a quantity, not just a value — say which.** A level, a
  delta, a count, a rate: the words and the units are often identical, so a
  sentence stays grammatical under the wrong reading and nothing downstream
  trips on it. That is why re-reading does not catch it and asking *which
  quantity is this?* does. No mechanical check exists, so this is a **review
  obligation** rather than an invariant: **when you restate a reading, label
  what quantity it is; a number arriving without that label should be refused,
  not discounted.** Two questions do the work, and the second catches a
  *correction* going wrong — the harder case, because a correction arrives
  wearing the authority of a fix:

  > **Restating someone's number — what did I add that was not in their
  > words?** Each restatement feels like summarizing, and a number gets *more*
  > useful with every misreading: more specific, better shaped as a conclusion.
  > That is why nobody stops.
  >
  > **Correcting someone's number — was the new reference point I just
  > introduced actually measured?** A correction can fix the stated error and
  > commit it again one layer in, by resting on a baseline nobody observed.

  Same cause, different surface: **change the question and the evidence has to
  be re-taken.** Readings gathered under question A are invalid by default for
  question B, most of all when both draw on the same material — exactly when
  nothing signals a re-read is needed. A relative of *measured on A, stated
  about B*: that family misaligns the **object**, this one the **question**.
  Worked instances of all of these are in
  [FEASIBILITY-B](../../experiments/trace_synthesis/process_supervision/FEASIBILITY-B.md).
- **Describing a check: write what each direction establishes, separately.** A
  check can be informative when it **fails** and empty when it **passes** — it
  rules out one necessary blocker while the thing it was invoked to support
  stays entirely open. Written as though both directions carried weight, its
  green becomes evidence for a design it cannot speak to. So: **if a positive
  result establishes nothing, say so in the sentence that introduces the check,
  and its passing may then never be cited as support anywhere.** Such a check is
  still worth running — being able to *kill* a design cheaply is valuable — but
  only ever as a falsifier.

  **When downgrading a document, magnitude and direction need separate passes.**
  Shrinking a claim's *size* — how much, how many, how faithful — leaves
  untouched the class of claim that says *what a check proves*, because nothing
  about it is numeric. One of these survived an entire round aimed explicitly at
  making claims smaller: the pass was over magnitude assertions, and no
  direction assertion was re-examined.

  With the two questions above, these are the three review obligations asked
  for **up to here** — none is machine-checkable, so none is written as an
  invariant; the entries below add their own:

  > **Restating a number:** what did I add that was not in their words?
  > **Correcting a number:** was the new reference point actually measured?
  > **Citing a check:** what does its *passing* establish?
- **A blind check's green is not an absence proof, and a better check does not
  make it one.** A verbatim n-gram overlap here returned zero and was reported
  as evidence that a document did not duplicate a registered rule. The
  duplication was **paraphrase**, which that check cannot represent at all, so
  its zero was uninformative by construction rather than by bad luck — the
  executable question (*if the problem existed, how would it appear in this
  check's output?*) has no answer for it. What was wrong was the **role, not the
  tool**: a detector's hits **rank what to read next**, and no detector's miss
  establishes that there is nothing to find. So build the sharper detector when
  it earns its keep as triage — but it stays evidence for inspection, never a
  substitute for the absence claim, and an improved one makes that substitution
  *more* tempting precisely because its blind spot is harder to characterize
  than the crude one it replaced. The three sites here were found by reading.
  Reading has its own blind spots and does not scale, which is why the finished
  state is not "a person read it" but **the assurance described as what it
  is** — here: one reading, at one revision, by someone who knew what to look
  for.
- **Put a statement about the parameter and a statement about the data in
  separate sentences.** Their modalities differ — a prior is an assumption, a
  count is an observation — and ordinary English hides the difference, because
  *"does not assume X"* parses correctly for both. So one sentence can assert
  something about the parameter while its author means the observation, and the
  prose gives no signal either way. A prior rationale here said the model "is
  **not** a claim that θ > 0" when what it declines to assume is `c > 0`: the
  prior does put θ > 0 with probability one, and at a rate that matters: for
  `Beta(a, b)` the inverse moment is finite exactly when `a > 1`, and the chosen
  `Beta(2, 2)` has density proportional to θ near zero. "The density vanishes at
  0" is **not** the condition — a density proportional to `1 / log(e/θ)` also
  vanishes there and still leaves `E[1/θ]` infinite. Meanwhile `c = 0` stays an
  ordinary observation. The remedy is structural rather than a matter of care — **put the
  assumption in one sentence and the observation in the next, and name the
  symbol in each.** A sentence carrying both is the one to split before checking
  anything else in it, and this is the cheapest place to catch it: **a merged
  sentence stays grammatical no matter which half is wrong**, so nothing later
  in the review will trip on it.
- **Finding one defect ends the search for the others.** Once a number has a
  named problem it reads as *examined*, and the examination stops there. A
  wall-clock estimate here was correctly found to be missing its machine-state
  predicate — and that was **not** the main reason it was wrong: it had
  extrapolated from a **different population** (frozen runs on another arm) than
  the one it predicted, and overshot by 2–4×. Then the correction itself
  overreached twice: first assigning the gap to the population difference, and
  then — when that was withdrawn — arguing that throttling could at least be
  **excluded by direction**, since steal only lengthens a run and cannot explain
  an estimate that came out too high. That second argument silently assumed the
  *baselines* were unthrottled, and their host state was never recorded; if they
  ran under equal or worse steal, their median is itself inflated and throttling
  is back in play. **An unrecorded predicate does not merely weaken the estimate
  that omitted it — it disqualifies every later argument that conditions on the
  quantity nobody recorded**, the arguments explaining the failure included. With
  eight attempts and no controlled comparison the gap cannot be apportioned at
  all. The structural fault is enough to invalidate the estimate; naming a cause
  trades a wrong number for a wrong explanation. **A number having a known problem is not
  evidence that it has only one**, and repairing the found one is what makes it
  look reviewed. When you fix a defect in an estimate, **re-derive the estimate
  rather than patching the defect** — and the reason that works is mechanical
  rather than a matter of diligence: a patch preserves the original derivation's
  *structure*, so every other wrong premise inside it is preserved too, with a
  qualifier bolted on outside. **Re-deriving forces you to restate each premise
  aloud**, which is the only step at which a question like "is the reference
  population the same population?" gets asked a second time.
- **"This is just a natural extension of the accepted X" transfers an accepted
  rule's standing to a proposal that has not earned it.** The move works because
  it points the reader at the old rule — whose standing is not in question — and
  away from whatever the new one adds. And it always adds something: **a rule
  worth proposing imposes some obligation its neighbour did not.** That
  obligation is what needs independent defence, and this argument form is
  precisely what stops anyone looking for it.

  The question it must trigger is therefore **"what does this require that the
  accepted rule did not?"** — deliberately *not* "is the new scope contained in
  the old one?" Containment is the wrong test, and an earlier draft of this
  bullet got it wrong in a way worth keeping: a proposal can sit **entirely
  inside** an accepted rule's scope and still be a real proposal, by making an
  implicit obligation explicit, by tightening it, or by making it enforceable
  where it was only advisory. So the criterion is the **added obligation**, not
  the added territory; the added obligation is non-empty whenever the proposal
  is not redundant, and it is the whole of what is being asked for. A new rule
  stands on its own obligation, cost and evidence, or it does not stand; an
  accepted neighbour is **precedent and motivation, never authority**.
- **The strongest form needs no blind party at all.** Protecting a ruler's
  blindness is a remedy for a *timing* defect: it is needed only because some
  decision was still live after results existed. So the target shape for a
  pre-registration is the one where the question never arises —

  > **Pin the analysis policy — the estimator, the censoring rule, what a unit
  > containing failures may conclude — before the first attempt runs. Once
  > execution begins, there should be no live decision whose ruler needs
  > protecting.**

  Say plainly when a run did not reach that shape and why. The honesty-scorer
  pilot did not: the defect that forced its analysis policy was only visible
  once the run was under way, which is a legitimate reason and not a failure —
  a rule cannot be pinned before the flaw in it is known. It was handled by
  isolating one person; **the next pre-registration's goal is to need no
  isolation at all.**
- **A stopping rule that reads the clock is outcome-correlated.** Truncating a
  fixed design on wall time removes whole units, and the slowest unit goes
  first — which is a property of the workload, not of chance. The same shape
  wears other costumes: retrying on "environment failure" is optional stopping
  whenever the failure is a function of how long a unit takes.
- **A wall-clock figure is workload × available compute, so it carries a
  predicate.** Record the machine state a timing estimate assumes (CPU steal,
  load, timestamp); an estimate without it silently asserts a condition that may
  not hold. For the same reason a fixed wall-clock timeout is **not** a fixed
  compute budget when steal varies — it is a variable disguised as a constant.
- **Never typeset an estimate and a hard constraint the same way.** Format
  carries credibility, and nothing checks format: side by side, a derived
  estimate and a registered constraint become indistinguishable in epistemic
  status, and the estimate starts being defended as a rule.
- **When a stratifying variable is perfectly aligned with the experimental
  unit, "we recorded it" is not a remedy** — diagnosability requires the
  variable to vary *within* some unit. And an **unmeasured** endpoint cannot
  serve as the control arm of a measured one: prefer **a weaker true statement
  to a stronger one that needs data you do not have** ("heterogeneity exists but
  is not quantifiable" beats "we have an internal control"). Never back-fill an
  estimated value into a missing measurement.
- **Disclosure is not a remedy for a degree of freedom.** This is the boundary
  of every "make it visible" fix above, and it constrains the remedies
  themselves: **for a measurement, visibility is a sufficient remedy — for a
  free parameter it is not, because a disclosed choice is still a choice made by
  someone who knew the results.** A parameter chosen post-outcome has to be
  *deleted*, not annotated. The test is whether the replacement rule has **zero**
  free parameters: re-tuning a cut to a safer value relocates the discretion,
  while removing the cut ends it.
- **Before citing a tool, criterion or dataset: does it exist *for the samples
  you have*?** Existence is not the question; **scoped** existence is. A
  criterion can be real, checked in, and referenced by name while covering none
  of the instances in front of you — and the name reads identically either way,
  so nothing in the sentence signals the gap. A measurement task whose
  instrument does not exist for its samples is not an expensive task; it is not
  a task. **Review obligation:** when a plan names an artifact, confirm its
  coverage of *this* population before any cost is incurred, not the artifact's
  existence in general.
- **When a check reports a violation, suspect the checker first.** The rest of
  this section is about green lights having several possible causes. This is the
  mirror: **a red light has several possible causes too, and the checker is
  usually the newer half** — freshly written for this purpose, while the thing
  under test has been exercised. A reported violation is therefore an
  **observation awaiting attribution**, not a finding. Two ways it goes wrong,
  both cheap to miss: a limit that silently truncates output produces a
  *directional* selection effect rather than noise, dropping exactly the cases
  that needed the most room; and an exact-match test against hard-wrapped or
  marked-up text fails on quotations that are perfectly faithful. **Review
  obligation:** a violation is not reportable until the checker has been cleared
  of producing it.
- **A substitution keeps the proposition only for the question you had in
  mind.** The costly half of a check gets swapped for a cheap stand-in — a
  `--help` for a real run, a dry run for a live one — and the swap is judged on
  the axis that motivated it, which is usually *cost*. What also changes,
  unwatched, is **which proposition the check still supports**: `--help` and a
  real invocation are equivalent on *does this command exist and parse its
  arguments* and not equivalent on *can it find its data at run time*, so a
  substitution made for cost can leave an acceptance step blind to the very
  defect it is meant to confirm. This is the same **scope** failure as *measured
  on A, stated about B*, one level up: two actions interchangeable on one
  question and not on another, with the question never written down. It is the
  worst of the family to catch because **nothing errors** — the check runs clean
  and returns a reassuring value. A poller reading a field its workflow never
  populates is the pure case, and it is not that the instrument broke — it is
  aimed at a place the answer never arrives: it reports *no result yet* forever,
  a value **indistinguishable from the true state it would report while
  waiting**, so watching it longer only deepens the conviction that you are
  waiting. The field answers *is there a formal record of type X*; the question
  asked was *has the decision been made*. **Review obligation:** when you substitute, name the proposition the original
  supported and show the substitute still supports it — a green from an
  unnamed proposition is not a result.
- **Verify a documented procedure by extracting and executing it, not by
  retyping it.** A `Reproduce` block, an install sequence, a runbook: these are
  instruments for someone else, and the author is the one person who cannot test
  them by hand. Retyping silently supplies what the reader lacks — the working
  directory, the environment, the order — so the author's implicit knowledge
  becomes an invisible input to the check. Extract the commands from the file
  and run them; a step that only works because you knew something fails here and
  passes when retyped. Expect the first failures to be in the *extractor* rather
  than the document — the previous entry's obligation arriving from the other
  side.
- **A failure you can remove structurally must not be written as a rule for
  people to follow.** A caveat — "run this from the repo root" — transfers
  responsibility for the failure to the reader instead of ending it, and every
  future reader has to be reached. Resolving the same path against the file that
  needs it deletes the failure mode outright. **Prefer the change that makes the
  mistake unavailable to the one that documents it**, and treat reaching for a
  caveat as the signal that a structural fix was available and skipped.

  Instances for all five: [PR #305](https://github.com/Luolc/swe-lab/pull/305).
- **A result of "we did not observe X" must say how large an X it can exclude —
  and a bound is itself a claim with premises.** Without any bound, a negative
  reads exactly like *X does not happen*, and those are not the same sentence.
  But the familiar repair carries a hidden model: with no occurrences in *n*
  tries the 95% upper bound is about **3/n** *only if the trials are independent
  and identically distributed at a stationary rate*. A design that leaves the
  serving path free — routing, model version, cache state recorded but not held
  fixed — **does not establish that**, and quoting the bound anyway swaps an
  unearned statistical claim for the honest one. Then the reportable result is
  the raw count, `observed 0 in 20`, with the bound quoted only as an explicit
  conditional if at all. **Still choose n before running, from what the quiet
  outcome would need to carry**, because afterwards it is the one nobody
  re-examines — but note that *more trials help more often* is also a claim
  about a process: when the varying thing is fixed for the duration of a run,
  extra calls within that run are extra observations and not extra chances. The same asymmetry decides what a one-directional check may be
  cited for: sharp when it fires, empty when it does not, said where it is
  defined, or its silence gets read as its verdict — and note that the firing
  direction usually needs **no** distributional assumption at all, since one
  counterexample is one counterexample.
- **A call whose result will be cited as evidence records what answered it and
  how.** Two things are absent by default and unrecoverable afterwards: the model
  id the **response** reports — never the alias in the request, since an alias
  re-pointed upstream leaves the request looking correct — and the sampling
  parameters actually sent, **including the ones that were not**, because an
  unset parameter is invisible unless absence is written down as absence.
  Without the first, *the same model disagreed with itself* and *the alias moved*
  cannot be separated. Without the second, disagreement between runs cannot be
  told from ordinary sampling, and a stochastic component gets written up as an
  instability discovery. **Adding one of them later does not bring the other
  back**, so the whole comparison is lost at the call site or nowhere.
- **A sentence saying you checked is itself a claim — and the most trusted kind,
  because it looks like diligence.** "Every link resolves", "each row carries
  its domain", "I searched for a sibling implementation": each asserts the
  *completeness of your own work*, and a reader cannot tell one that was earned
  from one that was assumed. So **either carry the trace that makes it
  checkable** — the search terms and the scope they ran over, the script, the
  enumerated list — **or do not write the sentence**. The damage is not that
  such a claim can be wrong; it is that **it discourages the one action that
  would find it wrong**, which is exactly what it shares with a sentence
  asserting that something does not exist
  ([task 37](../horizontal/plans/task-37-stale-mechanism-descriptions.md)). Both
  work by making the reader stop looking.

  **A URL is the sharpest instance**: it claims *this exists, and it is there*,
  and a fabricated one is the most credible bad value of all — not an empty
  result but one shaped exactly like a verified result, since an anchor guessed
  from a plausible pattern reads identically to one that was copied. **Never
  write a link you have not opened**; when you lack it, name the thing in words
  — "the review comment on PR #305" — rather than inventing an anchor.

  The instance a reader can check:
  [#312](https://github.com/Luolc/swe-lab/pull/312)'s own description promised
  that every measurement row carried its N and design; **three did not**
  ([review](https://github.com/Luolc/swe-lab/pull/312#issuecomment-5500618289))
  — in the section that exists to stop a narrow measurement being read as a
  general property. Two further occurrences the same day are what prompted the
  generalization, and **neither is recorded anywhere a reader can verify**, so
  they are not cited here: an entry about unverifiable self-report cannot rest
  on unverifiable self-report. What carries the rule instead is the linked
  instance and the mechanism it shares with the absence-claim defect.
- **Label an amendment for what it is.** "Post-hoc but prospective, with an
  empty prior action set" is a real and useful category — a rule changed after
  the run began but before it had ever fired changes nothing that already
  happened. Say so precisely, and confine the claim to the layer it covers; a
  document that asserts outcome-independence in one section and discloses known
  outcomes in another has written its own refutation.

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
| [trace_synthesis/handmade_instance](../../experiments/trace_synthesis/handmade_instance/) | Full experiment (README + REPORT) | [trace synthesis](../trace-synthesis/) |
| [trace_synthesis/injection_shape](../../experiments/trace_synthesis/injection_shape/) | Full experiment (README + REPORT) | [trace synthesis](../trace-synthesis/) |
| [trace_synthesis/instance_screening](../../experiments/trace_synthesis/instance_screening/) | Full experiment (README + REPORT) | [trace synthesis](../trace-synthesis/) / [W3](../workstreams/w3-quality-audit/) |

## Future: codify this as a skill

The installed `agent-skills` pack has no skill for empirical / experiment-driven
work — it's all coding-lifecycle. **We intend to author a local
`experiment-driven-development` skill** (a sibling under `.agents/skills/`) that
encodes this loop the way `test-driven-development` encodes RED→GREEN: hypothesis
→ logged run → empirical results → attributable conclusion → report, plus the
honest-reporting rules above. Until then, this playbook is the reference; follow
it by hand. Tracked in memory as `experiment-playbook`.
