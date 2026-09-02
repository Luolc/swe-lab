# Running the supervision experiment at scale — a note for the downstream consumer

**Status:** handoff, and **not yet a proven pipeline.** This repo delivers the
**design and the seams** for an end-to-end supervised rollout — the components
task 01 depends on — not a demonstrated one: we have not run a real supervised
rollout ourselves. Current status, point by point, lives in one place and
moves as PRs land — check it before scheduling anything against this note:
[task 01](plans/README.md#task-01-one-instance-end-to-end).
Measuring how large the supervision effect is belongs to whoever has the
quota and sandboxes to run the full set (owner, 2026-09-01); this repo is
capped at **10 SWE-bench Pro instances × 2 rollouts** and will not produce an
effect estimate even once the pipeline is proven.

Its purpose is narrow: **we already paid for some of the numbers and some of the
design, and you should not buy them twice.** Everything below is either measured
here or a decision with its reason attached, so you can adopt or overturn it
knowingly.

## 1. What a rollout costs and takes — measured

Three rollouts, one dataset (SWE-bench Pro), actor `claude-sonnet-5` on Claude
Code `2.1.212`, `rollout_and_unit_test`:

| run | cost | agent wall | conditions |
| --- | --- | --- | --- |
| NodeBB-00c70ce7 | **$2.04** | 285 s | alone on the box |
| NodeBB-8168c6c4 r1 | **$2.77** | 423 s | concurrency 2 |
| NodeBB-8168c6c4 r0 | **$4.17** | 900 s | concurrency 2 |

**n = 3, on 2 instances of one repository — treat the spread, not the mean, as
the planning input:** $2.04–$4.17, mean $3.00. Grading adds no model cost (it
runs the instance's own tests) and took 67 s on a passing NodeBB run; whole
workflow wall was 454 s solo, of which ~100 s is container setup, image pull and
teardown.

**Cheap instances exist and are not represented here.** An `ansible` instance
finished its agent phase in 83 s with a 1.2 s grading run — likely well under
$1. Its cost is unrecoverable because the defect in §6.1 destroyed the record;
the number is missing rather than estimated on purpose.

**Both NodeBB numbers above are from the same instance**, so the 1.5× cost gap
between them is *within*-instance variance under concurrency, not a difference
between tasks. Budget for a distribution, not a unit price.

### Scale, at this cost range

| design | rollouts | cost range |
| --- | --- | --- |
| 10 × 2 (this repo's cap) | 20 | $41–83 |
| screening 20×2 + formal 10×3×2 | 100 | $204–417 |
| screening 30×3 + formal 16×3×2 | 186 | $379–776 |
| screening 40×3 + formal 24×3×2 | 264 | $538–1102 |

Wall clock is the binding constraint, not money: at 454 s per rollout, 264
rollouts is ~33 h serial.

**No concurrency ceiling is claimed.** Memory is not the limit — peak RSS was
522–700 MB per container against 10 GB free. CPU is, and the only evidence here
is that the same instance run twice concurrently took 900 s and 423 s of agent
wall. That spread is too wide, from too few samples, to name a ceiling. Measure
it as a by-product of your own sweep — start at 2, record wall and contention
per batch, and step back on degradation — rather than as a separate campaign.

## 2. Design decisions already made, with their reasons

Adopt or overturn deliberately; each of these cost an argument.

- **Success is the dataset's own rule.** `resolved` ⇔
  (`fail_to_pass` ∪ `pass_to_pass`) ⊆ `passed`. No per-instance predicates:
  a criterion written per fixture is a criterion that can be tuned per fixture.
- **The information barrier is structural, not behavioural.** The supervisor's
  input type simply has no field for the gold patch or the hidden tests, so
  leakage is not a thing it is asked to refrain from — it is a thing it cannot
  express. Do not re-implement a second barrier on top; consume the component's
  constructor interface.

  **A field that exists but goes unused is not a barrier** — it is one more
  thing you have to keep true, and nothing fails when it stops being true. The
  per-instance guidebook is therefore *deleted* from the supervisor's input
  rather than passed and ignored (owner ruling, 2026-09-01). If you extend the
  interface, extend it the same way: what must not leak must be **absent**, so
  that a leak is a type error rather than a discipline.
- **Paired arms, per instance.** The control arm is the *same code path* with
  the speaking budget set to zero — same judge calls, stdin held open the same
  way, and a "would have spoken" marker recorded. That makes the arms comparable
  **at matched deviation points**, not only at the endpoint, which is worth far
  more than the endpoint comparison alone.
- **Screening draws must not double as the unsupervised arm.** Selecting on a
  noisy measurement and then reusing those same draws biases the baseline toward
  the middle. Re-draw for the measured arm.
- **The denominator defaults to "in".** Only an ending positively identified as
  *your* breakage leaves it — the current, complete set is
  `RolloutOutcome`'s docstring
  ([`rollout.py`](../../src/swe_lab/rollout.py)), not reproduced here: a
  partial copy of this enum has already gone stale twice in this note. The one
  worth calling out by name for a **supervised** rollout at scale:
  `SUPERVISION_FAILED` (the supervisor died mid-run) leaves the denominator
  too — a run that was meant to be supervised and lost its supervisor partway
  through is not evidence about supervision either way, and pooling it with a
  genuine non-compliance would put our own breakage inside the comparison
  supervision is being judged by. An unclassified ending stays, so it can only
  understate a rate; the opposite default lets the excluded set grow
  unwatched, in the direction that flatters results.
  ([ADR-0015](../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md))
- **Today, a transient supervisor failure ends the whole rollout** as
  `SUPERVISION_FAILED` — deliberately, for the reason above. At full scale
  this turns ordinary upstream jitter into a visible exclusion rate that
  reads as **our pipeline being unstable**, not as jitter. See
  [`channel.py`](../../src/swe_lab/trace_synthesis/channel.py) for current
  behavior — under active revision, so checked there rather than described
  here.
- **Report every rate with its two counts** — how many runs were excluded as
  ours, and how many nobody could attribute:
  `resolved 12 / 40 (3 system failures excluded, 2 unclassified)`, never
  `12/40`. An unstated exclusion set is an invisible knob; the two counts are
  one value with one rendering
  ([`swe_lab/reporting.py`](../../src/swe_lab/reporting.py)`.Rate`,
  [ADR-0016](../decisions/ADR-0016-the-endings-nobody-could-attribute.md)).
- **Pin the actor and the harness version.** Rates, costs and headroom all move
  with them, and a batch that does not record them cannot be compared with the
  next. The rollout record carries `agent_model`; the harness version is in the
  run's `claude.info`.

### Supervisor model: same as the actor, and what that costs you

We chose the **same** model for supervisor and actor. The reason is
attribution, not budget: with a stronger supervisor, a positive result has two
readings — "supervision works" and "a stronger model's reasoning was smuggled
into this arm" — and nothing in the design separates them. With one model, the
arms differ only in whether supervision happened.

**The price, which belongs in your limitations section:** a same-model
supervisor **shares the actor's blind spots** and cannot flag deviations it
would make itself. That biases toward *under*-detecting the effect — the safe
direction — but it means a null result does not establish that supervision
fails. It may only establish that one model cannot see its own faults. Varying
supervisor strength is a legitimate second experiment; it is not this one.

## 3. Screening for headroom, if you do it

Instances that solve 0% or 100% of the time carry no signal, so a screen keeps
the middle. With `k` draws per instance and a keep rule of
`1 ≤ successes ≤ k−1`:

| true p | discarded at k=3 |
| --- | --- |
| 0.2 | 52% |
| 0.3 | 37% |
| 0.5 | 25% |
| 0.8 | 52% |

**The error direction is conservative**: the screen throws away instances that
did have signal; it does not manufacture signal. Pay for it with a candidate
pool 2–3× your target instance count. `k=4` cuts the p=0.5 loss to 12.5% for
+33% screening cost — we judged `k=3` the better buy, given screening is pure
cost and yields no measurement.

## 4. Choose the statistic **before** the runs, not after

A **sign test** per instance discards magnitude: 3–0 and 2–1 count the same, and
it needs at least 5 concordant discordant-pairs to reach one-sided p < 0.05
(2⁻⁵ = 0.031), realistically 6–8. A **paired permutation test on per-instance
differences in success count** uses the same data with more power.

Compute what each needs from your *observed* headroom distribution, then
**freeze the choice before spending on the formal arms.** Choosing after seeing
results is choosing the flattering one.

## 5. What "the pipeline works" means, and where to check it

**Not a claim that these are green here** — that status lives in one place,
moves as PRs land, and would go stale the moment it was copied into this note:
[task 01's acceptance table](plans/README.md#task-01-one-instance-end-to-end).
Check it there before trusting anything above rests on a working pipeline.

The seven points, reproduced only so you know what each one proves, not as a
record of having passed them: the supervisor is on the actor's **live**
stream; the **barrier holds** (the fields are absent, and a criterion-artifact
sha mismatch refuses the run); the policy speaks at least once **because of a
real deviation** and not on a schedule; the correction lands **mid-turn** in
the measured wire shape; the patch is taken **against the pre-agent baseline**
and grading runs; the trace is persisted **with the interjection still in it
after conversion**; and the **outcome word** is the right one.

Each of those names a test, a persisted field or a record — an acceptance
nobody can check is worth as little as a metric nobody reads. Two are worth
copying into your own harness regardless of when task 01 finishes: a run
whose only utterances are **scheduled** does not demonstrate a policy (a knob
cannot be evidence for what it sets), and an interjection that is delivered
but **lost in conversion** satisfies every delivery check while leaving no
evidence behind.

## 6. Hazards that will cost you data

### 6.1 Two rollouts of one instance could erase each other

Fixed here in #332 — check your version. The CLI's per-run scratch directory
was keyed by workflow and instance only — not by `rollout_id` — and a
non-resumed run wipes that directory. Running `--rollout-id 0` then
`--rollout-id 1` sequentially therefore left **only the second record**, while
the lost run's own output still reported `"succeeded": true` and the
`record_key` it had written.

Two details worth carrying:

- **`--persist` (the shared T1 store) was never affected**, because it does not
  live under the wiped directory. Only the default throwaway store was.
- **The wipe is correct behaviour for a re-run.** The error was applying it
  across *samples*: throwaway is right for a re-run and wrong for two samples,
  and `--rollout-id` is the declaration that two runs are two samples.

A run that reports a `record_key` now reads it back and fails when it cannot,
which covers the family rather than this cause: a wipe, a failed write, a
mis-joined key and a full disk all produce the same successful-looking summary
naming a key with nothing under it.

It was diagnosable only because one batch happened to contain **both**
orderings: the concurrent pair kept both records (both wipes landed before
either persisted), the sequential pair kept one. All-concurrent would have
hidden it; all-sequential would have looked like persistence never working. If
you run sequentially at scale on an unfixed version, you lose half your records
silently.

### 6.2 Patches are contaminated unless the pre-agent baseline is on

Some images ship files that are untracked at `base_commit` — one SWE-bench Pro
image carries a Redis append-only-file directory — and the extraction contract
stages untracked files. Against `base_commit` that yields a **166 KB patch from
a run where no agent executed at all**; against a pre-agent baseline, on the
same image in the same container, **0 bytes**. It is on by default here
([ADR-0014](../decisions/ADR-0014-the-pre-agent-baseline-is-the-default.md)) and
enforced by a test that runs no agent and requires an empty patch. Any measured
`resolved` rate from before that fix is suspect on affected instances, and how
many instances are affected is **not measured** — one image was examined.

Record `git status --porcelain` at the baseline step and the affected-instance
rate accumulates as a by-product of runs you are making anyway.

### 6.3 A broken system and a hard task look identical

Both arrive as a zero. Give infrastructure failure its own word at the point it
happens, or it is counted as task difficulty and depresses the measured rate of
whichever arm happened to break more. The complete, current set of endings and
which of them leave the denominator is `RolloutOutcome`'s docstring
([`rollout.py`](../../src/swe_lab/rollout.py)) — not reproduced here, per §2
above. The reasoning is in
[ADR-0015](../decisions/ADR-0015-four-words-for-how-a-rollout-ends.md) and
[ADR-0016](../decisions/ADR-0016-the-endings-nobody-could-attribute.md), which
adds `unclassified`: an ending nobody could attribute is a fact distinct from
the actor's own zero, and the two-count report above (§2) is what keeps it from
being silently absorbed into the rate. For a **supervised** rollout
specifically, `SUPERVISION_FAILED` is the one to watch: see §2 above — it is
ours, not the actor's, and must leave a supervision-effect denominator the
same way a system failure does.

Related, and cheap to get wrong in the other direction: an agent that runs
cleanly and produces nothing is a **real failure to solve** and stays in the
denominator. Excluding it raises the measured rate, and raises it most for the
weakest actor.

### 6.4 Two environment hazards you will hit at this scale, not measured here

Both are already recorded, so linked rather than restated:
[Hazards learned the hard way](../conventions.md#hazards-learned-the-hard-way).

- **Do not run in a git worktree.** `git worktree remove` deletes gitignored
  content silently — this is how task 01's own phase-A evidence was lost once,
  costing three rollouts to re-harvest. Put anything worth keeping on a stable
  path outside every checkout.
- **The dataset is a manual download, and its absence is a misleading
  symptom.** A missing parquet makes every `gold_unit_test` run exit 1 with a
  `FileNotFoundError`, which reads as "these instances are broken" rather than
  "the parquet was never downloaded here."
