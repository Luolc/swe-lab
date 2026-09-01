# Handmade instance — REPORT

| Field | Value |
| --- | --- |
| Author | swelab-handmade-impl (Claude Opus 5) |
| Task | trace-synthesis [task 01](../../../docs/trace-synthesis/plans/task-01-one-instance-end-to-end.md), steps 0–4 (step 5 pending) |
| Design | [`README.md`](README.md) |
| Corpus | SWE-bench Pro (public test split, 731 rows) |
| Actor harness | `claude_code` (Claude Code 2.1.212), default `STREAM` capture |
| Actor model | `claude-sonnet-5` (workflow default); `claude-haiku-4-5-20251001` for the CLI's own side calls |
| Box | 4 vCPU / 15 GB linux-x64 dev workstation |
| Started | 2026-09-01 01:07 PDT |
| Last updated | 2026-09-01 01:23 PDT |

**Scope of this round.** Steps 0–4: candidate selection, environment validation,
harvesting one genuine failure, freezing it, and the Oracle's guidebook
(written by `swelab-orchestra`, [`guidebook/openlibrary-from-isbn.md`](guidebook/openlibrary-from-isbn.md)).

**Step 5 — the steered re-run — is pending**, gated on
[task 02](../../../docs/trace-synthesis/plans/README.md) settling the injection
shape. So this report answers nothing about whether hints actually steer an
actor; it establishes the failure they would be steering *from*.

## Contents

