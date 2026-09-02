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
run-to-run scatter — the interval between two draws — and nothing more: two
observations do not estimate a variance, and the interval between two draws
systematically understates the spread of the distribution they came from. So
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
certainly has, and which nothing here measures. The consequence runs one
direction and is stated in §5 where it bites: the observed scatter is an
**under**estimate, so the between-instance verdict is correspondingly
**over**confident, most of all about run 1's instance. The cheapest single
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
With the planned count at the ceiling, a re-run is not additive. A rollout that
fails for one of the named infrastructure causes in §6 may be replaced **only
with the owner's explicit go-ahead, recorded in the report** — because a
replacement is a third *invocation* on that instance, and this document takes
the strict reading of the ask-first line: **an invocation counts whether or not
it produced a record.** The lenient reading ("a rollout that made no model call
never happened") is available and is deliberately not taken, since it is the
reading whose adoption would be indistinguishable from wanting one more run.

**The default, when no go-ahead is sought or given: proceed with the reduced
design.** That instance ends with one usable rollout, contributing a point to
§5's between-instance comparison and nothing to its within-instance one, and
the report says which instance it was. No agent decides this on its own.

A third instance, a fifth planned rollout, or a control arm requires a **new
pre-registration**, written before it runs; this one may not be amended to
allow them.

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
exists for either instance at the time of writing — the two rollouts have not
been run. The rule removes discretion over *which* instances; it cannot remove
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
git log --oneline 3e97442..origin/main -- \
  src/swe_lab/workflow/definitions.py \
  src/swe_lab/trace_synthesis/channel.py \
  src/swe_lab/harnesses/claude_code/constants.py
```

— which is empty at `origin/main` = `62f6bb5`: no commit has touched those three
files since run 1. **Re-run that command at launch** with the then-current
`origin/main`. If it is non-empty, the report says which constant moved and
whether it is one of the thirteen above; a moved constant does not void the
probe, it becomes a stated difference, and a moved *supervision* constant
(model, policy, budget, cooldown, window, poll interval) means the primary
comparison in §4 is between two different configurations and must be reported
as such rather than as a transferability result.

The actor version is also read from the container's own `--version` at report
time (`claude.info`), the way run 1 read it — not assumed from the pin.

## 4. The readouts, and where each one comes from

**Frozen. Nothing may be added to this list after the first rollout starts.**
Every entry names the file and the field, relative to a rollout attempt
directory `<root>/rollout/a0/` unless stated otherwise. **Every readout is
taken per rollout, not per instance** — four times over the probe's own runs —
and an instance's two rollouts are never averaged into one row before §5 uses
them, since the gap between them is what V2 reads.

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
| R10 | `tail_s`, `tail_fraction` | derived: `tail_s` = last `supervisor.jsonl` row's `at` − R9's last stamped actor timestamp (an **upper bound**, for the reason run 1 gives); `tail_fraction = tail_s / span_s` |
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
archive. A probe-specific witness will be written from it. **Making the script
tolerate a missing artifact is operational and permitted at any time; adding a
row to the table above is not.** A run that lacks the artifact behind a readout
reports that readout as *absent*, with the absence named.

## 5. The verdict rule — written before the numbers exist

The measured quantity, per rollout, is R3: `span_s / boundaries`, dividing by
the row count and **not** by `boundaries − 1`. Every value below is built that
way, including run 1's `m_1 = 6.58`, because the number under test is run 1's
and a differently-constructed mean would not be a test of it.

Five values are expected: `m_1` (run 1, one rollout), `m_P1a`, `m_P1b`,
`m_P2a`, `m_P2b`. **Two verdicts, computed in this order, each pinned here
before any of the four new values exists.** They answer different questions and
neither may be reported without the other.

### V1 — consistency: the open question, as it was asked

    R = max(all available m) / min(all available m)

- `R ≤ 2` → **`consistent-within-2x`**
- `R > 2` → **`not-consistent-within-2x`**

No third value and no "inconclusive" branch — an escape hatch here is the whole
of what this section exists to prevent. `R` is taken over **every** usable
rollout, run 1's included, and deliberately **not** over per-instance
summaries: a downstream planner faces the spread of individual runs, not the
spread of their averages, and averaging inside an instance would hide exactly
the run-to-run component V2 exists to measure. The report states the count it
was computed over (`R over 5 runs`, or fewer, never `R` unqualified).

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

Two quantities, both ratios, both from the same five values:

    W = max over instances with two rollouts of ( max(m_i) / min(m_i) )
        — the largest run-to-run spread actually observed
    B = max over instances of c_i  /  min over instances of c_i,
        where c_i = the geometric mean of instance i's usable rollouts
        (for run 1, c_1 = m_1, its single value)

**Verdict:**

- **`separated`** if `B > W` **and** `B > 2`
- **`not-separated`** otherwise

Both conditions, joined by `and`, and each carries its own job: `B > W` asks
whether the between-instance difference is larger than run-to-run scatter as
this probe actually saw it, and `B > 2` is the same materiality floor V1 uses,
so a difference that is real but too small to move a headroom figure is not
reported as a separation. Neither condition alone is the verdict.

**What each verdict licenses, and what neither does:**

- `separated` licenses: *the instances differed by more than the run-to-run
  scatter observed here, and by more than a factor of 2.* It does **not**
  license "per-boundary cost is a property of the instance" — with two draws
  per instance, `W` is a bound, not an estimate.
- `not-separated` licenses: *the differences seen are not larger than this
  probe's own run-to-run scatter.* It is **not** evidence that the instances
  are alike; two draws cannot exclude a difference smaller than the scatter
  they showed, and this verdict may never be cited as if they could.
- **Neither verdict names a cause.** No claim that a repository, a language, an
  event volume or a task shape explains a difference is licensed by this probe
  under either branch.

**Three properties of `W` that are pre-registered as limitations, not
discovered as findings.** They run in known directions, which is why they can be
written down before the data:

1. **`W` is built from two instances, not three.** Run 1 has one rollout and
   contributes to `B` as a point and to `W` not at all (§0).
2. **The range of two draws understates the spread of their distribution**, and
   understating `W` makes `separated` *easier* to reach. So `separated` is the
   optimistic branch of this rule, and the report says so wherever it appears.
3. **`W` can come out small by luck.** Two draws landing close is an ordinary
   outcome, not evidence of stability. This is why `B > 2` is joined to
   `B > W` rather than replaced by it: the materiality floor is what stops a
   lucky-small `W` from manufacturing a separation on its own.

**The rule was run against the two defects it was written for, before being
called a rule** — on invented values, since no real ones exist, and recorded
here so a reader can see the branches are reachable rather than take it on
trust:

| scenario (invented `m` values) | V1 | `W` | `B` | V2 |
|---|---|---|---|---|
| O `6.58`; P1 `3.0, 12.0`; P2 `4.0, 11.0` — spread is run-to-run | `not-consistent-within-2x` | 4.00 | 1.11 | `not-separated` |
| O `6.58`; P1 `6.50, 6.55`; P2 `8.0, 8.1` — lucky-tight `W` | `consistent-within-2x` | 1.01 | 1.23 | `not-separated` |
| O `6.58`; P1 `6.2, 7.0`; P2 `20.0, 21.0` — instance really differs | `not-consistent-within-2x` | 1.13 | 3.11 | `separated` |

Row 1 is the defect `B > W` exists for: a large `R` that is entirely
run-to-run must not read as a difference between instances. Row 2 is the defect
the `B > 2` floor exists for: `B > W` alone would have fired there on a
1.23× difference, because `W` happened to come out tiny. Both branches stay
shut, and row 3 shows the rule is still able to fire.

**If an instance ends with one usable rollout** (§1's reduced-design default),
it contributes to `B` as a point and to `W` not at all, exactly as run 1 does.
If **no** instance has two usable rollouts, `W` does not exist, **V2 is not
computed, and the report says `V2: not computable, no instance had two usable
rollouts`** — that is an absence of data, not an inconclusive verdict, and V1
still reports normally.

**The quantity check, and it qualifies the verdict rather than replacing it.**
`span_s / boundaries` is not the judge's latency; it is the *interval between
consecutive supervisor rows*, which equals judge latency only while the
supervisor has a backlog. On a run where the actor is slower than the
supervisor, the same arithmetic measures how fast the actor emitted events —
a different quantity in identical units. R10 is the discriminator, and it is
one-directional:

- `tail_fraction < 0.10` **establishes** that the supervisor overlapped the
  actor for more than 90% of its span, so that run's `m` is an **upper bound on
  judge latency, diluted by waiting** — not a measurement of it. The report
  must say so for that run.
- `tail_fraction ≥ 0.10` **establishes nothing**, because `tail_s` is an upper
  bound; it is compatible with a saturated supervisor and with an idle one, and
  may not be cited as evidence that the supervisor was the bottleneck.

Run 1 does not fire the first branch (`tail_fraction ≤ 85.4%`, itself an upper
bound). A run that does fires it **without changing `R`, `W` or `B`**: its `m`
enters every one of them, with no discretion available. If a spread is driven
by a run that fired it, the report's reading is *these did not agree and at
least one of the means is a different quantity* — which is a stronger answer to
the open question than a bare disagreement, not a weaker one. It also bounds
V2: a `W` computed across two runs of which one fired this branch is a spread
between two different quantities, and `separated` may not be reported without
saying so.

**Secondary readouts that may never flip either verdict:** R4's median and
percentiles, R5, R6, R9, R11, R12. They are reported on every run. The median
delta is the obvious candidate for a second shot at the target and is
explicitly denied one: it describes the distribution, and both verdicts are on
the mean.

**No trend claim.** Five rollouts over three instances, two of them replicated,
is not a design that supports one. The report tabulates `(boundaries, m)` per
rollout and makes **no** regression, correlation, monotonicity or "scales with
event volume" claim. This is pre-registered precisely because such a story will
be available and tempting once five points exist — more available at five than
it was at three.

**The robustness verdict, per run, three values:**

- **`unattended-terminal`** — the rollout entry reached a terminal state with no
  human action after the command was issued (R8).
- **`attended`** — it reached a terminal state, but a human intervened; the
  report states what the intervention was.
- **`did-not-terminate`** — the wall clock or the harness ended it.

This is a per-run reading, reported as two words, never aggregated into a rate.
**Grading is scored separately from the rollout.** A grading failure or timeout
— foreseeable for P2, whose screening record lists 979 `pass_to_pass` tests
against `_UNIT_TEST_TIMEOUT_S = 1800.0` — does not touch the rollout's
robustness verdict and does not invalidate any of R1–R12, all of which are read
from the rollout attempt. R13 reports what grading did, as a reading, not as a
claim.

## 6. Failure handling, decided before it happens

**A failed rollout is a result by default.** The robustness question's answer
for an instance is allowed to be "no", and `did-not-terminate` is that answer,
not a broken run to be discarded. Anything else lets the excluded set grow in
the direction that flatters the result.

**The closed list of causes for which a replacement may be *requested*.** Per
§1, the planned 2 rollouts per instance already sit on the ask-first ceiling, so
nothing here authorizes a re-run by itself: a replacement needs the owner's
explicit go-ahead, and this list only says which failures may be taken to them.
A failure qualifies when it is positively identified as one of:

1. the instance's image is missing or its pull failed;
2. Docker itself is unreachable (`RUNBOOK.md` §1's exit-code-2 branch);
3. the host box failed — OOM of the machine, not of the container, or a reboot;
4. no model call succeeded at all: an absent or rejected credential, or a
   network failure that left `supervisor.jsonl` with zero rows.

Anything not on this list — including `TIMED_OUT`, `SUPERVISION_FAILED`, a
crash mid-run, a lapse rate of any size, and a rollout that produced no patch —
is a **result** and is reported. The list is closed; adding to it after seeing
a failure is the amendment this section exists to forbid.

**Whether a go-ahead was sought, and its answer, are reported either way** — a
replacement that was never asked for and one that was declined are different
facts, and both leave the instance at one usable rollout.

**`TIMED_OUT` inherits run 1's presumption.** A first-attempt timeout is
presumed ours, not the actor's, unless `claude_code.proxy_log.jsonl`'s own
timeline shows the actor actively working up to the wall clock
([`PREREGISTRATION.md` §7](../pipeline_end_to_end/PREREGISTRATION.md#7-failure-classification)).
A timeout that R10 and R11 show as the supervisor still catching up while the
actor sat finished is **the primary question's answer arriving as a failure**,
and the report reads it that way rather than as a lost run.

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
  computed, no grading outcome is evidence about supervision, and two rollouts
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
- **Not a measurement of judge latency.** Nothing instruments a per-call
  duration; §5's quantity check is what stands in for one, in one direction
  only.
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
`R`, `W` and `B`, the sentences each branch licenses, and the quantity check
that qualifies them** (§5), the failure handling and its closed cause list
(§6), the exclusions (§7).

**Not frozen:** the probe's witness script, so long as it computes §4's list and
nothing more; how the report presents the findings; and the prose explaining
why a verdict came out as it did, once the numbers exist.

**The one slot this document requires of the report that it does not itself
specify:** an *unanticipated findings* section, filled on every run, whose
entry is exactly `None observed.` when there is nothing to put there — the
convention run 1's report
[argues for and adopts](../pipeline_end_to_end/REPORT.md#7-unanticipated-findings).
Nothing in that section may close, weaken or strengthen V1 or V2.
