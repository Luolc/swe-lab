# Pre-registration: does 6.58 s per boundary transfer?

> **Frozen before any run.** Everything below is protocol, fixed at the commit
> that adds this file. **Nothing has been run.** No rollout for either instance
> named in §2 exists at the time of writing, and this document changes nothing
> that executes — it adds one directory containing one Markdown file.

The first end-to-end supervised rollout measured **6.58 s per boundary** and
left the question of whether that number belongs to the pipeline or to that one
instance explicitly open
([`REPORT.md` §8](../pipeline_end_to_end/REPORT.md#8-open-questions), first
bullet). This probe is two more instances, two rollouts each, asked at that
question and nothing else.

Everything the first run established — the coordinates, the seven acceptance
points, the mechanism of the catch-up tail, the two-assertion evidence rule —
is linked here, never restated. Where this document says "run 1" it means the
run recorded in
[`pipeline_end_to_end/REPORT.md`](../pipeline_end_to_end/REPORT.md), whose
corpus is `~/corpora/swe-lab/first-e2e-2026-09-02/r0/`
([`WITNESS.md`](../pipeline_end_to_end/WITNESS.md)).

## 0. The two questions, in priority order

**Primary — is the throughput measurement transferable?** Run 1's supervisor
spent 1118.9 s on 170 boundaries. §7a of that report turns the figure into a
headroom claim (`_AGENT_TIMEOUT_S / 6.58` ≈ 547 boundaries), and every
consequence downstream of it scales with a number measured once. The primary
question is whether the per-boundary figure on two instances nobody tuned for
lands anywhere near run 1's — and, because each of them is run twice, whether
any gap is larger than the gap between two runs of the same instance.

**Secondary — does the pipeline run unattended on an instance it was not set up
for?** Run 1's instance was chosen, pre-flighted, and had its image proven. The
secondary question is whether a supervised rollout reaches a terminal state
without a human touching it on an instance that got none of that attention.
This is robustness, not effect.

**A limitation of the design, stated here rather than in the report that would
otherwise discover it.** The quantity being measured is dominated by the
latency of a model call over a network, which is not obviously stable from one
run to the next. Two rollouts per instance give a **crude bound** on that
run-to-run scatter — the interval between the draws — and nothing more: two or
three observations do not estimate a variance, and the interval between a few
draws systematically understates the spread of the distribution they came from. So
the design's improvement over one rollout per instance is real and small: a
**disagreement** between instances stops being *uninterpretable by
construction* and becomes *weakly bounded* — it can be compared against a
scatter that was actually observed rather than against nothing. It does not
become an attribution. §5 pins that comparison before any number exists, and
neither of its verdicts is licensed to name a cause.

**The asymmetry among the three instances is not cosmetic.** Run 1 has **one**
rollout and will not get another under this probe, so it contributes a *point*
where the two probe instances contribute *intervals*. Every within-instance
statement below therefore rests on two instances, not three, and run 1's point
enters the between-instance comparison as though it had no scatter — which it
certainly has, and which nothing here measures. **The consequence does not run
one direction, and §5 works it out with a counterexample**: the missing
replication makes `W` an underestimate, which loosens one of V2's two
conditions — but run 1 also enters `B` as an unreplicated point, where its
effect has no fixed sign, and a valid data shape exists in which the missing
replication *suppresses* a separation rather than creating one. So the direction
is claimed for `W` alone and the combined verdict is left undirected. The
cheapest single
improvement available to a follow-up is a second rollout of run 1's instance;
this probe does not buy it, because that would make three instances and §1
freezes two.

## 1. Scale — the hard cap

**Frozen: 2 instances × 2 rollouts = 4 rollouts.**

`AGENTS.md`'s ask-first boundary is **more than** 10 SWE-bench Pro instances
**or more than** 2 rollouts per instance. Two rollouts on each of two instances
sits exactly on that line and inside it, so this probe needs no additional
permission — and has none in reserve: **every instance in it is at its ceiling
from the start.**

**What that does to a re-run, which is the part the extra budget changes.**
With the planned count at the ceiling, a re-run is not additive: a replacement
is a **third invocation** on that instance, which is across `AGENTS.md`'s
ask-first line, not up against it. This document takes the strict reading —
**an invocation counts whether or not it produced a record.** The lenient
reading ("a rollout that made no model call never happened") is available and
is deliberately not taken, since adopting it would be indistinguishable from
wanting one more run.

**So there are two branches and no third.**

- **Default — the reduced design.** No replacement is run; how many values that
  instance ends up contributing follows §5.0, and is not assumed to be one — a
  qualifying failed invocation still contributes. This needs nobody's permission and is what happens
  unless the exception below fires. Its consequences for V1 and V2 are
  pre-registered in §5 rather than worked out afterwards.
- **Exception — a replacement, and only on the user's explicit instruction.**
  The user gives it directly. **No agent may give it, relay it as authority, or
  answer for it** — not the orchestrating session, not the implementing one.
  What crosses the ask-first line is the user's to decide, and a message
  between agents carries collaboration context, not authorization. If the
  instruction arrives, the report records **that it was given**, alongside the
  replacement's own corpus.

Asking is allowed; assuming is not. A replacement that was never requested and
one that was requested and declined are different facts, and the report says
which happened.

A third instance, a fifth planned rollout, or a control arm is the same kind of
decision and sits on the same side of that line: **the user's.** A new
pre-registration is what such a run would additionally need — it is not what
would authorize it, and this one may not be amended to allow any of them.

## 2. The instances, and the rule that picked them

**The rule, executable by someone who has seen no result.** Over
[`experiments/trace_synthesis/instance_screening/candidates.json`](../instance_screening/candidates.json)
— the screened form of [issue #261](https://github.com/Luolc/swe-lab/issues/261)'s
40 candidates, committed before this probe existed — in ascending
`rank_in_issue_261` order, take the first two entries satisfying all of:

- `verdict == "good"` (not `good_with_caveat`, not `uncertain`, not `bad`)
- `confidence == "high"`
- `image_runnable == "proven"`
- `instance_id` is not run 1's
- `repo` differs from every already-taken entry's `repo`

Run verbatim:

```sh
python3 - <<'PY'
import json
RUN1 = ('instance_internetarchive__openlibrary-5de7de19211e71b29b2f2ba3b1dff2fe065d660f'
        '-v08d8e8889ec945ab821fb156c04c7d2e2810debb')
rows = json.load(open('experiments/trace_synthesis/instance_screening/candidates.json'))
picked, repos = [], set()
for x in sorted(rows, key=lambda x: x['rank_in_issue_261']):
  if (x['verdict'], x['confidence'], x['image_runnable']) != ('good', 'high', 'proven'):
    continue
  if x['instance_id'] == RUN1 or x['repo'] in repos:
    continue
  picked.append(x); repos.add(x['repo'])
  if len(picked) == 2:
    break
for x in picked:
  print(x['rank_in_issue_261'], x['repo'], x['language'], x['instance_id'])
PY
```

Its output, on this branch:

```
2 navidrome/navidrome go instance_navidrome__navidrome-5001518260732e36d9a42fb8d4c054b28afab310
9 qutebrowser/qutebrowser python instance_qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d
```

**The two instances, written down so the rule and its result cannot drift
apart:**

| | instance_id | repo | language |
|---|---|---|---|
| **P1** | `instance_navidrome__navidrome-5001518260732e36d9a42fb8d4c054b28afab310` | navidrome/navidrome | go |
| **P2** | `instance_qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d` | qutebrowser/qutebrowser | python |

Both are present in the pinned 731-record parquet, checked by loading it and
matching the ids exactly (`n = 731`), with image references
`jefzda/sweap-images:navidrome.navidrome-navidrome__navidrome-5001518260732e36d9a42fb8d4c054b28afab310`
and `jefzda/sweap-images:qutebrowser.qutebrowser-qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac780366`
respectively, derived the way
[`RUNBOOK.md` §1](../pipeline_end_to_end/RUNBOOK.md#1-pre-flight--re-check-every-item-at-launch-time-never-trust-a-prior-pass)
derives one. Whether either image is **present on the box** is a launch-time
check, delegated to that same checklist and deliberately not asserted here.

### 2a. Which copy of the data that check read

**A rule is only reproducible together with the data it ran against**, and this
repository has a live way for two people to run one rule over two different
files without noticing. `find_repo_root` prefers the `PROJECT_ROOT` environment
variable over walking up to `pyproject.toml`
(`src/swe_lab/paths.py:32-35`), `.envrc` exports it, and the SWE-bench Pro
parquet is gitignored — so `git worktree add` never brings it along, and a shell
that inherited `PROJECT_ROOT` from the main checkout reads the main checkout's
data from inside any worktree. That is not hypothetical: it produced a wrong
"not reproducible" verdict against two agents on 2026-09-02
([issue #366, entry 3](https://github.com/Luolc/swe-lab/issues/366), which
carries the three-row comparison).

The state the checks above were actually taken in, printed rather than assumed:

| | |
|---|---|
| Checked from | the worktree `/home/ubuntu/dev/swe-lab-probe`, branch `exp/probe-preregistration` |
| `PROJECT_ROOT` | **unset** |
| `find_repo_root()` | `/home/ubuntu/dev/swe-lab-probe` |
| `datasets_root()` | `/home/ubuntu/dev/swe-lab-probe/datasets` — **no parquet under it**; `datasets/swebench_pro/data/` does not exist |
| Parquet actually read | `/home/ubuntu/dev/swe-lab/datasets/swebench_pro/data/test-00000-of-00001.parquet`, 7,816,820 bytes, sha256 `c8cd7115496ad4e9a8b21d088cef576a65bf821bb542b24336f13f714cef13f8` |
| How it was reached | `load_dataset('swebench_pro', root='/home/ubuntu/dev/swe-lab/datasets')` — an argument on one command, nothing exported, nothing copied into the worktree, no `.envrc` touched |

The default resolution **failed loudly** here rather than reading the other
copy silently — `FileNotFoundError` naming the missing worktree path — which is
the good half of this hazard and is why the explicit `root=` above is a
deliberate override and not a repair of a silent mismatch. Anyone re-running
§2's checks must print those first two rows before believing the third.

**What this does and does not touch in the rule.** The selection rule of §2
reads `candidates.json`, which is **committed in-repo**: it resolves through
`git`, not through `datasets_root()`, so it selects the same two entries from
either checkout, and the extracted-and-executed block above ran entirely inside
this worktree. What needed the parquet is the *verification* — that both ids
exist in the pinned 731 and what their image references are. A different
parquet could therefore falsify §2's existence claim without changing which two
rows the rule picks, which is why the digest is recorded here rather than left
as "the pinned dataset".

**Why each clause is in the rule, and what it costs.** `verdict`/`confidence`
keep a determinacy-broken task from turning the robustness reading into a
statement about the dataset. `image_runnable == "proven"` removes a failure
mode — the image will not build or run — that is a known property of the
dataset rather than anything about supervision; **the cost is that this probe
says nothing about instances whose image has never run**, and that exclusion is
part of the result, not a footnote to it. Distinct repositories keep the two
probe points from being two samples of one repository's shape.

**On whether the rule was reverse-engineered.** It was written against the
visible columns of `candidates.json`, which is stated rather than denied. What
makes it a selection rule and not a selection is that **none of those columns
is the quantity being measured**: no field in that file records boundaries,
span, per-boundary seconds, event volume, or wall clock, and no such reading
exists for either instance at the time of writing — none of the probe's
rollouts has been run. The rule removes discretion over *which* instances; it cannot remove
it over a number nobody has.

## 3. Configuration held fixed — and the one mechanism that holds it

Transferability is a claim about *other things being equal*, so the equality is
the load-bearing part.

**The mechanism: the same registered workflow name, invoked with no
overrides.** Run 1 ran `supervised_rollout_and_unit_test` with no `--<entry>.…=`
overrides ([`RUNBOOK.md` §2](../pipeline_end_to_end/RUNBOOK.md#2-the-command),
which gives three separate reasons for the no-overrides rule); this probe runs
the same registered name, also with none. Everything below is therefore held
fixed *by construction* rather than by a checklist — the table is a readable
copy of what that name resolves to, not a second source of truth.

| Item | Value | Where it is fixed |
|---|---|---|
| Workflow | `supervised_rollout_and_unit_test` | `src/swe_lab/workflow/definitions.py:267` |
| Actor model | `claude-sonnet-5` | `DEFAULT_MODEL`, `src/swe_lab/harnesses/claude_code/constants.py:114` |
| Actor version | pinned `2.1.212` | `PINNED_CLAUDE_CODE_VERSION`, `src/swe_lab/harnesses/claude_code/binary.py:45` |
| Capture / channel | `capture="proxy"`, `correction_channel=True` | `_supervised_rollout`, `definitions.py:167-172` |
| Supervisor model | `anthropic/claude-sonnet-5` | `SUPERVISOR_MODEL`, `definitions.py:126` |
| Policy | `SpeakWhenOffTrack` via `supervision(...)` | `SUPERVISED_ROLLOUT`, `definitions.py:185-191` |
| Budget | `3` | `SUPERVISOR_BUDGET`, `definitions.py:129` |
| Cooldown | `4` boundaries | `supervision(cooldown=4)`, `src/swe_lab/trace_synthesis/channel.py:522` |
| Window | `8` records | `supervision(window=8)`, `channel.py:523` |
| Poll interval | `0.5 s` | `SupervisedRun.poll_interval`, `channel.py:322` |
| Join timeout | `10.0 s` | `JOIN_TIMEOUT_SECONDS`, `channel.py:84` |
| Agent timeout | `3600.0 s` | `_AGENT_TIMEOUT_S`, `definitions.py:63` |
| Grading retries | `2` | `_UNIT_TEST_RETRIES`, `definitions.py:67` |

**Nothing in this list is deliberately different from run 1.** The only
intended differences between run 1 and each probe run are the instance id and
the wall-clock date.

**What can still drift, and the pre-flight that catches it.** Run 1 executed at
`main` = `3e97442` ([`WITNESS.md`](../pipeline_end_to_end/WITNESS.md)); a probe
run executes at whatever `main` is then, and an edit to any constant above
would change the comparison without changing this table. Checked at the time of
writing —

```sh
git log --oneline 3e97442..origin/main -- src/swe_lab/
```

**It is not empty**, and the correction is worth more than the clean claim it
replaces. The narrow three-file version of this command was empty, and an
earlier draft of this paragraph said so about the package as a whole — an
inference from the narrow check, written as a reading of the broad one. Widened,
the gate catches one commit at `origin/main` = `62f6bb5`:

```
f1257c9 docs(trace-synthesis): finish the lapse rename where it was left behind (#358)
```

**Re-run that command at launch** against the then-current `origin/main`.

**The gate is a criterion, not a list of files, and the burden of proof is on
proceeding.** Three rounds of review found three different holes in a
file-by-file version of this check — an unwatched path (`binary.py`, which
holds the actor-version pin this document cites), an auto-proceed branch that
waved through the actor's own model, and the capture, channel and timeout
settings that shape event production. A category that has produced three
instances will produce a fourth, so the enumeration is replaced by a default:

> **Any commit in `src/swe_lab/` since run 1 stops the launch and goes to the
> user — unless it is shown that the change cannot affect anything R1–R12
> reads.** The demonstration is written down, names the commits, and is the
> launcher's to produce; absence of a demonstration is a stop, not a proceed.

**Two categories qualify, and each names the demonstration that admits it** —
naming them is what keeps the criterion usable rather than a blanket halt:

1. **No executable change.** The commit's diff under `src/swe_lab/` touches
   only comment and docstring lines. Mechanically checkable, and the check is
   the demonstration.
2. **Grading-side only.** The change is confined to the `unit_test` entry — its
   retries, timeout, or own record — which the rollout entry has finished
   producing before grading begins, so R1–R12 cannot read it.

Everything else stops: the actor's model, pin, harness, invocation script,
capture mode or correction channel; the supervisor's model, policy, budget,
cooldown, window or poll interval; the agent timeout; the row-stamping in
`supervisor.py`; anything that changes what is recorded, when, or whether. **A
change of unclear category is a stop** — that is the point of putting the burden
on proceeding.

**The gate's first firing, worked here rather than left as a rule nobody has
run.** `f1257c9` is admitted under category 1. The demonstration is the diff —
so here is the diff, not a count of it. `git show f1257c9 --numstat --
src/swe_lab/`:

```
4	2	src/swe_lab/trace_synthesis/criterion.py
2	1	src/swe_lab/trace_synthesis/judge.py
```

**6 insertions and 3 deletions, 9 changed lines** — and every one of the nine is
docstring text, which `git show f1257c9 -U0 -- src/swe_lab/` prints in full.
A second, independent check, because "all of them look like docstrings" is
still a reading of the diff: parsing both files before and after, stripping
every module, class and function docstring, and comparing the resulting ASTs
gives **identical executable ASTs in both files** while the raw sources differ.
**A launch today would proceed on that demonstration**; a launch tomorrow
re-runs the command, because this paragraph does not update itself when `main`
moves.

*(An earlier draft of this paragraph said "changes 6 lines" — the insertion
count reported as the change count — in the sentence directly after asserting
that the demonstration is the diff rather than a description of it. Recorded
because the proximity is the lesson: writing the right rule does not make the
next sentence obey it.)*

**Why a stop is the user's call and not the launcher's.** If a load-bearing
constant moved, §5 compares two configurations rather than two instances and no
verdict it produces answers the transferability question. Spending four rollouts
on a question that can no longer be answered is a decision about the user's
quota and their instance ceiling. Put it to them, with what moved: they may
choose to spend it anyway, to revert to run 1's configuration, or to
re-register. No orchestrating agent owns that decision either (§8).

The actor version is also read from the container's own `--version` at report
time (`claude.info`), the way run 1 read it — not assumed from the pin.

## 4. The readouts, and where each one comes from

**Frozen. Nothing may be added to this list after the first rollout starts.**
Every entry names the file and the field, relative to a rollout attempt
directory `<root>/rollout/a0/` unless stated otherwise. **Every readout is
taken per rollout, not per instance** — four times over the probe's own runs —
and an instance's rollouts are never averaged into one row before §5 uses
them, since the spread between them is what V2 reads.

| # | Readout | File → field |
|---|---|---|
| R1 | `boundaries` | `supervisor.jsonl` → number of rows, **and** `run.json` → `metrics["supervision.boundaries"]` |
| R2 | `span_s` | `supervisor.jsonl` → last row's `at` − first row's `at`, both `datetime.fromisoformat` |
| R3 | `mean_s_per_boundary` | derived: `span_s / boundaries` |
| R4 | delta distribution | derived: the `boundaries − 1` consecutive differences of `at`, reported as min / p25 / median / p75 / p90 / max |
| R5 | lapses | `run.json` → `metrics["supervision.lapses"]`, **and** `supervisor.jsonl` rows with `kind == "lapse"`: their cursors, split into "no output" and "unparsable output" the way [`WITNESS.md`](../pipeline_end_to_end/WITNESS.md) splits them |
| R6 | corrections delivered | `run.json` → `metrics["supervision.corrections"]`, **and** `supervisor.jsonl` rows with `kind == "spoke"`: cursors, `at`, `policy`, and the text of each |
| R7 | native-transcript verbatim check | each R6 text located in `claude_code.native_transcript.tar.gz` → `projects/-app/*.jsonl`, by line number and `type`; this is run 1's Assertion A, and the count of texts found is reported against the count delivered |
| R8 | terminal state without intervention | `run.json` → `extra["rollout_outcome"]`; `complete.json` → `outcome`; `metrics["claude_code.exit_code"]`, `metrics["claude_code.timed_out"]`, `metrics["agent_complete"]`; whether `metrics` carries a `supervision.unhealthy` key at all; and a yes/no on whether a human touched the run after the command was issued |
| R9 | actor event volume | `claude_code.event_stream.jsonl` → line count, count carrying a `timestamp`, and the last stamped `timestamp` |
| R10 | `tail_s`, `tail_fraction` | derived: `tail_s` = last `supervisor.jsonl` row's `at` − R9's last stamped actor timestamp (an **upper bound**, for the reason run 1 gives); `tail_fraction = tail_s / span_s`. **Descriptive only** — it enters no verdict and licenses no inference about backlog, waiting or latency (§5) |
| R11 | rollout wall clock | `run.json` → `metrics["claude_code.wall_seconds"]` |
| R12 | actor cost | `claude_code.event_stream.jsonl` → terminal `result` event's `total_cost_usd`, `num_turns`, `duration_ms`, `usage` |
| R13 | grading, reported not claimed | `unit_test/a*/run.json` → `metrics["unit_test.required" / "passed" / "missing" / "resolved"]`, and how many attempts ran |
| R14 | corpus provenance | sha256 and byte size of every collected artifact, the `main` commit at run time, and the environment row of §4a — the obligation [`WITNESS.md`](../pipeline_end_to_end/WITNESS.md) discharges for run 1 |

### 4a. Every count is reported with the state that produced it

**A count is reported with the SHA, a clean tree, *and* the resolved data root
— the SHA alone is not enough.** This holds for the quality-gate counts in this
probe's PRs and for any count either probe run produces.

The reason is the hazard §2a records: `PROJECT_ROOT` beats the walk-up, so a
count taken in a worktree may be a reading of another checkout's gitignored
data, and two counts at one SHA that disagree are then evidence **about the two
environments**, not a contradiction about the commit. On 2026-09-02 that gap
turned three runs of one configuration into a reported "three worktrees, all
agreeing", and a correct 967/4 from two other agents into a wrongly-declared
irreproducible reading
([issue #366, entry 3](https://github.com/Luolc/swe-lab/issues/366)).

So a count in this probe's report or PR body carries four things: the count,
the commit, whether the tree was clean, and `datasets_root()` as *printed* —
not as inferred from which directory the command was typed in. The general
shape is one this repo has already named: **a true reading claimed over a wider
scope than it measures.**

This document's own gate reading, in that form: `967 passed, 4 skipped, 10
deselected`, tree clean, `PROJECT_ROOT` unset,
`datasets_root() = /home/ubuntu/dev/swe-lab-probe/datasets` — the
parquet-absent configuration, which is #366's third row and is why the count is
967/4 and not 968/3. **The commit is deliberately not written here**: a file
cannot name the commit that contains it without going stale on its next edit,
so the SHA travels with the reading in the PR body, where a gate count is
reported anyway. The other three parts of the state have no such excuse and are
above.

**Supervision-side tokens and dollars are absent, and the absence is the
readout** — the same gap run 1 reported
([`REPORT.md` §2](../pipeline_end_to_end/REPORT.md#2-the-three-readouts), line
3). Nothing here converts a call count into a cost.

**The reading script is not the readout list.** Run 1's
[`witness.py`](../pipeline_end_to_end/witness.py) computes most of the above but
assumes artifacts a differently-shaped run may not have — a terminal `result`
event, at least one lapse row, a third grading attempt, a native-transcript
archive. A probe-specific witness will be written from it.

**What the script may and may not be changed to do**, because "tolerate a
missing artifact" was too wide a permission: it may be edited to *read* an
artifact that is shaped differently or to print `absent` where one is missing,
at any time. It may **not** decide anything §5 decides — which invocations
contribute an `m`, which reason code an excluded one gets, or which verdict
branch is taken. Those are fixed in §5.0 and the script implements them as
written; a script edit that changes an inclusion is an amendment to the
protocol, not a fix to the reader. Adding a row to the table above remains
forbidden either way.

## 5. The verdict rule — written before the numbers exist

### 5.0 What `m` is, and which invocations contribute one

**The quantity.** `m = span_s / boundaries` — the last supervisor row's `at`
minus the first, divided by the **row count**, not by `boundaries − 1`. Every
value below is built that way, including run 1's `m_1 = 6.58`, because the
number under test is run 1's and a differently-constructed mean would not be a
test of it.

**What `m` is not, stated because an earlier draft of this document got it
backwards.** `at` is stamped inside `_row`
(`src/swe_lab/trace_synthesis/supervisor.py:639,649`) —

```python
        "at": self.now().isoformat(),
```

— and `_row` is called *after* `policy.consider` has returned, so the span runs
from the **second** call to the last: for `N` synchronous calls of equal duration `d`,
`m = d(N−1)/N < d`, and `m = 0` at `N = 1`. So `m` is **not** an upper bound on
judge latency; under a continuous backlog it is a slight **under**estimate of
per-call duration, and it is not an estimator of latency in either direction.
It is a **comparison quantity**: the same arithmetic on the same artifact
across rollouts, which is all V1 needs and all it claims.

The `(N−1)/N` factor differs between rollouts of different length — 0.900 at
`N = 10`, 0.994 at `N = 170` — so `m` carries a length-dependent bias of up to
10% for `N ≥ 10`, and more below that. It is small against the 2× thresholds
below and it is **not** corrected for; instead **`N` is reported beside every
`m`**, so a reader can see when two values being compared came from very
different lengths.

**When an invocation contributes an `m`.** An *invocation* is one execution of
the workflow on one instance under one rollout id. It contributes an `m` if and
only if **all** of:

- **a.** `supervisor.jsonl` exists and every line parses as JSON;
- **b.** it has **at least 2 rows** (a span needs two ends);
- **c.** its row count equals `metrics["supervision.boundaries"]`;
- **d.** the last row's `at` is strictly later than the first's.

**"Usable rollout" is shorthand, used throughout this document, for exactly
this: an invocation that contributes an `m`.** Otherwise it contributes **no
`m`**, and the report prints exactly one code from this closed list: `no-log`, `unparsable-log`, `fewer-than-2-rows`,
`count-mismatch`, `zero-span`. The row count of `supervisor.jsonl` is the
denominator, because it is the same artifact the span comes from; the metric is
the cross-check, and a disagreement between them **disqualifies the invocation
under (c)** rather than being settled by picking one — the two numbers
disagreeing means the account being measured is not internally consistent, and
choosing a side would be the discretion this rule exists to remove.

**Inclusion is independent of how the rollout ended.** `rollout_outcome` plays
no part in (a)–(d). A `TIMED_OUT`, `SUPERVISION_FAILED`, `NO_PATCH` or
host-failed invocation contributes its `m` whenever its log meets them, however
extreme that `m` looks. Excluding on the outcome word is precisely the knob the
playbook warns about, and its direction flatters: the runs that would be
dropped are the ones whose numbers are inconvenient.

**Every qualifying invocation contributes — including a replaced one.** If the
user authorizes a replacement (§1) and the failed predecessor's log meets
(a)–(d), **both** values enter, and instance `i`'s `W_i` is `max/min` over
**all** of that instance's contributing values, not over a chosen two. A
replacement is not a second chance at a number.

**Five values is the *planned* count** — `m_1` (run 1, one rollout) plus two
from each probe instance — not a bound on how many exist. §5.0 admits every
qualifying invocation, so a user-authorized replacement whose predecessor also
qualifies leaves an instance with three. The definitions below are written over
*all* contributing values for exactly that reason, and no rule anywhere may be
stated in terms of "the five" or "the two".

**Two verdicts, computed in this order, each pinned here before any of the new
values exists.** They answer different questions and
neither may be reported without the other.

### V1 — consistency: the open question, as it was asked

    R = max(all available m) / min(all available m)

- `R ≤ 2` → **`consistent-within-2x`**
- `R > 2` → **`not-consistent-within-2x`**

Two values, and no "inconclusive" third — the escape hatch is the whole of what
this section exists to prevent. What *can* happen instead of a verdict is that
the rule's coverage condition is unmet, which is a different thing and is
defined just below.

`R` is taken over **every contributing `m`** (§5.0), run 1's included, and
deliberately **not** over per-instance summaries: a downstream planner faces the
spread of individual runs, not the spread of their averages, and averaging
inside an instance would hide exactly the run-to-run component V2 exists to
measure. The report states the count it was computed over (`R over 5 runs`, or
fewer, never `R` unqualified).

**Minimum coverage, without which V1 is not computed at all.** V1 requires
contributing values from **at least two distinct instances, at least one of
them a probe instance**. Otherwise the report says exactly:

> `V1: rule does not apply — contributing m from <n> instance(s): <which>.`

**Why this is here, and it is not a hedge.** Without it, four failed probe
rollouts leave `R = max/min` over run 1's single value — `R = 1.0` — and V1
emits `consistent-within-2x`: **total failure rendered as an affirmative
result**, produced by the very rule written to stop post-hoc reinterpretation.
The general lesson is wider than the patch: **a hard rule with no escape hatch
can still manufacture a false positive at its boundary**, and "no inconclusive
branch" is only safe when paired with a coverage condition.

**`rule does not apply` is not `inconclusive`, and the difference is
mechanical.** An inconclusive verdict is one the data could have avoided and is
therefore available whatever the numbers say — which is what makes it an escape
hatch. `rule does not apply` is reachable **only** by counting instances that
produced a contributing `m`, is announced with that count and their names, and
takes no argument from any observed value. The same distinction governs V2's
uncomputable branch below.

**What each verdict licenses:**

- `consistent-within-2x` licenses exactly one sentence: *across the rollouts
  measured here, per-boundary supervision cost lay in `[min, max]` s.* It does
  **not** license "6.58 s per boundary holds", and it does not license
  extrapolating a headroom figure from any single rollout.
- `not-consistent-within-2x` licenses: *6.58 s per boundary is not a
  transferable constant; any downstream figure derived from it must carry the
  observed range in place of the point.* It names **no cause** — that is V2's
  question, and V2 does not name one either.

**Why 2, and why the threshold is not tunable.** The factor was chosen while
exactly one measurement existed, so it cannot have been fitted to the spread it
will judge — there was no spread yet, and there still is none. It is also the
coarseness the downstream consequence can absorb: §7a's headroom figure
(`3600 / m` boundaries) moves by the same factor, and a factor of 2 in headroom
is the difference between "run 1's 170 boundaries used a third of the budget"
and "used two thirds", which is a planning input either way. A factor of 4
would not be.

### V2 — attribution: is a spread between instances, or just between runs?

This is what the second rollout per instance buys, and the rule that reads it is
fixed **now**, before the scatter it references has been observed — otherwise
"how much run-to-run scatter counts as a lot" gets defined against numbers
already on the screen, which is the one failure this whole document is built to
prevent.

Two quantities, both ratios, both over the contributing values of §5.0:

    M_i = the set of ALL contributing m from instance i  (§5.0)

    W_i = max(M_i) / min(M_i)
    W   = max of W_i over probe instances with |M_i| >= 2
          — the largest run-to-run spread actually observed
    B   = max over instances of c_i / min over instances of c_i,
          where c_i = the geometric mean of ALL of M_i
          (for run 1, c_1 = m_1, its single value)

**`W_i` and `c_i` range over every contributing value, never over a chosen
two.** A worked case, because the wording that said "two rollouts" produced the
opposite verdict on the same data: an instance with `M = {100, 1, 1.1}` — a
host-failed predecessor, the other planned invocation, and an authorized
replacement — beside another with `M = {20, 20}` gives `c = 4.7914` and `20`,
so `B = 4.1741`. Over **all** values `W = 100` and the verdict is
`not-separated`; over a chosen `{1, 1.1}` it would be `W = 1.1` and
`separated`. Same data, opposite answers, and the difference is entirely which
values the formula is allowed to see. `|M_i| >= 2` is a **minimum**, not an
exact count.

**Verdict — `separated` requires `B > W` *and* `B > 2`; every other case is a
`not-separated`, split by *which* condition failed.** The split is not
decoration: `not-separated` is the negation of a conjunction, so it is reached
three different ways, and one licensed sentence cannot cover them.

| | condition | verdict |
|---|---|---|
| | `B > W` and `B > 2` | **`separated`** |
| | `B ≤ W` and `B > 2` | **`not-separated (within scatter)`** |
| | `B > W` and `B ≤ 2` | **`not-separated (below floor)`** |
| | `B ≤ W` and `B ≤ 2` | **`not-separated (both)`** |

Each condition has its own job: `B > W` asks whether the between-instance
difference is larger than run-to-run scatter as this probe actually saw it, and
`B > 2` is the same materiality floor V1 uses, so a difference that is real but
too small to move a headroom figure is not reported as a separation.

**The exact sentence each branch licenses:**

- `separated` — *the instances differed by more than the run-to-run scatter
  observed here **and** by more than a factor of 2.* It does **not** license
  "per-boundary cost is a property of the instance": with a handful of draws per
  instance, `W` is a bound, not an estimate.
- `not-separated (within scatter)` — *the between-instance difference was
  material (over 2×) but **did not exceed** this probe's own observed
  run-to-run scatter.*
- `not-separated (below floor)` — *the between-instance difference exceeded the
  observed run-to-run scatter but **did not exceed** the 2× materiality floor.*
  "Did not exceed", not "under": the branch is `B ≤ 2`, so it includes `B = 2`
  exactly, where "under 2×" would be false (row E below). And note what this
  branch is not: it is **not** a statement that the difference was within
  scatter, which would be false whenever `B > W` — row B below.
- `not-separated (both)` — *the difference was neither material nor larger than
  the observed scatter.*
- **None of the four is evidence that the instances are alike.** Two draws
  cannot exclude a difference smaller than the scatter they showed, and no
  branch may be cited as if they could.
- **None of the four names a cause.** No claim that a repository, a language, an
  event volume or a task shape explains a difference is licensed here.

**Three properties of `W` that are pre-registered as limitations, not
discovered as findings** — and only the first two have a known direction:

1. **`W` is built from the probe instances only.** Run 1 has one rollout and
   contributes to `B` as a point and to `W` not at all (§0).
2. **The range of a few draws understates the spread of their distribution**,
   so `W` is an underestimate, which makes the `B > W` condition **easier** to
   satisfy. That direction is stated about `W` and about nothing else.
3. **`W` can come out small by luck.** Draws landing close is an ordinary
   outcome, not evidence of stability. This is why `B > 2` is joined to
   `B > W` rather than replacing it: the floor is what stops a lucky-small `W`
   from manufacturing a separation on its own.

**What the missing replication of run 1 does to the *combined* verdict is
unknown, and the earlier claim that it ran one way was wrong.** Point 1 makes
`W` optimistic, but run 1 also enters `B` as an unreplicated point, and there
its effect has no fixed sign — a counterexample: probe centres 3.5 and 4.0 with
`W = 1` give `B = 1.88` against run 1's 6.58 and a `not-separated`; had run 1
drawn a second value of 8.0, `c_1 = 7.26`, `W = 1.216`, `B = 2.073` and the
verdict is `separated`. There the missing replication **suppressed** a
separation rather than manufacturing one. So: the direction is stated for `W`
alone, and the combined V2 classification is left with **unknown direction**.

**The rule was run against the defects it was written for before being called a
rule** — on invented values, since no real ones exist, and **checked against the
sentence each branch licenses, not only against the branch label.** That last
part is the method, and it is registered as a standing obligation: *a verdict's
invented-value check reads the licensed prose aloud against the numbers.* An
earlier draft of this section passed the label check and shipped a licensed
sentence that row B falsifies — the labels were right and the English was
wrong, which no comparison of labels can catch.

| # | invented `m` values | V1 | `W` | `B` | V2 |
|---|---|---|---|---|---|
| A | O `6.58`; P1 `3.0, 12.0`; P2 `4.0, 11.0` | `not-consistent-within-2x` | 4.0000 | 1.1055 | `not-separated (both)` |
| B | O `6.58`; P1 `6.50, 6.55`; P2 `8.0, 8.1` | `consistent-within-2x` | 1.0125 | 1.2337 | `not-separated (below floor)` |
| C | O `6.58`; P1 `6.2, 7.0`; P2 `20.0, 21.0` | `not-consistent-within-2x` | 1.1290 | 3.1146 | `separated` |
| D | O `6.58`; P1 `1.0, 9.0`; P2 `20.0, 25.0` | `not-consistent-within-2x` | 9.0000 | 7.4536 | `not-separated (within scatter)` |
| E | O `6.58`; P1 `6.58, 6.58`; P2 `13.16, 13.16` | `consistent-within-2x` | 1.0000 | 2.0000 | `not-separated (below floor)` |

- **A** is the defect `B > W` exists for: an `R` of 4 that is entirely
  run-to-run must not read as a difference between instances. Licensed prose
  reads true — neither material nor larger than the observed scatter.
- **B** is the defect the `B > 2` floor exists for: `B > W` alone would have
  fired on a 1.23× difference because `W` came out tiny. **This row is also why
  the licensing was split.** Here `B > W`, so the old single sentence — "the
  differences seen are not larger than this probe's own run-to-run scatter" —
  would have been **false** while the label was right. The `below floor`
  sentence reads true.
- **C** shows the rule can still fire, and its prose reads true on both clauses.
- **D** exercises the fourth branch, which nothing else reaches: a material
  `B` of 7.45 that is nonetheless inside a `W` of 9. `within scatter` reads
  true; `below floor` would have been false.
- **E** sits exactly on the registered boundary, `B = 2.0000` and `R = 2.0`,
  which is where a rule's prose fails while its interior reads fine. Both
  thresholds are `≤`, so both take the inclusive branch — and the old wording
  "under the 2× floor" was **false** here while the label was right, the same
  defect as row B one boundary over. **Rows are required at the boundary, not
  only in the interiors**: A–D all passed the label check and neither of the
  two prose defects was in them.

### What the reduced design does to V1 and V2

§1's default — no replacement is run, so an instance may contribute fewer
values than planned (how many is §5.0's answer, not an assumption) — is decided
there, but its analytical consequences are decided
**here**, before any run, so that a shrunken design is never read against a
rule chosen once its shape was known.

**V2 requires both probe instances to have at least two contributing values.**
With fewer, the report says exactly:

> `V2: not computable — <n> of 2 probe instances contributed 2 or more m.`

This holds at `n = 1` as well as at `n = 0`, and the reason `n = 1` does not
suffice is not caution, it is what `W` would have to mean. At `n = 1`, `W` is
one instance's single observed run-to-run ratio, and using it as the yardstick
for *every* instance assumes run-to-run scatter transfers between instances —
an assumption of exactly the kind this probe exists to test for the mean, and
one it would then be smuggling in as a premise to reach its own verdict. **This
is the sample being gone, not an escape hatch**: the difference is that an
escape hatch is available whatever the data say, while this branch is entered
only by a rollout that did not happen, is announced with the count that
triggered it, and takes no argument from the values observed.

An instance with a single contributing value still enters `B` as a point,
exactly as run 1 does — but with V2 uncomputed, `B` is reported as a raw
readout and **carries no verdict**; it may not be described as a difference
between instances.

**V1 still reports, in the same form, and its degradation runs one way.** `R`
is computed over the rollouts that exist, with the count stated (`R over 4
runs`). Dropping a value from a `max/min` set can only lower the ratio or leave
it unchanged, so a reduced design makes **`consistent-within-2x` strictly
easier to reach** — it is the optimistic branch under degradation, the way
`separated` is the optimistic branch of V2 (§5's three `W` limitations). A
`consistent-within-2x` reported over fewer than five rollouts says so in the
same sentence as the verdict.

### There is no saturation discriminator, and R10 carries no inference

An earlier draft of this document registered a "quantity check": that
`tail_fraction < 0.10` established the supervisor had kept pace, making that
run's `m` a waiting-diluted upper bound on judge latency. **That branch is
removed, not softened.** Both of its steps are false, and each was shown false
by a counterexample rather than argued down:

- **Small tail does not establish no backlog.** A supervisor continuously
  backlogged from 0 s to 100 s, with the actor's last stamped event at 95 s,
  registers `tail_s = 5`, `tail_fraction = 0.05` — the branch fires while
  waiting is exactly zero.
- **`m` is not an upper bound on judge latency in the first place.** §5.0: `at`
  is stamped after `policy.consider`, so `m = d(N−1)/N < d` for `N`
  synchronous calls of duration `d`. Under the very backlog the branch claimed
  to detect the absence of, `m` runs *below* per-call duration, not above it.

**Nothing replaces it.** This probe instruments no per-call duration and no
wait, so it has no discriminator between "the supervisor was the bottleneck"
and "the actor was", and it does not claim one. Adding such an instrument is a
code change with its own rule to register before running, not something this
document can assert its way to.

**R10 (`tail_s`, `tail_fraction`) stays a raw readout and gains no role.** It is
reported per rollout because run 1 reported it and the series should stay
comparable; it enters no verdict, qualifies no verdict, and licenses no
sentence about backlog, waiting, dilution or latency. Recording the removed
branch here rather than deleting it silently is deliberate: the next reader to
notice that `tail_fraction` looks like a saturation signal should find the two
counterexamples before rebuilding it.

**Why the wording was not merely weakened.** Replacing "establishes" with
something softer would have kept an absent discriminator in the design while
making its absence harder to see — and if the chain had been sound, softening
would have thrown away a true conclusion. Either way softening is the wrong
move; the chain broke at its first step, so the branch goes.

**Only R1, R2 and R3 feed a verdict** — they build `m`. **Every other readout,
R4 through R14 without exception, may never flip either verdict**; they are
reported on every rollout and read nothing into it. Stated as the complement
rather than as a list, so a readout cannot be omitted from it by accident. The median
delta is the obvious candidate for a second shot at the target and is
explicitly denied one: it describes the distribution, and both verdicts are on
the mean.

**No trend claim.** Five rollouts over three instances, two of them replicated,
is not a design that supports one. The report tabulates `(boundaries, m)` per
rollout and makes **no** regression, correlation, monotonicity or "scales with
event volume" claim. This is pre-registered precisely because such a story will
be available and tempting once five points exist — more available at five than
it was at three.

**The robustness verdict — an ordered mapping to exactly one word.** The three
earlier words overlapped: a no-human `TIMED_OUT` satisfied both
`unattended-terminal` and `did-not-terminate`, leaving the author free to pick
the flattering one after the fact. The words below are decided by taking the
**first** matching row, which makes them mutually exclusive, and the last row is
the complement of the others, which makes them exhaustive.

| order | condition | word |
|---|---|---|
| 1 | a human intervened (defined below) | **`attended`** |
| 2 | no intervention, and `run.json` records a `rollout_outcome` | **`unattended-terminal`** |
| 3 | otherwise (no intervention, no recorded outcome) | **`no-terminal-record`** |

Row 2 covers **every** member of `RolloutOutcome`, `TIMED_OUT`,
`SUPERVISION_FAILED`, `OOM_KILLED`, `SYSTEM_FAILED`, `NO_PATCH` and
`PATCH_PRODUCED` alike: the word answers *did the pipeline reach an end and
classify it without us*, which a recorded timeout does. **The outcome word
itself is always reported beside it** — `unattended-terminal (timed_out)` — so
nothing the old `did-not-terminate` conveyed is lost, and the overlap that let
it be chosen is gone. Row 3 is the case with no classification at all: the
harness died, the host died, or `run.json` was never written.

**Human intervention, defined so row 1 is decidable.** Any action after the
launch command is issued and before the workflow process exits that touches the
run, its container, its output directory, the host's Docker state, or the
checkout it runs from — killing, restarting, freeing memory, pulling an image,
editing a file, `docker rm`. **Read-only observation is not intervention**:
reading logs, `docker ps`, `gh` queries, watching the pane. If an intervention
occurred, the report states what it was and when.

This is a per-rollout reading, never aggregated into a rate.
**Grading is scored separately from the rollout.** A grading failure or timeout
— foreseeable for P2, whose screening record lists 979 `pass_to_pass` tests
against `_UNIT_TEST_TIMEOUT_S = 1800.0` — does not touch the rollout's
robustness verdict and does not invalidate any of R1–R12, all of which are read
from the rollout attempt. R13 reports what grading did, as a reading, not as a
claim.

## 6. Failure handling, decided before it happens

**A failed rollout is a result by default.** The robustness question's answer
for an instance is allowed to be "no" — `no-terminal-record`, or an
`unattended-terminal` carrying a failure outcome word (§5) — and that is the
answer, not a broken run to be discarded. Its `m` still enters V1 and V2
whenever §5.0's (a)–(d) hold, whatever the outcome word says. Anything else
lets the excluded set grow in the direction that flatters the result.

**The closed list of causes for which a replacement may be *requested*.** Per
§1, the planned 2 rollouts per instance already sit on the ask-first ceiling, so
**nothing in this section authorizes a re-run** — only the user does, directly,
and no agent may give or relay that instruction. This list says which failures
may be put to the user at all; the default for every one of them is still §1's
reduced design. A failure qualifies when it is positively identified as one of:

1. the instance's image is missing or its pull failed;
2. Docker itself is unreachable (`RUNBOOK.md` §1's exit-code-2 branch);
3. the host box failed — OOM of the machine, not of the container, or a reboot;
4. no model call succeeded at all: an absent or rejected credential, or a
   network failure that left `supervisor.jsonl` with zero rows.

Anything not on this list — including `TIMED_OUT`, `SUPERVISION_FAILED`, a
crash mid-run, a lapse rate of any size, and a rollout that produced no patch —
is a **result** and is reported. The list is closed; adding to it after seeing
a failure is the amendment this section exists to forbid.

**Whether the user was asked, and what they said, is reported either way** — a
replacement that was never requested and one that was requested and declined
are different facts, and neither adds a value; what the instance contributes is
whatever §5.0 admits from the invocations that did run.

**`TIMED_OUT` inherits run 1's presumption.** A first-attempt timeout is
presumed ours, not the actor's, unless `claude_code.proxy_log.jsonl`'s own
timeline shows the actor actively working up to the wall clock
([`PREREGISTRATION.md` §7](../pipeline_end_to_end/PREREGISTRATION.md#7-failure-classification)).
**What this document does *not* add to that presumption**, because an earlier
draft did and it was the removed discriminator growing back in a second place:
R10 and R11 do **not** show "the supervisor still catching up while the actor
sat finished", and no sentence here may read a timeout that way. A timeout is
reported as: the outcome word, whether the invocation contributed an `m`
(§5.0), and the raw R10/R11 values — with no causal reading of throughput
attached. The reason is §5's: the probe has no instrument that observes backlog
or waiting, so it has nothing that could support the claim, here or anywhere
else in this file.

**Every rollout uses a fresh rollout id — the second planned one included.** A
non-`--resume` invocation deletes the prior attempt's output directory outright
([`RUNBOOK.md` §3](../pipeline_end_to_end/RUNBOOK.md#3-if-it-fails--read-this-before-doing-anything)),
so an instance's two planned rollouts must not share an id or the first is
destroyed by the second — which would take `W` with it. Every corpus is kept
and reported, an authorized replacement's predecessor included.

**Launch procedure is run 1's.**
[`RUNBOOK.md`](../pipeline_end_to_end/RUNBOOK.md) is the launch procedure, and
its pre-flight is re-run at launch for each probe instance with that instance's
id substituted at the two places it names an instance. This document does not
copy it.

## 7. What this probe does not measure or claim

- **Not an effect estimate.** No sentence in the report may say supervision
  helped or hurt. This probe has no control arm and does not run
  `control_rollout_and_unit_test`.
- **Not a resolve rate.** R13 is reported per rollout. No `resolved N/M` is
  computed, no grading outcome is evidence about supervision, and rollouts
  of one instance agreeing or disagreeing on `resolved` is **not** reported as
  a stability finding — that would be an effect-side claim reached with the
  budget bought for a throughput-side one.
- **Not a generalization.** Three instances and at most five rollouts, all from
  [issue #261](https://github.com/Luolc/swe-lab/issues/261)'s mixed-outcome 40,
  all with `verdict == "good"` and a proven image, one of the three with a
  single rollout. The strongest available conclusions are V1's and V2's, in the
  words each licenses — not that any figure holds for SWE-bench Pro, for this
  pipeline, or for a batch.
- **Not a variance estimate.** Two draws per instance give a range, not a
  variance, a standard error or a confidence interval, and none of those may
  appear in the report.
- **Not an attribution of any disagreement.** §0 and V2's licensed sentences:
  the second rollout per instance bounds run-to-run scatter crudely, it does
  not explain a difference. Neither V2 branch names a cause.
- **Not a measurement of judge latency, and nothing stands in for one.** No
  per-call duration and no wait is instrumented. `m` is a comparison quantity
  and is not an estimator of latency in either direction (§5.0); the
  `tail_fraction` branch that once claimed to bound it is removed, with the two
  counterexamples that killed it recorded in §5.
- **Not a claim about whether the supervisor or the actor was the bottleneck.**
  This probe has no discriminator for that and does not acquire one by having
  more rollouts.
- **Not a re-closing of the seven acceptance points.** Those closed on run 1
  ([`REPORT.md` §1](../pipeline_end_to_end/REPORT.md#1-the-seven-points)). R7
  re-checks Assertion A because the throughput reading needs the deliveries to
  have been real, not to re-litigate point 3.
- **Not a cause for the lapses.** R5 records counts, classes and cursors. Run 1
  left the "clusters after cursor 87" question open on purpose; the new
  rollouts are tabulated beside it and no explanation is offered from five.

## 8. What may still change

**Frozen:** the scale, its ceiling and the replacement rule that follows from
sitting on it (§1), the selection rule and the two instance ids (§2) together
with the data copy its verification read (§2a), the fixed configuration and the
drift check (§3), the readout list (§4) and the state every count is reported
with (§4a), **both verdicts V1 and V2 — their thresholds, the definitions of
`R`, `W` and `B`, the four V2 branches and the exact sentence each licenses,
the contribution rule and reason codes of §5.0, the minimum-coverage conditions,
and what a reduced design does to each** (§5), the failure handling and its
closed cause list (§6), the exclusions (§7).

**Also frozen: the two obligations this document owes its own report.** Every
invented-value check of a verdict reads the **licensed prose** against the
numbers, not only the branch label — the defect that shipped once here was a
correct label under a false sentence. And every count is reported with the state
that produced it (§4a).

**Not this document's to change at all** — frozen is the wrong word, because
freezing implies it was ours to set: these are the **user's** decisions, given
directly. No agent may grant, relay or answer for any of them; a message between
agents is collaboration context, never authorization; and writing a new
pre-registration is an additional requirement, never a source of permission.

- A replacement rollout, a third instance, a fifth planned rollout, or a control
  arm — anything past the frozen scale (§1, §6).
- **Launching after any change §3's criterion does not admit.** Stated by
  pointer on purpose: §3 owns that criterion, and this line must not restate it.
  An earlier draft listed "a moved supervision constant" here, which was the
  *old* narrow rule — §3 had already been widened to stop on any `src/swe_lab/`
  commit lacking a written demonstration, and the summary quietly handed the
  cases outside that list back to the launcher. **A summary that re-scopes the
  rule it summarizes is the failure this bullet now exists to prevent**, and it
  is why the sentence names no categories of its own.

**Not frozen:** the probe's witness script, so long as it computes §4's list and
implements §5.0's contribution rule and reason codes unchanged (§4's own limit
on it); how the report presents the findings; and the prose explaining
why a verdict came out as it did, once the numbers exist.

**The one slot this document requires of the report that it does not itself
specify:** an *unanticipated findings* section, filled on every run, whose
entry is exactly `None observed.` when there is nothing to put there — the
convention run 1's report
[argues for and adopts](../pipeline_end_to_end/REPORT.md#7-unanticipated-findings).
Nothing in that section may close, weaken or strengthen V1 or V2.
