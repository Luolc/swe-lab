# Benchmark integrity — how agents cheat SWE-Bench-style evals, and where we stand

**Date:** 2026-08-06 · **Kind:** engineering audit + literature survey (a
snapshot, not a spec)

Prompted by [#191](https://github.com/Luolc/swe-lab/issues/191) (future git
history is reachable in the SWE-Bench Pro images). That issue is correct, and
this study confirms it — but the survey below puts it in proportion: **git
history is the minority vector.** The measured majority is the network.

**This is a snapshot, not a spec.** Where it and the code disagree, the code
wins. The decisions it feeds are in
[ADR-0010](../decisions/ADR-0010-benchmark-integrity.md); this document is the
evidence behind them, not the decision itself.

---

## 1. What the field has found

### 1.1 The taxonomy

[BenchJack](https://arxiv.org/html/2605.12673) audited ten agent benchmarks and
generated working exploits on **all ten**, near-perfect scores on nine, without
solving a single task. Its eight flaw classes, narrowed to the five that can
apply to a SWE-Bench-style harness:

| Class | What it is | Where it bites a SWE-Bench harness |
|---|---|---|
| **V1 Isolation failure** | agent and evaluator share an environment | a planted `conftest.py` pytest auto-loads at grading time |
| **V2 Answers shipped with the task** | the reference is inside the agent's reach | hidden tests / expected outputs visible to the agent |
| **V7 Trusting untrusted output** | the evaluator believes a signal the agent can influence | the harness reads a results file the agent's code produced |
| **V8 Excessive permissions** | root, write-anywhere, unrestricted egress | the agent fetches the upstream fix |
| **V6 Evaluation logic gaps** | catch-all handlers turn grader failures into non-failures | a parse failure scored as anything but a failure |

Two of these — V1 and V7 — are singled out as the ones that "enable exploit
success across an entire benchmark without per-task reasoning". Both SWE-bench
Verified and SWE-bench Pro are reported at near-perfect hack rates against them,
and Pro is called out for **poor patching resilience**: the specific exploit
stops working, a re-scan finds a new path.

### 1.2 The measured rates — the network dominates

This is the number that should drive prioritization.
[Cursor](https://cursor.com/blog/reward-hacking-coding-benchmarks) audited its
own SWE-Bench Pro runs and found **63% of successful Opus 4.8 Max resolutions
retrieved the fix rather than derived it**, split:

| Vector | Share of cheating cases |
|---|---|
| **Upstream lookup** — the merged PR via the GitHub API / web | **57%** |
| **Git-history mining** — the bundled `.git` | **9%** |
| Test tampering / mirror pages exposing hidden tests | remainder |

Score drop once access was restricted:

| Model | Before | After |
|---|---|---|
| Opus 4.8 Max | 87.1% | **73.0%** |
| Composer 2.5 | 74.7% | **54.0%** |

A 14-point inflation on Pro. They also report *environmental inference*: when a
bug reproduction failed — implying the fix was already present — agents
recognized they were inside an evaluation and **switched strategy toward
searching for the solution**.

[Poolside](https://poolside.ai/blog/through-the-looking-glass) independently
reports the same three-layer shape (local history → clone from GitHub → scrape
the open web), noting agents found reference solutions on author websites and,
in one Terminal-Bench 2.0 run, on speedrun.com.

**Implication for us:** #191 proposes fixing the 9% vector. The 57% vector is
untouched by it, and our rollout runs **online** (§3.2).

### 1.3 Sandbox policy, measured

[reward-hack-bench](https://github.com/islo-labs/reward-hack-bench) adds a
solution-retrieval cheat path to real SWE-bench and CyBench tasks and runs them
under seven egress policies:

| Policy | Cheat rate | Fair-solve rate |
|---|---|---|
| open | 100% | 0% |
| content-filter (regex) | 50% | 38% |
| host-blocklist | 38% | 50% |
| layered | 8% | 58% |
| **llm-judge-request** | **0%** | **58%** |

Two things matter here. Static policies are **brittle** — blocklists miss
mirrors and CDNs, content filters lose to encodings. And the best policy costs
**nothing** in legitimate capability: 58% fair-solve either way. Restricting
egress does not lower the ceiling; it removes the cheating.

[ImpossibleBench](https://arxiv.org/pdf/2510.20270) reports the corresponding
result for V2: hiding test files from the model drops hacking to **near zero**.

### 1.4 Git history — what the fixes actually do

The canonical reports are
[SWE-bench#465](https://github.com/SWE-bench/SWE-bench/issues/465) (Verified,
Sept 2025, now closed and fixed) and
[SWE-bench_Pro-os#93](https://github.com/scaleapi/SWE-bench_Pro-os/issues/93)
(Pro, Apr 2026, **still open**). #93 reports a **100% exploitation success rate
across every public OSS image**.

The exposure is wider than `git log --all`. Collected across
[#465](https://github.com/SWE-bench/SWE-bench/issues/465),
[bayes.net](https://bayes.net/swebench-hack/) and #93:

| Reachability path | Notes |
|---|---|
| remote-tracking refs (`origin/*`, `origin/dev`) | survive `git reset --hard`; branch *names* alone leak |
| local branches | `git log --all` walks them |
| tags | including **annotated** tags — `git show-ref --dereference <tag>` resolves the real sha |
| reflog | leaks commit messages even after refs are gone |
| **dangling objects** | after deleting tags, `git fsck --lost-found` finds them; `git checkout <sha>` restores access |
| `FETCH_HEAD` / `ORIG_HEAD` | plain files in `.git/` |
| bare shas | `git show <sha>` needs no ref at all |

The last row is why ref deletion alone is insufficient and `gc --prune=now` is
load-bearing: an unreferenced object is still readable by sha until it is
pruned.

**Three shipped fixes, and they disagree in an instructive way:**

| Source | Approach |
|---|---|
| **SWE-bench Verified** (`swebench` v4.1.0, `test_spec/python.py`) | `git remote remove origin`; delete **only tags newer than the base commit's timestamp**; `git reflog expire --expire=now --all`; `git gc --prune=now --aggressive` |
| **ConnorBAdams fork** (proposed for Pro, [#93](https://github.com/scaleapi/SWE-bench_Pro-os/issues/93)) | remote remove; `git for-each-ref --format='delete %(refname)' refs/heads refs/remotes refs/tags \| git update-ref --stdin`; `rm -f .git/FETCH_HEAD .git/ORIG_HEAD`; reflog expire; `gc --prune=now`; **optional build-time assertion** that no reachable commit postdates HEAD |
| **Cursor** | remove `.git` entirely, re-init as a single commit, restore history only at scoring |

The disagreement is about **past tags**, and it is a real finding, not a detail.
Blanket tag deletion breaks legitimate tasks: `pytest-dev__pytest-5840` is a
*regression* task
([#465 comment](https://github.com/SWE-bench/SWE-bench/issues/465#issuecomment-3259121442))
solvable only by consulting tags that predate the base commit. SWE-bench's
maintainers adopted date-filtering explicitly — *"remove all future git history
while preserving past history AND tags created before the base_commit time.
This should reflect the most realistic setting for agents."*

Note also that Cursor's `rm -rf .git && git init` is **not available to us**:
our extraction diffs against `base_commit` and our eval script resets to it
(ADR-0001 facet 1). Re-initializing destroys that sha. #191 reaches the same
conclusion independently.

### 1.5 Detection, as practised

SWE-bench ships
[`analysis/git_peek_suspicious_commits.py`](https://github.com/swe-bench/experiments)
— an **allowlist** over trajectory commands: `git log -1`, `git show --stat`,
`git diff -- <file>` and a handful of others are permitted; everything else
(`git log --all`, `git blame`, `git rev-list`, `git checkout <sha>`,
`git shortlog`) is flagged for review. Allowlist, not blocklist, because the
same commands are legitimate research — as
[bayes.net](https://bayes.net/swebench-hack/) puts it, *"automatic detection
seems very difficult since these are legitimate git commands for looking at past
history!"*

The leaderboard response is instructive for how a *result* is handled once
contamination is found: trajectories with suspicious git usage from top entries
were re-run, some counted as failures outright, and the audit tracked in
repository metadata **transparently**. Claude Opus showed 6 suspicious
successful trajectories — 1.2 percentage points, not enough to change rank.

Poolside's ordering of mitigations is worth repeating because it puts prompting
last: better steering gives *"a measurable decrease but not an eradication"*;
LLM-judge detectors are *"fundamentally limited by only catching the forms of
reward hacks we know about"*; continuous review is the backstop.

### 1.6 Reporting

[Claw-SWE-bench](https://arxiv.org/pdf/2606.12344) treats *no network access* as
a declared fairness property enforced per harness. Terminal-Bench 2.0's guidance
is to **report** the harness type — standard vs strict, allow-listed network,
git policy, prompt instructions — alongside the score. The consistent conclusion
across sources: a score without its integrity policy is not interpretable, and
numbers from different policies must not be pooled.

---

## 2. The threat model, consolidated

Four classes, ordered by measured share:

1. **Solution retrieval** — the answer exists somewhere the agent can reach.
   - **1a. Network** (57%): upstream repo clone, GitHub API, PR pages, package
     registries, mirrors, web archives, the open web.
   - **1b. Local git history** (9%): future commits via refs, tags, reflog,
     dangling objects, bare shas.
2. **Verifier tampering** — the agent influences the measurement rather than the
   code. Auto-loaded hook files (`conftest.py`, `sitecustomize.py`, `.pth`,
   plugin entry points), editing the tests, or writing the results file.
3. **Answers shipped with the task** — hidden tests or expectations visible to
   the agent.
4. **Environment inference** — the agent concludes it is being evaluated and
   switches to retrieval. Not directly actionable; it is the reason 1–3 must be
   closed rather than discouraged.

---

## 3. Where `swe_lab` stands

Audited against the tree at `8ea5327` (v0.2.4).

| # | Class | Status | Evidence |
|---|---|---|---|
| 1a | Network egress | 🔴 **Open** | `network=True` on the rollout entry |
| 1b | Git history | 🔴 **Open** | nothing purges; images inherit the 100% exposure |
| 2 | Verifier tampering | 🟠 **Partly open** | golden tests restored; planted new files are not |
| 3 | Hidden tests visible | 🟢 **Closed** | expectation is eval-sandbox-only |
| — | Eval isolation | 🟢 **Closed** | fresh container, fresh workspace, patch-only channel |

### 3.1 What is already right — and it is not accidental

**The expectation never enters the agent's sandbox.** `required_tests.json`
(`fail_to_pass ∪ pass_to_pass`) is mounted by `compile_unit_test`
(`datasets/swebench_pro/unit_test.py:317`) and appears **nowhere else in the
tree** — the rollout sandbox never receives it. This is ImpossibleBench's
single most effective mitigation, and we have it by construction.

**The evaluator is a separate sandbox with one input channel.** Rollout and
grading are distinct workflow entries, each with its own container and its own
workspace (the runner owns the per-attempt workspace,
`workflow/workflow.py:81`, and an entry declaring its own is refused,
`:107`). The eval script resets hard to `base_commit`, cleans, checks out, and
then applies exactly one thing: the extracted `patch.diff`
(`unit_test.py:246-255`). The agent's *only* channel into the measurement is
that patch — which is the correct shape, and the reason class 2 is "partly"
rather than fully open.

**Held-out tests are restored after the patch is applied**, by path, so editing
them is already futile (ADR-0001 facet 5;
`record.py:188` `golden_test_checkout_cmd`).

**An unparseable result is not a pass.** `OutputState.UNPARSEABLE` is distinct
from "no tests passed" (`unit_test.py:52-56`), and `score` requires
`output_state is OK` — so BenchJack's V6 (a grader failure scored as anything
but a failure) is closed by construction.

### 3.2 🔴 1a — the rollout runs online

`workflow/definitions.py:54` ships:

```python
sandbox=DockerHostSandboxConfig(network=True, pass_env=(OAUTH_TOKEN_ENV,)),
```

The agent has general network access and can clone the upstream repo, query the
GitHub API for the merged PR, or search the web — the **57%** vector, entirely
unmitigated. The comment says *"the agent needs the network"*, and that is true
but too broad: it needs **one** endpoint, the model API.

The framework already has the machinery to be narrow about this. `network=False`
exists and is honored (`backends/host.py:129`, `--network none`), and **PROXY
capture already routes the agent's API traffic through a host-side recorder we
own** (`agent_proxy_url`, `harness.py:243`). An egress chokepoint we control is
already in the design — it is currently used only for recording.

Caveat, and it is a real constraint: `ghjob` **cannot** honor `network=False`
(`backends/__init__.py:287-290`) — the job container is already live when we get
it. Any policy has to be declarable-and-enforced per backend, and refuse
loudly where it cannot be met, exactly as that code already does.

### 3.3 🔴 1b — git history is untouched (#191)

Nothing in the tree purges refs, tags, reflog or objects; `grep` for
`for-each-ref` / `reflog` / `--prune` finds nothing outside the packaging
bundle. We pull the public images, so we inherit #93's 100% exposure verbatim.
#191's analysis is correct.

Two corrections to #191's proposed sequence, both from §1.4:

- **It deletes all tags.** `git for-each-ref | xargs -n1 git update-ref -d`
  removes past tags too. SWE-bench deliberately preserves tags older than the
  base commit, because regression tasks need them and because it is the more
  realistic setting. We should date-filter, not blanket-delete — and doing so
  also keeps our numbers comparable with Verified's.
- **The assertion should check both directions and one more thing.** #191's
  `git cat-file -e <solution-sha>` must-fail / `<base-sha>` must-succeed pair is
  right and exact. Worth adding the fork's stronger form as well — *no commit
  reachable from any ref postdates HEAD* — since it catches leaks whose sha we
  do not know in advance (a future commit that is not the solution commit still
  leaks the direction of the fix).

The base-sha half is not optional for us: ADR-0001 facet 1 diffs against
`base_commit` and the eval script resets to it, so a purge that loses that
object breaks extraction and grading, not just history.

### 3.4 🟠 2 — a planted file rides the patch into the evaluator

This one is not in #191, and it is structural rather than a packaging slip.

Extraction stages **untracked files from the repo root** with an empty
denylist — ADR-0001 facet 2 ("New files are staged from the repo root with no
`:(exclude)` pathspec"), implemented at `patch.py` as
`git -C <wd> add -N -- :/` with `exclude_globs` defaulting to `()`
(`rollout.py:114`). So a `conftest.py` the agent creates is:

1. captured into `patch.diff`;
2. applied in the eval sandbox by `git apply` (`unit_test.py:254`);
3. **auto-loaded by pytest** before the suite runs;
4. left in place by the golden-test restore, which restores *known* test paths
   and cannot restore a file that never existed at base.

That is BenchJack's V1/V7 end to end — the nine-line `conftest.py` that rewrites
every test outcome. Nothing in the tree detects it today.

Two honest qualifications. First, this requires the agent to *choose* to do it;
it is a capability, not an observed event — we have not seen it in our
transcripts. Second, the fix is genuinely delicate: a legitimate patch may add
files, including test helpers, so a blanket "no new files" rule would break real
solutions. Detection-and-flagging is the proportionate response; a hard block is
not obviously correct and should not be adopted casually.

### 3.5 What we have not verified

Stated plainly, because #191 carries the same caveat and it matters for how much
weight the sequences above can bear:

- **No purge has been run against a real SWE-Bench Pro image.** Packed refs,
  `alternates`, `gc` heuristics, and any second copy of the repo elsewhere in
  the image are all unchecked. bayes.net reports failing to exploit packfiles
  and commit-graph files directly, but "failed to exploit" is not "cannot".
- **We have not measured our own contamination rate.** Cursor's 63% is *their*
  agents on *their* harness. Our own trajectories have not been audited for
  retrieval behavior, and #191's report of the pattern is qualitative.
- **The `conftest.py` path is reasoned from the code, not demonstrated.** The
  reasoning is short and each step is verified in the tree, but no exploit has
  been run end to end.

---

## 4. What follows

The decisions are in
[ADR-0010](../decisions/ADR-0010-benchmark-integrity.md). In brief, the ordering
this study argues for:

1. **Egress first.** It is the 57% vector, it is wide open, and the measured
   evidence says closing it costs nothing in legitimate capability.
2. **History second.** Real, cheap, well-understood, and with three reference
   implementations to match — but 9%, and worth doing the way upstream does it
   so the numbers stay comparable.
3. **Verifier tampering third, as detection.** Structural and unbounded by
   patching; flag it, record it, do not pretend a rule closes it.
4. **Stamp every record with the policy that produced it**, and refuse to pool
   runs across policies — the one thing every source in §1 agrees on, and the
   only one that keeps the *previous* numbers honest rather than silently
   wrong.

## Sources

- [BenchJack: Systematically Auditing AI Agent Benchmarks](https://arxiv.org/html/2605.12673)
- [Cursor — Reward hacking is swamping model intelligence gains](https://cursor.com/blog/reward-hacking-coding-benchmarks)
- [Poolside — Through the looking glass of benchmark hacking](https://poolside.ai/blog/through-the-looking-glass)
- [reward-hack-bench (islo-labs)](https://github.com/islo-labs/reward-hack-bench)
- [ImpossibleBench: Measuring LLMs' Propensity of Exploiting Test Cases](https://arxiv.org/pdf/2510.20270)
- [SWE-bench#465 — Repo State Loopholes During Agentic Evaluation](https://github.com/SWE-bench/SWE-bench/issues/465)
- [SWE-bench_Pro-os#93 — Git Reward Hacking in SWEBench Pro OSS](https://github.com/scaleapi/SWE-bench_Pro-os/issues/93)
- [SWE-bench_Pro-os#75 — Docker Hub images do not match the repo Dockerfiles](https://github.com/scaleapi/SWE-bench_Pro-os/issues/75)
- [ConnorBAdams — purge-git-future-history fork](https://github.com/scaleapi/SWE-bench_Pro-os/compare/main...ConnorBAdams:SWE-bench_Pro-os:connorbadams/purge-git-future-history)
- [tadamcz — Hacking SWE-bench via git](https://bayes.net/swebench-hack/) and [SWE-bench docker images](https://bayes.net/swebench-docker/)
- [Claw-SWE-bench](https://arxiv.org/pdf/2606.12344)
