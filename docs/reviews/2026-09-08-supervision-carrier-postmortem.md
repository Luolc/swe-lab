# The supervised carrier we kept was the one we ruled out first

**Date:** 2026-09-08 · **Kind:** postmortem (a dated snapshot, not a spec and
not a decision)

Three carriers were built for one supervision stack in eight days, and on
2026-09-08 two of them were deleted — 15,791 deleted lines in
[#449](https://github.com/Luolc/swe-lab/pull/449) and 6,259 in
[#450](https://github.com/Luolc/swe-lab/pull/450)
(`gh pr view --json deletions`). The survivor, the segment loop, is a mechanism
this project examined on its **first** day of supervision work and recorded as
dead.

This document reconstructs how that happened. It is not an argument that the
deletions were wrong or that anybody should have known better; the decisions
that removed the other two are settled in
[ADR-0025](../decisions/ADR-0025-the-segment-loop-is-the-only-supervised-carrier.md)
and [ADR-0026](../decisions/ADR-0026-the-correction-channel-is-removed.md) and
are not re-litigated here.

## How to read this

Three kinds of sentence, kept apart on purpose:

- **Plain text is record.** Every factual sentence points at a commit, a PR, an
  ADR, an experiment report or a plan. Where the citation is a document, it is
  that document's own words being reported.
- **Blockquotes marked *Inference* are mine**, derived from the records cited
  immediately above them. Nothing in one is evidence.
- **Sentences marked *Relayed* come from outside the repository** — a
  coordination message, not a record anybody else can open. This is the weakest
  tier here and it is labelled rather than laundered into the first: a reader
  who wants to check one has nowhere to go, and should know that before
  believing it.

A third form appears where it matters most: **"the record does not say."** Those
are findings, not gaps in this write-up.

**Every absence claim below carries the search that produced it** — the runnable
command, the revision it ran at, **each stage's exit status read on its own**,
and a control arm that returns a non-empty result — because a search that finds
nothing and a search that looks in the wrong place produce the same output, and
a search whose first stage failed produces that output too. Each also states
what its terms cannot cover.

**One boundary is deliberately not crossed.** Where a record says what somebody
did, that is taken as first-hand. Where the same record explains *why the system
behaves as it does*, that is its author's inference and is marked as such. This
document makes no claim about anybody's motive at the time.

**Clock.** Dates and times are the repository's own calendar — the author-local
one the ADRs and reports date by, UTC−7. `gh` reports UTC and runs seven hours
ahead, so a PR that `gh` timestamps `2026-09-03T01:22Z` is dated 2026-09-02
here.

## 1. The timeline

| When | What | Where it is recorded |
|---|---|---|
| 09-01 13:47 | `FEASIBILITY-A` lands: stop → inject → `--resume` is measured and ruled dead on one artifact | [FEASIBILITY-A](../../experiments/trace_synthesis/process_supervision/FEASIBILITY-A.md), `df6779d` (#306) |
| 09-01 14:02 | `streamjson_input` lands: turn-boundary injection clean over 21 runs, **and `--max-turns` segmentation measured clean** (N=2 runs / 4 segments) | [REPORT §3, §13.3](../../experiments/trace_synthesis/streamjson_input/REPORT.md), `a35e960` (#304) |
| 09-01 14:17 | Debate verdict: **"A′ now"** — spend the next engineering on stdin injection | [DEBATE-VERDICT §1](../../experiments/trace_synthesis/process_supervision/DEBATE-VERDICT.md), `6d997c3` (#311) |
| 09-01 16:06 | A′'s pre-registered compliance gate returns `BELOW_BAR` | [mid_turn_compliance REPORT](../../experiments/trace_synthesis/mid_turn_compliance/REPORT.md), `f27dce8` (#317) |
| 09-01 16:52 | ADR-0013 is accepted anyway, on an owner ruling, and says so in its own Status | [ADR-0013](../decisions/ADR-0013-supervision-on-the-stdin-channel.md), `3022873` (#320) |
| 09-01 21:43 → 09-02 23:41 | The correction channel is built — six commits: #341, #344, #349, #353, #348, #379 | `git log -- src/swe_lab/trace_synthesis/channel.py` |
| 09-02 02:34 | The first end-to-end supervised run is written up. §7a: 1118.9 s of supervisor span over 170 boundaries, ≤ 84.9 % of the run's wall clock, with the actor finished and waiting for at most 955.1 s | [pipeline REPORT §7a](../../experiments/trace_synthesis/pipeline_end_to_end/REPORT.md), `62f6bb5` |
| 09-02 18:22 | Issue [#375](https://github.com/Luolc/swe-lab/issues/375) opens, proposing a native Rust runtime and citing §7a | the issue body |
| 09-03 02:19 → 04:38 | The native carrier lands: #385, #389, #397, #387, #400, #402 | `gh pr view --json mergedAt` |
| 09-03 08:53 | [#412](https://github.com/Luolc/swe-lab/pull/412) reports the resume-loop feasibility spike; the owner relaxes criterion (b) | the PR body and its README / REPORT |
| 09-03 11:29 | The segment loop lands: [#413](https://github.com/Luolc/swe-lab/pull/413), +3,168 / −30 | `gh pr view --json additions,deletions` |
| 09-04 → 09-06 | Every subsequent supervision feature lands on the segment loop: #431, #434, #435, #438, #443, #445 | `git log -- src/swe_lab/trace_synthesis/segmented_loop.py` |
| 09-05 21:56 | #440 mirrors #438's `self_correcting` removal into the Rust verdict contract | `8f138e4` |
| 09-08 | The owner rules that one carrier is enough; #449 and #450 remove the other two | [ADR-0025](../decisions/ADR-0025-the-segment-loop-is-the-only-supervised-carrier.md), [ADR-0026](../decisions/ADR-0026-the-correction-channel-is-removed.md) |

Two corrections to how this episode is usually retold, both readable off that
table:

- **"The stream-JSON / Rust carrier" is two carriers, not one.** Different
  origins (a debate verdict; issue #375), different records (ADR-0013; **no ADR
  at all** — ADR-0025 says so in its Status section), and dates two days apart.
- **The Rust carrier did not precede the segment loop by any meaningful
  interval.** Both were built on 2026-09-03, about seven hours apart, in that
  order. The gap that is real is between A′ (09-01) and the segment loop
  (09-03): **two days.**

## 2. What the day-one records said about stop-and-resume

On 2026-09-01 the delivery question was framed as A vs B, and **approach A, as
originally specified, *was* stop-and-resume**: the premise sheet describes it as
"hook stops the session, supervisor injects, `--resume`" and records it as
**dead**, replaced by A′ — "No stop, no resume"
([debate-premises](../../experiments/trace_synthesis/process_supervision/debate-premises.md)).

What killed it was one artifact, and the record is unusually precise about
which. `FEASIBILITY-A` re-scored its own three artifacts against the owner's
(a)/(b) criteria and narrowed the verdict to a single row — the synthetic
**assistant** turn `"No response requested."` violates (a), "and the verdict
rests on it." The other two, a synthetic *user* turn and a `<system-reminder>`,
it explicitly clears. The conclusion reads:

> "plan A is disqualified because a mid-run stop→resume **necessarily** writes a
> synthetic *assistant* turn into the trace"

That sentence was then hardened into
[`spec.md` §6](../trace-synthesis/spec.md#what-disqualifies-a-trace--the-two-criteria-of-record),
where the same artifact is "the single artifact that disqualifies the
stop-and-resume path."

**The two stop mechanisms actually exercised were a `PostToolUse` hook returning
`{"continue": false}` (FEASIBILITY-A §1) and `SIGKILL` mid-tool-call
(`streamjson_input`'s positive control).** Neither report exercised a
`--max-turns` stop followed by a resume, and the word *necessarily* carries no N
and no enumeration of the stop mechanisms it quantifies over.

Two days later [#412](https://github.com/Luolc/swe-lab/pull/412) measured that
plain `--resume` is dirty "byte-for-byte the one `streamjson_input` measured for
SIGKILL+resume" — so the generalization held for the flag it was made about —
**and that `--resume-session-at` removes both records.**

> **Inference.** The day-one verdict was not wrong about what it measured. It
> generalized from two stop mechanisms to a path, and the counterexample turned
> out to be a different CLI flag rather than a different stop — which a
> measurement of stop mechanisms would not have found however many arms it ran.

### The other thing day one measured, and then set aside

The same `streamjson_input` report has a §13 whose subject is exactly the
mechanism that survives. §13.3 measures `--max-turns` segmentation — one session
id throughout, none of the three artifacts, wire `<system-reminder>` count equal
to control — and §13.4's **Recommendation** reads:

> "`--max-turns` is the only measured way to get a seam earlier than task
> completion **without** putting words in the user's mouth … that is still far
> finer than task completion and is, on this evidence, the seam to build on."

It was withdrawn the same day, in the premise sheet's `## RESOLVED` section, on
a stated ground: mid-turn injection had been measured byte-identical to a real
TUI interjection, so "Granularity is free," and therefore "**The whole
`--max-turns` / `control_request` apparatus is unnecessary.**" The debate judge
noted the leftover in the sheet and ruled that RESOLVED outranks it
(DEBATE-VERDICT, *Withdrawn-premise check*).

Note what the withdrawal is about. It retires `--max-turns` as a way of buying
**granularity**. Granularity is not why the segment loop exists today:
[ADR-0026](../decisions/ADR-0026-the-correction-channel-is-removed.md) records
its advantage as being that "it does not need the actor's stdin, because it
starts a new actor invocation for each segment," and the owner's stated reason
for keeping it is Occam.

> **Inference.** RESOLVED closed the axis it was about, correctly. The
> segmentation apparatus was scored on granularity, found redundant on
> granularity, and retired — and the property it would later be kept for, that
> the actor is stopped so nothing has to be plumbed into a live process, was not
> an axis anybody was scoring on 2026-09-01.

## 3. Not thought of, rejected, or infeasible? The record can tell

The answer is none of the three as usually meant.

- **Not "nobody thought of it."** Stop-and-resume was *approach A*, the
  incumbent, with its own feasibility report
  ([#306](https://github.com/Luolc/swe-lab/pull/306)).
- **Not "rejected as expensive."** FEASIBILITY-A §5 measured resume at **0 extra
  API requests**, a fully hit prompt cache (`cache_read` 35,453) and 224 extra
  cache-creation tokens, "indistinguishable from ordinary in-session
  boundaries."
- **Not "infeasible."** Every mechanical property the loop needs was measured
  clean on 2026-09-01 (§13.3).

It was **rejected on trace admissibility, by a criterion applied to one variant
of the mechanism** — and the criterion was the right one to apply: (a), no SFT
loss on tokens the actor did not generate, is a red line the project still
holds.

Its return on 2026-09-03 has two separate causes in the record, and merging them
would misstate both:

1. **The owner relaxed criterion (b)** — "this is SFT data generation with rich
   post-processing available, so a trace need not match an interactive user's
   shape" (#412's body, recorded there with who and when). #412 states plainly
   that **no measurement changed or was softened**.
2. **`--resume-session-at` answered (a)**, producing 0 synthetic assistant
   records (task 22 §6, measured).

**Criterion (a) was never relaxed.**
[Task 22](../trace-synthesis/plans/task-22-segmented-supervision-loop.md) row 10
records the branch's own correction: bringing the loop up on plain `--resume`
and accepting the dirty seam was ruled *wrong*, because the artifact violates
(a) and `spec.md` §6 forbids removing it afterwards — "the path is blocked both
ways." The default is `--resume-session-at`, and any segment written by plain
resume is marked `training_eligible: false`.

## 4. What was not in hand when A′ was chosen

ADR-0013 is dated 2026-09-01. The first end-to-end supervised run's report
landed the next morning, and its §7a is the measurement that bears hardest on
the choice of carrier:

> "All three interventions say the same thing … because they were judged at
> cursors 4, 8 and 12, and by the time each one crossed the channel the actor
> had moved on. **This is not a judge error rate; it is structural.** A judgment
> costs a model call, and the actor does not wait during it."

With numbers: 1118.9 s over 170 boundaries, 6.58 s per boundary, at most 955.1 s
of it with the actor already finished, at most 84.9 % of the rollout's wall
clock.

What that finding produced is on the record. Issue #375, opened about sixteen
hours later, cites §7a directly and proposes the native Rust runtime as "a clean
place to fix supervision lag: the native wrapper can continuously drain the
actor's output while one model judgment is pending and can **discard stale
decisions** before writing them to actor stdin."

**The argument that produced the other carrier is also on the record, and it is
not §7a.** #412's README states why the spike was commissioned:

> "The owner has proposed an alternative to hold **beside** A′ … The argument for
> it is complexity: every hard problem in the A′ implementation (concurrency
> barrier, `setsid` descendant freeze, folded-event accounting, judge
> cancellation, read-gate blind window) exists **only** to let actor and judge
> run concurrently — and that design was then serialized with `sigstop` anyway.
> A segmented loop is serial by construction and those problems do not arise."

> **Inference.** These are two readings of one underlying property — a judgment
> takes wall-clock time the actor does not spend waiting. #375 answers it by
> reducing the harm (drain continuously, discard what went stale); the
> segmented loop answers it by removing the concurrency. **The record does not
> say that anyone compared the two answers**; #412's report never cites §7a, and
> the two were commissioned on the same local day.

## 5. The shape of the stall

The stall is not in the building. It is in the deletion, and it is visible in
one command — `git log` per carrier:

| Carrier | Commits |
|---|---|
| `channel.py` (A′) | 6, from 2026-09-01 21:43 to 09-02 23:41. Then nothing of its own: its last two touches before deletion are `9a04eb5` (#397, wiring the *native* wrapper) and `31195bd` (#443, a change to the shared judge prompt). |
| `rust/` (native) | 5 on 2026-09-03, then one on 09-05 — `8f138e4` (#440), mirroring #438's `self_correcting` removal into the Rust verdict contract — then deletion. |
| `segmented_loop.py` | 14, continuous from 2026-09-03 to 09-08. Every supervision feature after 09-03 landed here. |

From 2026-09-04 onward the segment loop was, in the commit record, the only
carrier receiving work. The other two received exactly two commits between them
in five days, and both were **forced**: a sweep across the shared stack, and a
hand-mirrored contract change. #440 is the clearest single artifact of what the
duplication cost — a Rust edit made only because a Python edit happened, on a
carrier that had never run. ADR-0025 records why that mirroring could not be
automated away: the divergence between the Rust and Python policies "was
deliberate ([#380](https://github.com/Luolc/swe-lab/issues/380),
[#381](https://github.com/Luolc/swe-lab/issues/381),
[#383](https://github.com/Luolc/swe-lab/issues/383)), so the two could not be
checked against each other either."

The 2026-09-08 ruling did not change which carrier the work was going to. It
made the tree agree with something that had been true in the commit record for
five days.

## 6. The signals

Three, separated because they could have changed different things.

### The one that could have changed the day-one verdict: `--help` cannot see a hidden flag

The flag that removes the (a)-violating artifact, `--resume-session-at`, is
`hideHelp()` — it does not appear in `claude --help`. #412's Q0 establishes this
with a discriminating probe, and its README states the general defect in the
repo's own vocabulary, about the sibling flag `--max-turns`:

> "The brief that commissioned this experiment stated as verified fact that
> **`--max-turns` does not exist**. That was **wrong**, and the way it was wrong
> is the repo's own recurring defect: it was established with
> `claude --help | grep`, and *"hidden from `--help`"* and *"does not exist"*
> produce **identical output** under that observation."

Two things about that quotation are worth holding together. First, it is the
[undiscriminating-observation](../evidence.md) family — the thing this repo has
a whole document about — landing on flag discovery. Second, the brief it
corrects was written on 2026-09-03, **two days after `streamjson_input` §13.3
had measured `--max-turns` segmentation in this same repository**. The false
"does not exist" survived alongside a committed measurement of the flag working.

> **Inference.** The day-one verdict rested on exactly one artifact, and a flag
> that removes that artifact was invisible to the method by which one looks for
> flags. Whether a discriminating probe run on 2026-09-01 would have found
> `--resume-session-at` is **not something the record can answer**: #412 probed
> Claude Code **2.1.259**, the day-one work ran against **2.1.257**, and nothing
> in the record says whether the flag existed in the earlier build.

### The one that could have changed what was built on 09-03: §7a's word "structural"

§7a was read as a performance finding and answered with a faster carrier. Read
as a statement about carrier *shape*, it says that any carrier speaking to a
live actor pays staleness, and that the class of carrier which does not is the
one that stops the actor — which is the argument #412's README attributes to
the owner, in different words, on the same day.

Two things made it easy to file as performance. The number is a ratio (6.58 s
per boundary), and ratios read as tuning targets. And it sits in §7 of an
experiment report whose own preamble says "nothing here may close, weaken or
strengthen one of the seven points" — by the report's own design, its findings
are outside the pre-registered decision surface.

### The one that could have changed when the tree was cleaned: commit density

One `git log` per carrier, available continuously from 2026-09-04. No models, no
runs, nothing paid. The two dead carriers were removed on 2026-09-08, and both
ADRs attribute that to the owner's ruling of that date — so five days separate
the observation being available from the tree being cleaned. **What anybody read
in between is not something this document can establish**, and it is not claimed
here.

> **Inference, and the part worth carrying forward.** This repo has strong
> forcing functions for *evidence* — pre-registration, N-and-design labelling,
> the invariant-needs-a-test rule, the two-arm discipline in
> [`evidence.md`](../evidence.md). It has none that asks **"is anything still
> being built on this?"** A carrier nobody commits to for five days emits no
> red. The status home
> ([`plans/README.md`](../trace-synthesis/plans/README.md)) tracks *tasks*, and
> a task that is done stops emitting anything at all — which is exactly the
> state a superseded carrier is in.

## 7. What the record does not contain

Three findings about what is missing, each carrying the search that establishes
it rather than asserting it.

- **The only side-by-side comparison of the three carriers was written in order
  to remove two of them.** ADR-0025's Context table gives each carrier a row —
  how the actor is reached, and its record. The search behind that sentence,
  at `9f39348` — the commit before the first removal — is the intersection of
  three `git grep -l` runs over the tracked Markdown, each exiting on its own:

  ```sh
  rev=9f39348; paths=("docs/*.md" "docs/**/*.md" "experiments/**/*.md")
  git grep -lEi 'correction[ _-]channel|CorrectionChannel'            $rev -- "${paths[@]}" > a; echo $?   # 0, 19 files
  git grep -lEi 'native[ _-](supervis|runtime)|swe-lab-supervisor'    $rev -- "${paths[@]}" > b; echo $?   # 0, 11 files
  git grep -lEi 'segment(ed)?[ _-](loop|supervision)'                 $rev -- "${paths[@]}" > c; echo $?   # 0, 12 files
  comm -12 a b | comm -12 - c
  ```

  `git grep -l` exits 1 when nothing matches, so each `echo $?` above tells a
  real empty result from a broken invocation. The intersection is **two files**,
  and neither compares the carriers:
  [`plans/README.md`](../trace-synthesis/plans/README.md), a task index, and
  [task 22](../trace-synthesis/plans/task-22-segmented-supervision-loop.md),
  whose §9 lists the other two under *"Not touched"*. The same three commands at
  `origin/main` intersect to **seven**, five of which are the removal PRs' own
  output (ADR-0024, ADR-0025, ADR-0026, `releases/v0.3.7.md`, task 21) plus the
  same two. **What this cannot cover:** it is token-based, so a document that
  compared the three without any of those spellings would not appear in it.
- **The surviving carrier has no design decision record of its own.** Task 22 §7
  is titled *"No ADR, and why not"*, and records that one was written and
  "dropped unwritten to `main` on the owner's 2026-09-03 ruling", because "the
  acceptance for this task is a loop that runs." Its design record stayed a plan
  until ADR-0025 (09-08), whose Decision line makes it the carrier of record;
  earlier ADRs name it, but as one of two Python carriers rather than as the
  one (e.g. [ADR-0024](../decisions/ADR-0024-the-judge-is-not-told-what-the-supervisor-said.md),
  *"The Python carriers — the A′ channel and the segmented loop"*). That §7 exists is the
  good practice in this story: it keeps "no ADR" from looking like "nobody
  thought about an ADR" afterwards.
- **The surviving carrier's feasibility evidence is not in the repository.**
  #412 is still open (`gh pr view 412`) and its 26 files live under
  `experiments/trace_synthesis/resume_loop_feasibility/` on that branch. Task 22
  §9 names that path as "still untouched and still true"; it is not on `main`.
  Measured on the tree rather than the index, in two stages rather than a
  pipeline, because `grep -c` prints `0` and a pipeline's status is its last
  command's — so a failed `ls-tree` and a genuine absence would look identical:

  ```sh
  tree=$(mktemp)
  git ls-tree -r --name-only origin/main > "$tree"; echo $?                        # 0, 4890 paths
  grep -c '^experiments/trace_synthesis/resume_loop_feasibility/' "$tree"; echo $? # 0,  exit 1
  grep -c '^experiments/trace_synthesis/streamjson_input/'        "$tree"; echo $? # 41, exit 0
  ```

  `ls-tree` rather than `ls-files --with-tree`, which
  [`evidence.md`](../evidence.md) records as unioning the tree with the current
  index. And task 22's own
  acceptance — the bring-up run — is still ⬜ in the
  task index as of this date, so the only carrier in the tree has not yet
  completed the two acceptance points the owner set for it.

## 8. An adjacent case

Not a carrier decision. It is here because the carrier question is absent from
it, which is what makes it useful; whether it is *the same* failure is argued at
the end of this section and marked as inference, not asserted here.

[`docs/releases/v0.3.2.md`](../releases/v0.3.2.md) records that a provider-named supervisor was
deliberately deleted — "Nobody had chosen that: OpenRouter was convenient to
test against, and the convenience hardened into code." One release later,
[#445](https://github.com/Luolc/swe-lab/pull/445) reintroduced provider names in
a harder form: a registry closed at two entries, so that, per
[#448](https://github.com/Luolc/swe-lab/pull/448), "a downstream consumer with
their own base URL could not use this at all" — at the cost of "a dataclass, a
`functools.cache`d registry, a lookup function, an injectable probe and a custom
exception to express two strings." #448 deleted it two days later.

**What the record holds about how it got in.** #445's body ends
`Pair: swelab-orkey-impl / swelab-orkey-review`, so both roles are named there.
The review comment on #445 records `Verdict: LGTM` and, under `## Findings`,
`None.` — a full review pass over the change that #448 would undo two days
later, raising nothing. Both PRs are authored and merged under the one account
this repository's agents commit as, so GitHub cannot tell the roles apart.

> **Relayed** — the workspace coordinator, 2026-09-08, in a `herdr` coordination
> message to the author of this document. They state that beyond the pair, they
> read the change, endorsed it and merged it, and asked for that to be recorded
> without softening. Nothing in the repository distinguishes that from the
> single-account merge above, so it is carried here at the weakest tier this
> document has.

> **Inference.** The carriers and the registry fail the same check, and it is
> not a correctness check. Each was evidenced, tested and reviewed — #445's
> review found nothing, and #449/#450 removed code whose tests were green —
> and none of them was asked *does this need to exist?* This repo's gates are
> answerable: a test can go red, a claim can be checked, a number can carry its
> N. **"Could this not exist" has no red state**, so nothing surfaces it on a
> schedule; in both of these cases the answer arrived as an owner ruling
> (#448's body records the design as the owner's; ADR-0025 and ADR-0026 record
> theirs), which is a channel, not a gate.

## 9. What changed as a result

**One mechanical gate, and it guards the deletion rather than the decision.**
Both ADRs record that `no-stale-module-refs` gained the removed names, so
`native_supervision`, `supervisor_binary`, `transcript_marks`,
`correction_channel`, `CorrectionChannel`, `SupervisedRun`, `SupervisorPump` and
the rest cannot return to `src/`, `tests/` or `experiments/` Python without
failing the gate. ADR-0026 also records one invariant that gained a test it had
never had, checked by mutation
(`test_a_failure_the_policy_did_not_bound_is_a_gap_and_the_run_goes_on`).

**No rule was added about carrier proliferation, and none about reading a
five-day silence as a signal.** [`AGENTS.md`](../../AGENTS.md),
[`docs/conventions.md`](../conventions.md) and
[`docs/evidence.md`](../evidence.md) carry nothing on either as of this date.
Stated as a finding, not a proposal: this is a postmortem and proposes no rule.

## 10. What this document does not claim

- **Not that ADR-0013 was a mistake.** ADR-0026 supersedes it as a *retirement,
  not a refutation*, and nothing here changes that: the byte-identity result was
  never contradicted, and the paired-arm measurement ADR-0013 named as its own
  retirement condition was never run.
- **Not that the native runtime should not have been built.** Issue #375's
  first-named motivation is remote sandboxes without a bind-mounted workspace,
  which neither surviving mechanism addresses and which this document did not
  investigate.
- **Not a single cause.** Naming one would take an isolation this history cannot
  provide. Several things are true at once: a generalization from two stop
  mechanisms to a path; a granularity axis that closed correctly on its own
  terms; a flag-discovery method that cannot tell *hidden* from *absent*; a
  latency finding that arrived a day after the decision it bore on; and five
  days in which the cost of two dead carriers was paid by two commits nobody
  wanted to make.
