# Evidence: how we know a check checked anything

Almost everything this repo learned the hard way is **one invariant** plus the
media it hid in. This file records the media and the few rules that do *not*
follow from it. The invariant itself is cross-repo and lives in
`~/.agents/AGENTS.md` → 质量门 → **「无分辨力的观察不算检查」**; it is not
restated here.

Each **numbered** rule below carries the dated case that produced it. That is
not decoration: a rule cut from its case is an aphorism, and an aphorism changes
nobody's action — the failure this whole file is about. One statement has no such
case and is therefore kept out of the numbered list, under *Intended, and not
enforced*.

`~/.agents/AGENTS.md` also owns the consequences that follow from the invariant
— the pipe rule, the positive-premise rule, and the wrong-environment case.
**Their conditions and reasoning are stated there and deliberately not here**, so
this file cannot become a second, abbreviated copy of them that drifts.

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
   stamps each line) so no surrounding prose is load-bearing. (2026-09-03,
   #405/#407/#408: four findings in one PR body — a fabricated hash, a real
   count taken before the fix and printed after it, a `<count>` placeholder, and
   a figure with no command.)
3. **To learn what a contract change affected, start from the consumers, never
   from the diff.** A diff holds the names that were *edited*; a definition
   change moves the names that *depend* on it, and those did not change by a
   byte. (2026-09-03, #409: #393 moved two definitions; enumerating from the
   frozen criteria found **four** affected quantities, three of which never
   appear in that diff.)
4. **Frozen means the text is frozen, not that the file is closed.** Never edit a
   registered criterion — "the edit is obviously an improvement" is judged by
   someone who has seen the results, the one input a pre-registration excludes.
   **Append a dated, attributed addendum instead**; editing without saying so
   destroys the record, and saying nothing sets a trap. (2026-09-03, #409: a
   frozen criterion's quantities were redefined elsewhere, recorded as an
   addendum with the criteria text untouched.)
5. **An LGTM authorizes the version the reviewer saw, not the act of merging.**
   Pinning the approved SHA (cross-repo) guards against the head *moving*; this
   guards against the reviewer's picture being wrong at that head. Trigger for
   re-confirming: **would this change a rational reviewer's conclusion?** — not
   "did any byte change", which is unfalsifiable and decays into ceremony.
   (2026-09-03, #402: an LGTM was voided because the author knew of a defect the
   reviewer did not.)
6. **A finding has four parts that fail independently: instinct, location,
   mechanism, conclusion.** Say which you are accepting. A fix is recorded by the
   diff; an **explanation is cited**, so an untested causal sentence in a merged
   PR becomes the next person's verified premise. The bar for pushing back is not
   "I think they're wrong" but **"I ran an experiment that would have come out
   differently if they were right"** — then let them reproduce it. (2026-09-03,
   #409: a finding's instinct and location were right and its conclusion wrong;
   a merge test with a control arm settled it, and the reviewer re-ran it.)
7. **Name a check as though the name is its only documentation**, because for a
   gate it is: the output shows the name and nothing else. Where a name is wider
   than its coverage and the coverage is right, **narrow the name** (#408).
   Corollary: a pattern-scanning guard always misfires on prose discussing the
   pattern — that gap is stated, not closed. (2026-09-03, #408: a hook named
   for a repo-wide guarantee that scans only `.py`.)
8. **Do not land a check you know will be red**, unless that red is the only
   thing currently reporting the problem. Test: *if this check did not exist,
   would anyone be unaware?* A known-red check destroys the discriminating power
   of "the suite is red" for everyone, and is how a correct check gets deleted as
   flaky. (2026-09-03, #405: a round-trip check knowingly red until #393
   landed — queued instead.)
9. **During ordinary review iteration, update a branch under review by
   `git merge origin/main` and add commits — not by rebasing or amending what
   the reviewer already read.** A re-review reads the `old..new` delta; merge
   keeps the recorded head as an ancestor so that delta stays valid, while a
   rebase erases the starting point and forces a full re-read on a round where
   attention has already been spent. The two are equivalent for getting `main`
   into a branch, and branch shape never reaches `main` — this repo squashes.
   **This says nothing about when history may be rewritten**;
   `~/.agents/AGENTS.md` owns that, including cases where it *requires* a
   rewrite. If the owner's rule requires one, do it and **tell the reviewer their
   recorded delta is void and a full re-review is needed** (2026-09-03, #410: a
   head was recorded mid-follow-up, and appending left a one-commit delta).
10. **Make the machine poorer rather than finding a stricter one.** Scarcity can
    be manufactured locally; abundance cannot. Reproducing a CI failure by
    removing what the local box has is faster and more faithful than reasoning
    about what CI lacks. (2026-09-03, #400: CI red and local green; pointing
    `CC_REVERSE_PROXY_SRC` at a nonexistent path reproduced it locally at once.)
11. **Stop adding control arms when the remaining failure mode is loud and
    immediate.** The counter-measure recurses — an arm is itself a check — and
    unbounded that is ritual, paid out of the attention the next real defect
    needs. Two questions: does this check bear load (would being wrong switch off
    a downstream review?), and is its failure silent and distant? (2026-09-03,
    #406: a hash comparison got one positive premise — `grep -c` for the
    sentence, non-zero on each side — and stopped there, because an empty slice
    would make that count `0` on the spot.)
12. **Disclose what you expect to count against you, not only what you already
    know is harmless.** The two are indistinguishable in the text — a reader
    sees only that you reported something — so nothing but your own practice
    separates them, and **a disclosure policy conditional on the answer being
    safe is not one**. (2026-09-03, #410: three feature-branch force-pushes were
    disclosed expecting a ruling that they were out of bounds; the ruling instead
    used them as the evidence for narrowing the cross-repo rule, which would
    otherwise still have two readings — #411.)

## Intended, and not enforced

**No case has occurred for this one, so it is not a rule.** The intent is that a
rule here keeps the case that produced it, and that shortening this file happens
by dropping a whole rule *with* its evidence rather than by thinning every rule
into a maxim.

That is a prediction about how this file would decay, not something observed:
nobody has yet deleted a case here and watched the rule become an aphorism. Per
`AGENTS.md`, an invariant with no test is reworded rather than asserted — so it
is stated as intent, and if it ever happens it becomes a numbered rule carrying
that date.

**What *was* observed is a different failure and is already handled by review**:
this file's first version shipped rule 10 with no case and rules 2–8 with a bare
issue number, caught one round later (2026-09-03, #410) — decay starting at the
keyboard rather than at a later editor's hand. The rules that read most like
maxims were exactly the ones with nothing underneath.