- [Conclusions](#conclusions)
- [Method](#method)
- [Step 1 — the gold gate](#step-1--the-gold-gate)
- [Step 2 — harvesting a failure](#step-2--harvesting-a-failure)
- [Step 3 — the freeze](#step-3--the-freeze)
- [Step 4 — the guidebook, and why this instance failed](#step-4--the-guidebook-and-why-this-instance-failed)
- [Cost](#cost)
- [Open questions](#open-questions)

## Conclusions

1. **All four candidates pass the gold gate** on this box — none dropped. The
   gate still earned its place: it caught a *worktree* fault (below) that would
   otherwise have burned four rollouts.
2. **A genuine failure was harvested on the first sample.** openlibrary rollout
   0 exited `2` after 125 s, with the run itself healthy —
   `agent_complete: 1.0`, agent exit `0`, no timeout, a non-empty 105-line
   patch, `git_history_clean: 1.0`, the integrity verifier unflagged. This is a
   reasoning failure, not infrastructure.
3. **The failure is deterministic, not flaky.** Three grading attempts within
   the run, and a second independent rollout, all produced the *identical*
   verdict: 25 required tests, 9 passed, the same 16 failed. Issue #261's
   environment-sensitivity caveat does not bite this instance.
4. **The freeze holds.** After a second rollout of the same instance — which
   demonstrably rmtree'd the original cache directory — all 99 files of the
   frozen tree are byte-identical by sha256. Step 3's acceptance is met.
5. **Worth flagging for later selection work:** this instance is 1/2 resolved
   in issue #261 but **0/2 here**. `n=2` supports no claim about its true
   pass-rate; it is a caution against reading #261's ratios as portable to this
   box, not evidence that they are wrong.
6. **The failure is a placement error, not an algorithm error**, and the agent
   could not have caught it with the tests it had — they are installed only at
   grading time. See [step 4](#step-4--the-guidebook-and-why-this-instance-failed).
   That makes it good raw material *and* raises a caveat about how much of this
   corpus fails this way for reasons a hint would have to work around rather
   than through.

## Method

Per [`README.md`](README.md). In short: four candidates from
[issue #261](https://github.com/Luolc/swe-lab/issues/261), each gated on its
gold patch resolving *on this box* before any rollout is spent; then
`rollout_and_unit_test` samples in wall-time order until one exits `2`; then an
immediate copy out of `.cache/`.

The exit code, not a log read, is the acceptance criterion
(`src/swe_lab/cli/run.py`, `ExitCode`): `2` is a run that completed and graded
as unresolved — a reasoning failure, the raw material we want. `1` is a task or
edge that failed, or a refused run — infrastructure, and harvesting it would
have the Oracle write a guidebook against a problem the actor never had.

### Environment — two gaps that cost the first sweep

Both halves of the repo's own gitignore policy bite a fresh worktree, and the
first gold sweep exited `1` on **all four** candidates because of the second:

1. `.envrc.local` does not travel with a worktree; copied from
   `.envrc.local.example` + `direnv allow`.
2. `datasets/swebench_pro/data/` does not either; re-downloaded per
   [the dataset README](../../../datasets/swebench_pro/README.md).

The symptom of (2) is misleading — a `FileNotFoundError` on the dataset
directory surfaces as exit `1` per instance, which reads as "every candidate is
broken". Written up as a hazard in
[`docs/conventions.md`](../../../docs/conventions.md#hazards-learned-the-hard-way)
so the next task pair does not re-derive it.

This is also the first live demonstration of why step 1 exists: an environment
fault that would have burned four rollouts cost four cheap test runs instead.

## Step 1 — the gold gate

`gold_check.sh`, sequential, 2026-09-01 01:07–01:17 PDT. Raw:
[`runs/gold/summary.jsonl`](runs/gold/summary.jsonl) plus one full CLI log per
instance.

| instance | gold exit | wall (s) | verdict |
| --- | ---: | ---: | --- |
| `…openlibrary-5de7de19…` | 0 | 87 | resolves — kept |
| `…ansible-c1f2df47…` | 0 | 59 | resolves — kept |
| `…navidrome-50015182…` | 0 | 148 | resolves — kept |
| `…vuls-4c04acbd…` | 0 | 299 | resolves — kept |

**All four survive; none is dropped.** Every gold run reported
`unit_test.resolved: 1.0` with every required test passing and
`unit_test.missing: 0.0`.

Wall times here are **not** comparable to issue #261's rollout wall times: no
agent runs in a gold check, and these numbers are dominated by the first-time
Docker image pull (`sandbox.pull_seconds` is the bulk of each). They rank
image-pull cost on a cold box, nothing more.

## Step 2 — harvesting a failure

`harvest.sh`, on openlibrary (the fastest survivor). Raw:
[`runs/rollouts.jsonl`](runs/rollouts.jsonl) + a full CLI log per sample.

| rollout | started (PDT) | wall (s) | exit | verdict |
| ---: | --- | ---: | ---: | --- |
| 0 | 01:17:22 | 125 | `2` | unresolved — **harvested** |
| 1 | 01:20:27 | 166 | `2` | unresolved (durability probe, see step 3) |

**Sample 0 is a genuine reasoning failure**, and the run record says so rather
than a log read:

| metric | value | reads as |
| --- | ---: | --- |
| `rollout` entry status | `succeeded` | the phase ran |
| `agent_complete` | 1.0 | the agent finished on its own |
| `claude_code.exit_code` | 0 | it did not crash |
| `claude_code.timed_out` | 0 | it did not hit the wall clock |
| `patch_is_empty` | 0 | it produced work (105 lines) |
| `git_history_clean` | 1.0 | the future-commit purge held |
| `verifier.flagged` | 0 | the integrity verifier saw nothing |
| `unit_test.required` / `passed` / `missing` | 25 / 9 / 16 | it graded, and lost |
| `unit_test.resolved` | 0.0 | → exit `2` |

### What the failure looks like

All 16 failures land on the three interfaces the task asked for —
`get_isbn_or_asin`, `is_valid_identifier`, `get_identifier_forms`
(`openlibrary/tests/core/test_models.py::TestEdition`) — while the 9 pre-existing
tests keep passing. The agent wrote all three as **module-level functions** in
`openlibrary/core/models.py` and rewrote `Edition.from_isbn()` to call them, then
closed with:

> Verified the three helpers produce exactly the outputs specified in the
> requirements (including case-insensitive ASIN, empty-string handling) and
> confirmed existing `test_models.py`/`test_vendors.py` suites still pass.

— a confident sign-off attached to a patch that resolves nothing. The shape of
the run: 21 tool calls (13 `Bash`, 6 `Read`, 2 `Edit`), 88.9 s of agent wall time.

Diagnosing *why* is phase B's job and deliberately not done here.

### Determinism

The `unit_test` entry ran 3 attempts (`a0`/`a1`/`a2`); all three returned 25
tests with the same 16 failing. Rollout 1, an independent agent run, produced
the same 16 failures on the same three methods. So the verdict is stable under
both re-grading and re-rolling.

### On wall times

125 s and 166 s here against issue #261's 510 s mean for this instance. Different
box and configuration — exactly what that issue's "wall times are
environment-relative" caveat warns about. Our numbers rank nothing against #261;
they only say the harvest loop is cheap *here*.

## Step 3 — the freeze

`harvest.sh` calls `freeze.sh` itself the moment a sample exits `2`, so the copy
happens inside the same process rather than depending on a human issuing it
before the next command. The hazard it defends against is real and was observed
directly: while rollout 1 was running, the cache directory
`.cache/runs/rollout_and_unit_test/<instance_id>/` held only a fresh `rollout/` —
sample 0's `unit_test/`, `edges/` and `store/` were gone.

**Frozen at** (absolute, gitignored — the tree is not in the repo):

```
/home/ubuntu/dev/swe-lab-handmade/experiments/trace_synthesis/handmade_instance/frozen/failure-rollout-0/
```

1.4 MB, 99 files. What phase B needs is all present:

| artifact | path in the frozen tree |
| --- | --- |
| conversation, raw | `rollout/a0/claude_code.event_stream.jsonl` (94 records) |
| conversation, typed | `rollout/a0/conversation.json` |
| the agent's patch | `rollout/a0/patch.diff` (+ `patch.raw.diff`) |
| unit-test verdict | `unit_test/a2/unit_test.output.json` (per-test PASSED/FAILED) |
| run record | `store/adhoc/<instance_id>/r0/workflow.json` |
| integrity | `rollout/a0/git_integrity.json`, `rollout/a0/verifier.json` |
| the actor's prompt | `rollout/ws/a0/prompt.md` |
| cost / model / turns | the `result` event in `claude_code.event_stream.jsonl` |
| provenance | `PROVENANCE.json` — instance id, rollout id, exact command, git commit, capture mode |

### Acceptance: does it survive the next run?

Checked by content, not by inspection. Before rollout 1, a sha256 manifest of
all 99 files was written to
[`runs/frozen-manifest-before.txt`](runs/frozen-manifest-before.txt); after
rollout 1 completed, the same manifest was regenerated into
[`runs/frozen-manifest-after.txt`](runs/frozen-manifest-after.txt).

```
INTACT: all 99 files byte-identical after a second rollout of the same instance
```

**Rollout 1 is a durability probe, not a second harvest.** It was run because
step 3's acceptance names exactly this check. Its own output also exited `2` and
`harvest.sh` froze it too, so `frozen/failure-rollout-1/` (1.8 MB) exists as a
free byproduct; step 4 should use **rollout 0** unless orchestra decides
otherwise.

## Step 4 — the guidebook, and why this instance failed

The guidebook is [`guidebook/openlibrary-from-isbn.md`](guidebook/openlibrary-from-isbn.md),
written by `swelab-orchestra` playing the Oracle with the privileged access
[spec phase B](../../../docs/trace-synthesis/spec.md#phase-b--the-oracle) grants:
the frozen conversation, the gold patch, the gold test patch, and the repo. Five
stages, each carrying the `justification` field the spec requires.

The diagnosis below is orchestra's; every claim in it was **re-verified against
the frozen artifacts and the dataset row** before being written here, and one
was found wrong — see [the correction](#a-correction-to-the-stage-2-justification).

### The mechanism

The agent's three functions are behaviorally near-identical to gold. The
difference is **where they live**:

| | placement |
| --- | --- |
| gold patch | `@staticmethod` inside `class Edition` |
| agent patch | module-level functions in `openlibrary/core/models.py` |

All 16 grading tests reach them as `e.get_isbn_or_asin(...)` — through an
`Edition`. Module-level definitions are unreachable by that path, so all 16 fail
and the 9 pre-existing tests pass. **A placement error, not an algorithm error.**

### Why the agent could not see it

**The grading tests are not in the working tree during the rollout.** Verified
two independent ways from the frozen trace:

- **Tool call 8** — `cat /app/openlibrary/tests/core/test_models.py`. The
  captured `tool_result` contains none of `get_isbn_or_asin`,
  `is_valid_identifier`, `get_identifier_forms`; it defines 9 tests, none of them
  the graded ones.
- **Tool call 19** — `pytest test_models.py test_vendors.py` returns
  `collected 24 items`, with `test_models.py .........` (9 dots) and
  `24 passed in 0.35s`.

The mechanism is in the dataset row's `before_repo_set_cmd`, which runs at
**grading** time:

```sh
git reset --hard 5f7d8d19…          # base_commit
git clean -fd
git checkout 5f7d8d19…
git checkout 5de7de19… -- openlibrary/tests/core/test_models.py   # the graded tests, from the solution sha
```

So the rollout sees the repo at `base_commit` and the graded tests arrive only
afterwards. The agent searched for them (tool calls 5, 7, 8) — this is not
laziness; they genuinely were not there.

Worse, its self-check was **self-consistent with its own mistake**: tool call 18
runs `from openlibrary.core.models import get_isbn_or_asin, …`, which succeeds
*because* they are module-level. It then signed off with a green suite that
exercised none of its new code.

This is structural to the corpus, not particular to this instance, and it is why
the guidebook's stage 5 is about knowing what a green suite cannot tell you.

### The stage-2 contradiction, found and fixed

Verifying the guidebook against the dataset row turned up one claim that did not
hold. The **first** version of stage 2 said the `interface` block "gives a path,
it does not give a container" and is "silent about the container". It is not
silent. The row's `interface` field says, for all three units:

```
- Type: Function
- Name: get_isbn_or_asin
- Path: openlibrary/core/models.py
```

while `requirements` says "**The method** `get_isbn_or_asin(...)`" three times.
**The task statement contradicts itself**, and the interface half points at the
placement the agent chose. So the agent's patch was a defensible reading of a
self-contradictory spec, not carelessness.

This mattered because stage 2 is load-bearing and its justification would not
have survived contact with the actor: an actor told "the interface block is
silent" can re-read it in one command and find otherwise, and a hint that loses
that argument is worse than no hint. The spec's `justification` field exists to
force exactly this check
([phase B](../../../docs/trace-synthesis/spec.md#phase-b--the-oracle)).

Reported to orchestra, which **revised the guidebook**: stage 2 now puts the two
vocabularies side by side, states that they disagree, and resolves it without
guessing — both placements can be satisfied at once (a `@staticmethod` on the
class *and* a module-level name bound to the same object), so the honest move is
to satisfy both rather than flip a coin on which one the caller used. The
committed guidebook is that revision.

Stage 5 was tightened in the same pass, using a detail from the frozen trace:
the agent's own smoke test at tool call 18 imported the three helpers
module-level, which succeeded *because* that is how it had written them. A smoke
test against your own import is self-consistent with your own placement and
therefore proves nothing about it — stage 5 now says so.

**This is the round's most useful finding about the pipeline**, beyond the
artifacts: an Oracle with full privileged access still produced a justification
that a blind actor could have falsified, and it took an independent pass over
the raw task text to catch. Whatever automates phase B in
[task 04](../../../docs/trace-synthesis/plans/README.md) needs a check of this
kind, because the failure was invisible from the guidebook alone — it read as
perfectly reasonable.

## Cost

Wall clock, 2026-09-01 01:07–01:23 PDT (~16 min end to end):

| phase | runs | wall |
| --- | ---: | ---: |
| gold gate (agent-free) | 4 | 593 s, mostly first-time image pulls |
| rollouts | 2 | 291 s (224 s of it agent time) |

Dollar cost per rollout, from each run's `result` event in the frozen
`event_stream.jsonl` (`total_cost_usd`):

| rollout | turns | agent duration | cost |
| ---: | ---: | ---: | ---: |
| 0 | 22 | 86.9 s | **$0.5659** |
| 1 | 36 | 133.8 s | **$0.8442** |

**$1.41 for two rollouts**, ~$0.70 each, dominated by `claude-sonnet-5`
(918 k cache-read + 27.6 k cache-creation input tokens, 8.2 k output on rollout
0). The gold gate is free of model cost by construction — no agent runs in it.

The number task 08 should carry forward: on this instance and box, **one
harvested failure cost one rollout, ~$0.57 and ~2 minutes**. That is the lucky
end of the range — an instance that resolves on early samples costs a rollout
per attempt with nothing to show.

## Open questions

- **Is this instance really 0/2, or were we unlucky twice?** `n=2` settles
  nothing. It matters for [task 08's selection question](../../../docs/trace-synthesis/plans/README.md)
  only if #261's per-instance ratios are being treated as portable — they should
  not be.
- **Does the failure mode generalize?** One instance, one repo, one language.
  The three other candidates passed gold and were never rolled out, so nothing
  here says whether their failures look as clean.
- **Proxy capture is untested.** This round used `STREAM`. `PROXY` needs a
  `go build` of `cc-reverse-proxy` and Go is not installed on this box, so
  whether the proxy log is a better phase-B input is unanswered.
- **Step 5 is pending**, gated on task 02's injection shape. Nothing in this
  round speaks to whether hints steer an actor — only to what they would steer
  from.
- **How much of SWE-bench Pro fails this way?** The graded tests being absent
  during the rollout is a property of `before_repo_set_cmd`, not of this
  instance. If placement/contract-guessing failures are common across the
  corpus, a directional hint has to compensate for missing information rather
  than for bad reasoning — which is a different, and weaker, claim for the
  pipeline than "the actor reasoned poorly".
- **Does "satisfy both readings" survive contact with an actor?** Stage 2's
  revised resolution is sound on paper and untested: nobody has yet run an actor
  against it. It is also specific to a contradiction where both readings happen
  to be cheaply satisfiable at once — a contradiction where they are mutually
  exclusive has no such escape, and this round says nothing about that case.
- **Nothing here validates the guidebook as a guidebook.** It was written
  against a known failure by an author who had seen the gold patch; whether it
  steers a blind actor is step 5's question.
