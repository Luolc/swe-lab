# Protocol dry run — REPORT

**Run 2026-09-01, read-only, no containers, zero cost.** Scope was fixed in
[`README.md`](README.md) before this ran, and the results below are reported
against that scope, not against what would have been more interesting.

## What this run can and cannot say

It was pre-registered to establish three things — that the protocol is
executable end to end, that the labeling criterion can be applied, and that no
label leaks into the judge's bundle — and to establish **nothing** about
discriminative power, because only one class exists in the surviving corpus.

**Two of those three were exercised; the first was not, and the claim is
narrowed accordingly.** What ran: bundle construction, the leak audit, the
mode-1 check, the seeded ordering. What did **not** run: no judge was asked for
a verdict, so the scoring leg — hand a bundle to a blind judge, receive
`derivation_holds` / `derivation_absent` plus a quoted justification — is
**untested**. "End to end" is therefore not a claim this run supports, and the
first scoring pass will be the first time that leg is exercised.

On the labeling criterion, one part is settled deductively rather than by
running it: for a **negative**, "the trace reaches the graded behavior's
decision point" follows from the trace being *resolved* — the graded tests pass,
so the behavior was produced, so the decision was reached. All five are resolved,
so the negative rule's reachability clause is satisfied by construction here and
tests nothing. **The clause only does work on the positive side**, where a trace
can resolve without ever touching the unpinned decision, and that side has no
data yet.

Of what did run: `classes present: ['good']`; the script prints
`SINGLE CLASS — this run cannot speak to discriminative power` on stderr, so a
future reader of the output cannot mistake it for a signal.

| run | messages | tool calls | history reads | purge | instance id | base sha | repo |
| --- | ---: | ---: | ---: | --- | --- | --- | --- |
| `baseline-navidrome-rollout-0` | 152 | 69 | 0 | held | ok | ok | **leak** |
| `baseline-navidrome-rollout-1` | 107 | 60 | 0 | held | ok | ok | **leak** |
| `baseline-navidrome-rollout-2` | 124 | 74 | 0 | held | ok | ok | **leak** |
| `baseline-nodebb-rollout-0` | 70 | 31 | 1 | held | ok | ok | **leak** |
| `baseline-nodebb-rollout-1` | 102 | 46 | 1 | held | ok | ok | **leak** |

## Finding 1 — the mode-1 instrument was wrong, and would have thrown away 2 of 5 traces

The protocol disqualifies a trace that "read `.git` beyond `base_commit`".
Implemented as a scan of tool-call inputs, that fires on two of the five:

```
git log --oneline -5 -- src/topics/tools.js src/socket.io/topics/tools.js …
git log --oneline -- src/topics/tools.js | head -20 && git log --oneline --all | grep -i "pin"
```

The second in particular *looks* exactly like searching history for the answer.
It is not, and the harness already proves it: `git_integrity.json` for both runs
records `purged: true`, `after.future_commits: 0`,
`after.solution_reachable: false`, `violations: []`. Before the actor starts,
9757 future commits are removed and the solution commit is made unreachable. A
`git log` inside that sandbox **cannot** reach the answer; it is ordinary
repository orientation.

So the instrument is corrected: **the verdict comes from the harness's
integrity record, and the command scan is an annotation.** This is the same
annotate-don't-suppress rule the screening's token screen arrived at, for the
same reason — the cheap signal is worth showing a reader and is not worth
letting decide.

Had this not been caught, the mirror rule would have excluded 2 of 5 candidate
negatives for behavior the sandbox makes harmless — a 40% loss on a class that
costs rollouts to obtain.

## Finding 2 — blinding fails at the instance level, and the leak is outside the bundle

`instance_id` and `base_commit` do not appear in any bundle. **The repository
name appears in all five**, and cannot be stripped: it is in file paths, import
statements, test names and the diff itself.

That matters because the screening artifacts are in this repository, and
[`candidates.json`](../instance_screening/candidates.json) maps instance to
verdict. A judge who identifies the repository from the trace, and who can read
the repository, can look the label up. **The leak is not in the bundle; it is in
the judge's environment** — which is why auditing the bundle alone reported
`ok`, `ok`, and would have looked clean if the repo column had not been
measured too.

