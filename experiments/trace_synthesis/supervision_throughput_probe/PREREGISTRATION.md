# Pre-registration: does 6.58 s per boundary transfer?

> **Frozen before any run.** Everything below is protocol, fixed at the commit
> that adds this file. **Nothing has been run.** No rollout for either instance
> named in §2 exists at the time of writing, and this document changes nothing
> that executes — it adds one directory containing one Markdown file.

The first end-to-end supervised rollout measured **6.58 s per boundary** and
left the question of whether that number belongs to the pipeline or to that one
instance explicitly open
([`REPORT.md` §8](../pipeline_end_to_end/REPORT.md#8-open-questions), first
bullet). This probe is two more instances, one rollout each, asked at that
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
lands anywhere near run 1's.

**Secondary — does the pipeline run unattended on an instance it was not set up
for?** Run 1's instance was chosen, pre-flighted, and had its image proven. The
secondary question is whether a supervised rollout reaches a terminal state
without a human touching it on an instance that got none of that attention.
This is robustness, not effect.

**A limitation of the design, stated here rather than in the report that would
otherwise discover it.** Two instances at one rollout each cannot separate
*instance-to-instance* variation from *run-to-run* variation of the same
instance, and the quantity being measured is dominated by latency of a model
call over a network — which is not obviously stable from run to run. So a
**disagreement** among the three numbers is ambiguous by construction: it is
compatible with "instances differ" and with "any one instance differs from
itself." Nothing in this budget resolves that; buying replication instead
(one instance, two rollouts) would measure run-to-run variance and answer the
transferability question not at all, and the budget does not buy both. The
consequence is written into §4's verdicts rather than left for prose: the
probe's disagreement branch does **not** name a cause, and the follow-up it
recommends is within-instance replication.

## 1. Scale — the hard cap

**Frozen: 2 instances × 1 rollout = 2 rollouts.**

`AGENTS.md`'s ask-first boundary is more than 10 SWE-bench Pro instances **or**
more than 2 rollouts per instance; 2 × 1 is inside it, so this probe needs no
additional permission.

**The one exception, and its ceiling.** A rollout that fails for one of the
named infrastructure causes in §6 may be re-run **once** on the same instance,
with a fresh rollout id. The absolute ceiling is therefore **4 rollouts, never
more than 2 on one instance** — which is exactly where the ask-first line sits,
so the ceiling grants nothing the repository has not already permitted and
nothing is available past it. A third rollout on any instance, a third
instance, or a control arm requires a **new pre-registration**, written before
it runs; this one may not be amended to allow them.

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
matching the ids exactly (`load_dataset('swebench_pro')`, `n = 731`), with
image references `jefzda/sweap-images:navidrome.navidrome-navidrome__navidrome-5001518260732e36d9a42fb8d4c054b28afab310`
and `jefzda/sweap-images:qutebrowser.qutebrowser-qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac780366`
respectively, derived the way
[`RUNBOOK.md` §1](../pipeline_end_to_end/RUNBOOK.md#1-pre-flight--re-check-every-item-at-launch-time-never-trust-a-prior-pass)
derives one. Whether either image is **present on the box** is a launch-time
check, delegated to that same checklist and deliberately not asserted here.

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
directory `<root>/rollout/a0/` unless stated otherwise.

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
| R14 | corpus provenance | sha256 and byte size of every collected artifact, and the `main` commit at run time — the obligation [`WITNESS.md`](../pipeline_end_to_end/WITNESS.md) discharges for run 1 |

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

Let `m_1 = 6.58` (run 1, measured), and `m_P1`, `m_P2` be R3 for the two probe
runs. All three are constructed the same way — `span_s / boundaries`, dividing
by the row count and not by `boundaries − 1` — because the number under test is
run 1's, and a differently-constructed mean would not be a test of it.

**Primary verdict, no free parameters at analysis time.**

    R = max(m_1, m_P1, m_P2) / min(m_1, m_P1, m_P2)

- `R ≤ 2` → **`consistent-within-2x`**
- `R > 2` → **`not-consistent-within-2x`**

There is no third value and no "inconclusive" branch — an escape hatch here is
the whole of what this section exists to prevent. If a probe run produced no
usable R1/R2 at all, §6 decides whether it is a re-run or a result, and the
verdict is computed over whichever runs remain, with the count stated (`R over
2 runs`, not `R` unqualified).

**What each verdict licenses, frozen with the threshold:**

- `consistent-within-2x` licenses exactly one sentence: *on these three
  instances, per-boundary supervision cost lay in `[min, max]` s.* It does
  **not** license "6.58 s per boundary holds", and it does not license
  extrapolating a headroom figure from any single one of the three.
- `not-consistent-within-2x` licenses: *6.58 s per boundary is not a
  transferable constant; any downstream figure derived from it must carry the
  observed range in place of the point.* It does **not** name a cause — see §0
  on why this design cannot attribute a disagreement.

**Why 2, and why the threshold is not tunable.** The factor is a round order-of-
magnitude-free choice made while exactly one measurement exists, so it cannot
have been fitted to the spread it will judge — there is no spread yet. It is
also the coarseness the downstream consequence can absorb: §7a's headroom
figure (`3600 / m` boundaries) moves by the same factor, and a factor of 2 in
headroom is the difference between "run 1's 170 boundaries used a third of the
budget" and "used two thirds", which is a planning input either way. A factor
of 4 would not be.

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
bound). A run that does fires it **without changing `R`**: its `m` still enters
the ratio, with no discretion available. If `not-consistent-within-2x` is driven
by a run that fired it, the report's reading is *the three did not agree and at
least one of the three means is a different quantity* — which is a stronger
answer to the open question than a bare disagreement, not a weaker one.

**Secondary readouts that may never flip the primary verdict:** R4's median and
percentiles, R5, R6, R9, R11, R12. They are reported on every run. The median
delta is the obvious candidate for a second shot at the target and is
explicitly denied one: it describes the distribution, and the verdict is on the
mean.

**No trend claim.** With three points and one rollout each, the report tabulates
`(boundaries, m)` per run and makes **no** regression, correlation, monotonicity
or "scales with event volume" claim. That is pre-registered here precisely
because such a story will be available and tempting once three points exist.

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

**The closed list of re-runnable infrastructure causes.** A rollout may be
re-run **once**, on a fresh rollout id, only when its failure is positively
identified as one of:

1. the instance's image is missing or its pull failed;
2. Docker itself is unreachable (`RUNBOOK.md` §1's exit-code-2 branch);
3. the host box failed — OOM of the machine, not of the container, or a reboot;
4. no model call succeeded at all: an absent or rejected credential, or a
   network failure that left `supervisor.jsonl` with zero rows.

Anything not on this list — including `TIMED_OUT`, `SUPERVISION_FAILED`, a
crash mid-run, a lapse rate of any size, and a rollout that produced no patch —
is a **result** and is reported. The list is closed; adding to it after seeing
a failure is the amendment this section exists to forbid.

**`TIMED_OUT` inherits run 1's presumption.** A first-attempt timeout is
presumed ours, not the actor's, unless `claude_code.proxy_log.jsonl`'s own
timeline shows the actor actively working up to the wall clock
([`PREREGISTRATION.md` §7](../pipeline_end_to_end/PREREGISTRATION.md#7-failure-classification)).
A timeout that R10 and R11 show as the supervisor still catching up while the
actor sat finished is **the primary question's answer arriving as a failure**,
and the report reads it that way rather than as a lost run.

**Re-running destroys evidence unless a fresh rollout id is used.** A
non-`--resume` invocation deletes the prior attempt's output directory outright
([`RUNBOOK.md` §3](../pipeline_end_to_end/RUNBOOK.md#3-if-it-fails--read-this-before-doing-anything)).
Any re-run under this section uses a new rollout id, and both runs' corpora are
kept and reported.

**Launch procedure is run 1's.**
[`RUNBOOK.md`](../pipeline_end_to_end/RUNBOOK.md) is the launch procedure, and
its pre-flight is re-run at launch for each probe instance with that instance's
id substituted at the two places it names an instance. This document does not
copy it.

## 7. What this probe does not measure or claim

- **Not an effect estimate.** No sentence in the report may say supervision
  helped or hurt. This probe has no control arm and does not run
  `control_rollout_and_unit_test`.
- **Not a resolve rate.** R13 is two counts from two runs, reported per run. No
  `resolved N/M` is computed, and neither run's grading outcome is evidence
  about supervision.
- **Not a generalization.** `n = 3` instances, one rollout each, all from
  [issue #261](https://github.com/Luolc/swe-lab/issues/261)'s mixed-outcome 40,
  all with `verdict == "good"` and a proven image. The strongest available
  conclusion is *whether these three agree within a factor of 2* — not that any
  figure holds for SWE-bench Pro, for this pipeline, or for a batch.
- **Not an attribution of any disagreement.** See §0: instance-to-instance and
  run-to-run variation are confounded by the design.
- **Not a measurement of judge latency.** Nothing instruments a per-call
  duration; §5's quantity check is what stands in for one, in one direction
  only.
- **Not a re-closing of the seven acceptance points.** Those closed on run 1
  ([`REPORT.md` §1](../pipeline_end_to_end/REPORT.md#1-the-seven-points)). R7
  re-checks Assertion A because the throughput reading needs the deliveries to
  have been real, not to re-litigate point 3.
- **Not a cause for the lapses.** R5 records counts, classes and cursors. Run 1
  left the "clusters after cursor 87" question open on purpose; two more runs
  are tabulated beside it and no explanation is offered from three points.

## 8. What may still change

**Frozen:** the scale and its ceiling (§1), the selection rule and the two
instance ids (§2), the fixed configuration and the drift check (§3), the
readout list (§4), the verdict rule with its threshold, its licensed sentences
and its quantity check (§5), the failure handling and its closed cause list
(§6), the exclusions (§7).

**Not frozen:** the probe's witness script, so long as it computes §4's list and
nothing more; how the report presents the findings; and the prose explaining
why a verdict came out as it did, once the numbers exist.

**The one slot this document requires of the report that it does not itself
specify:** an *unanticipated findings* section, filled on every run, whose
entry is exactly `None observed.` when there is nothing to put there — the
convention run 1's report
[argues for and adopts](../pipeline_end_to_end/REPORT.md#7-unanticipated-findings).
Nothing in that section may close, weaken or strengthen a §5 verdict.
