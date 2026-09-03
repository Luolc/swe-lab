# Evidence: how we know a check checked anything

Almost everything this repo learned the hard way is **one invariant** plus the
media it hid in. This file records the media and the few rules that do *not*
follow from it. The invariant itself is cross-repo and lives in
`~/.agents/AGENTS.md` → 质量门 → **「无分辨力的观察不算检查」**; it is not
restated here.

That file also already owns three of its consequences — a check must not run
through a pipe (the pipe reports the last stage's status), a criterion asserts a
**positive premise** rather than excluding the bad cases you thought of, and the
second kind, where a correct check is blind because it runs in the wrong
environment (`docker run --init`, PID 1 and zombie reaping). Read those there.

**When to stop adding control arms.** The counter-measure recurses — an arm is
itself a check — and unbounded that is ritual, paid for out of the attention the
next real defect needs. Two questions: *does this check bear load* (would being
wrong switch off a downstream review?), and *is its failure silent and distant*?
**Stop when the remaining failure mode is loud and immediate.**

## The media it took here

Same defect each time; only the surface changed. Dates matter — each is a real
instance, not an illustration.

| # | Medium | Instance |
|---|---|---|
| 1 | the check itself | `[ -x "$bin" ]` is green for a real binary and for a shell script (2026-09-03) |
| 2 | the thing being checked | a producer that exists, taken as one that is wired: `read_terminal_summary()` had no call site (#397) |
| 3 | the check on the check | a control arm covering 2 of the 6 names the assertion claimed (#397) |
| 4 | documentation | a name in a doc taken as one the code reads (#397) |
| 5 | your own memory of having checked | a SHA reported before reading `git rev-parse`'s output (#400) |
| 6 | a relayed state | a verdict relayed to you is a past snapshot; **"arrived in my inbox" and "is the current state" look identical** (#409) |
| 7 | an acceptance reading | four findings in PR bodies, each closer to text and further from fact (#405, #407, #408) |

Medium 5 is the one no tool guards. **Errors a tool catches will slip through
wherever no tool stands** — a PR number has `gh` in front of it; "I already
checked that" has nothing.

Two git commands whose failure mode is a *plausible answer* rather than an
error, both found here: `git diff --numstat` over a mistyped path prints nothing
and exits 0 (removing the pipe does not save it — the exit code is 0 either
way), and `git ls-files --with-tree=<commit>` **unions** that tree with the
current index, so it reports the same answer for commits that must differ.
`git ls-tree -r --name-only <commit>` measures the tree.

## Rules that do not follow from the invariant

Each changes a concrete action on its own; anything that only illustrates the
invariant is an example above, not a rule here.

1. **Every command block carries its exit status, and chains with `&&`.** In a
   `;` chain only the last command's status survives, so one exit code under a
   multi-command block is a lie about the block. (2026-09-03: a `git rebase`
   failed mid-chain with `Please commit or stash them`; the output read belonged
   to a later command, and the branch shipped on a stale base.)
2. **A numeric claim about current state needs posted command output — or it is
   deleted.** Not "is this number important": that judgement is what failed all
   four times. Identifiers (`#407`, a sha) are not readings, and past-tense
   narration asserts nothing about now. **Prefer deleting to sourcing**: a
   corrected total buys time until the next merge, while removing it retires a
   class. Put the coordinate *inside* the command (`git grep … <rev> <rev>`
   stamps each line) so no surrounding prose is load-bearing.
3. **To learn what a contract change affected, start from the consumers, never
   from the diff.** A diff holds the names that were *edited*; a definition
   change moves the names that *depend* on it, and those did not change by a
   byte. (#393 moved two definitions; enumerating from the frozen criteria found
   **four** affected quantities, three of which never appear in that diff.)
4. **Frozen means the text is frozen, not that the file is closed.** Never edit a
   registered criterion — "the edit is obviously an improvement" is judged by
   someone who has seen the results, the one input a pre-registration excludes.
   **Append a dated, attributed addendum instead**; editing without saying so
   destroys the record, and saying nothing sets a trap.
5. **An LGTM authorizes the version the reviewer saw, not the act of merging.**
   Pinning the approved SHA (cross-repo) guards against the head *moving*; this
   guards against the reviewer's picture being wrong at that head. Trigger for
   re-confirming: **would this change a rational reviewer's conclusion?** — not
   "did any byte change", which is unfalsifiable and decays into ceremony.
6. **A finding has four parts that fail independently: instinct, location,
   mechanism, conclusion.** Say which you are accepting. A fix is recorded by the
   diff; an **explanation is cited**, so an untested causal sentence in a merged
   PR becomes the next person's verified premise. The bar for pushing back is not
   "I think they're wrong" but **"I ran an experiment that would have come out
   differently if they were right"** — then let them reproduce it.
7. **Name a check as though the name is its only documentation**, because for a
   gate it is: the output shows the name and nothing else. Where a name is wider
   than its coverage and the coverage is right, **narrow the name** (#408).
   Corollary: a pattern-scanning guard always misfires on prose discussing the
   pattern — that gap is stated, not closed.
8. **Do not land a check you know will be red**, unless that red is the only
   thing currently reporting the problem. Test: *if this check did not exist,
   would anyone be unaware?* A known-red check destroys the discriminating power
   of "the suite is red" for everyone, and is how a correct check gets deleted as
   flaky.
9. **Make the machine poorer rather than finding a stricter one.** Scarcity can
   be manufactured locally; abundance cannot. Reproducing a CI failure by
   removing what the local box has is faster and more faithful than reasoning
   about what CI lacks.