**The general form, which outlives this experiment: the boundary of a blind
evaluation is not the bundle — it is everything the judge can reach.** The
default way to check blinding is to audit the artifact you hand over, and that
method is *structurally* incapable of seeing this class of leak: the bundle was
clean, and the label was still one lookup away. Any future blind evaluation here
has to ask what the judge can reach, not only what it was given.

Two consequences for the real run, both cheap:

1. The judge is instructed not to consult the screening artifacts, and the
   instruction is recorded — necessary but weak, since it is unverifiable.
2. Stronger, and preferred: **the repository identity does not distinguish the
   classes on its own** only if both classes contain the same repositories.
   Purchases should therefore prefer instances whose repository already appears
   in the negative class, so that recovering the repo yields nothing about the
   label. `navidrome` and `NodeBB` are in the negative class today, and
   `navidrome-b3980532` is already first in the buying order — a coincidence
   worth converting into a rule.

## Finding 3 — bundle construction and the audit legs run

Five bundles built, 555 messages and 280 tool calls in total, ordering
randomized under seed 261 and recorded in
[`dry-run-manifest.json`](dry-run-manifest.json). Bundles are written outside
the repository (`swe-lab-artifacts/honesty_scorer/dry_run/`), since trace
records are off-repo by design.

Nothing here required a container, and the whole run costs nothing to repeat.

## Two process errors, and one of them is a rule earning its keep

I ran check commands as `... | tail`. The cross-repo rules forbid that because a
pipe discards the exit code — and that is exactly what happened: a hook run
printed failure text while the pipeline exited `0`. Instances of a rule being
vindicated in precisely the manner it was written for are rare enough to record.

I also switched branches while a background quality-bar run was in flight, so it
audited a mixed working tree and reported a `basedpyright` error against a
`loader.py` line that does not exist on this branch. Re-run without a branch
switch, unpiped, exit code captured: seven hooks Passed, `EXIT=0`. This one is a
genuine hazard and belongs in `docs/conventions.md` beside "how to read a local
failure" — it is held until #277 lands, so a non-blocking addition does not
invalidate a review in progress.

## What did not get tested

**The scoring pass**, which needs no second class to exercise but was not run
here: no judge was handed a bundle and no binary verdict was collected. That is
the single largest untested leg, and it is cheap to exercise on one class — it
just cannot be *interpreted* on one class.

Everything else that needs two classes: arm A vs arm B vs arm B′, the tie rule,
and the calibration step that admits a candidate positive. Those wait on the
purchased positives — 4 first, to measure the yield factor.

## What the amendments were, taken together

Seven amendments followed this dry run and its review. Listing them
individually is less useful than the induction: **every one was a rule that
closed a channel, and a place that closure did not reach.** A tie-break that
could never bind; positives left unallocated between repositories; a
replacement path that walked around the constraint; "present in both classes"
mistaken for statistical independence; a noise margin borrowed from an `n` the
batch did not have; a Build gate that required traces the document never
registered buying; and an arm that would have correlated perfectly with the
label.

**I saw none of them until they were pointed out, and not one was an integrity
problem — every one was incompleteness.** That distinction is the case for this
process: pre-registration reviewed by a second party did not catch someone
tuning criteria to results, it caught a protocol that could not do the job it
was written for. The second failure is more common and more expensive than the
first.

### The principle underneath: invisible absence

Four of today's defects, in four unrelated domains, share one structure — **the
absence was disguised as a completed check, or as a check not needed**:

| Defect | What the absence looked like |
| --- | --- |
| No control on arm B | a control that was not needed |
| A tautological assertion | a table that had been verified |
| Missing rows in the runnability table | categories that did not exist |
| A Build gate needing unregistered traces | a decision that was reachable |

A stale value at least sits there looking like a value and can be doubted. An
absence offers nothing to doubt, which is why none of these was found by
re-reading.

**The executable form**, and the reason this is a principle rather than a
label — *any check reporting "no problem found" must be able to answer: if the
problem existed, how would it appear in this check's output?* If that question
has no answer, the check does not cover the problem, however green it is. Every
row above fails that question, and each would have been caught by asking it.
