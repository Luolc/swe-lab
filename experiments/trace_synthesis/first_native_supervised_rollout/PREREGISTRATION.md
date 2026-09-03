# Pre-registration — first natively supervised rollout

**Frozen before the run.** `git log --follow PREREGISTRATION.md` shows when;
the run's timestamp says whether that was before. A criterion edited after the
run is not a criterion, whatever it says.

The design, method and how-to-run are in [`README.md`](README.md); this file is
only the readings and what would falsify them.

## Where the defects in this file have actually been

Every flaw found in this document so far — four in the criteria, one in the
censoring rule, one in the projection — is recorded where it happened rather
than quietly fixed, because the pattern is more useful than any of them.

**Defects accumulate in the parts of a document nobody treats as an object of
review.** Criteria, assertions and conclusions are what a reader knows to
attack. Censoring rules, definitions, units, defaults, normalizations, and the
"everything else" arm of a table wear procedural clothing and get skimmed —
and they decide the conclusion just as much.

This file has the receipt. It refused "it failed for an unrelated reason, so it
doesn't count" in every criterion, and then did exactly that inside its
censoring rule, where it survived review twice. The projection in criterion 8
was the same thing again: an implicit normalization sitting underneath an
assertion everyone was busy checking.

So the derived rules, applied above:

- **the censoring rule is a criterion** and is written to be attacked like one;
- **the projection is written out** rather than left to the implementation;
- **the "everything else" arm is the inclusive one**, so that what nobody
  enumerated is counted rather than dropped.

**And one about how they were found.** Not one was caught by re-reading. Every
one took someone else supplying a concrete world to check — `[A, A, B]` against
`[A, B, B]`, or a wrapper recording only messages one and three. Re-reading
your own document is a comparison whose two sides are both yours.

The consequence worth stating, because it is easy to get backwards: **failing
to think of a counterexample is not evidence that none exists.** "I searched my
own head and found nothing" gives the same output whether or not there is
something to find. That is why a criteria document goes to another reader — not
because they are smarter, but because their search starts somewhere else.

## Success criteria

Every one of these is checked against **persisted artifacts**, after the
container is gone. A criterion that can only be checked by watching the run is
not a criterion.

**A. The supervisor existed and said so.**

1. `supervisor_summary.json` is present and parses at the schema version this
   reader knows.
2. `accounted_for` is `true`.
3. The run's metrics carry no `supervision.unhealthy`.
4. `rollout_outcome` is not `SUPERVISION_FAILED`.

*(3 and 4 are not redundant with 2: 2 is what the wrapper claims, 3 is what our
observer made of it, and 4 is what the classifier did with that. A break
anywhere in that chain is the thing most worth catching on a first run, and
each link is a separate source.)*

**B. It actually judged, at least once — and it is recorded *how much*.**

5. `supervision.boundaries` ≥ 1.
6. `supervisor_log.jsonl` is non-empty and contains ≥ 1 row whose kind is a
   judgement (`spoke`, `silent`, or `unjudged` — all three mean the supervisor
   was consulted; only their absence means it never was).

*(Without B, criteria A are satisfiable by a wrapper that started, supervised
nothing, and shut down tidily — precisely the run that must not be mistaken
for evidence.)*

7. **Recorded, with no pass threshold:** `supervision.boundaries`, the number
   of assistant messages in the actor's event log, and the configured
   `judge_every_n_assistant_messages`, reported together.

*(5 and 6 cannot tell "supervised throughout" from "judged once, at the end" —
they give the same output for both. The product's claim is **process**
supervision, so a run with 200 assistant messages and `boundaries = 1` would
satisfy B while making that claim false. The expected relation is roughly
`assistant_messages / N`. **Deliberately no threshold on a first run:** a
number invented to fit one observation is not a criterion, it is a
rationalisation. The three numbers appearing together is the criterion.)*

**C. It watched the actor that actually ran — witnessed from outside it.**

The witness must not pass through the wrapper. Two are already collected by
this run:

- `native_transcript.tar.gz` — the agent CLI's own session record, whose module
  says it is written "by the agent binary's own session persistence — not by
  the pump, not by the supervisor's log, not by the converter — so it survives
  every one of those being wrong". **This is the reverse-direction witness**,
  because it is a complete record of the session rather than a view of one
  channel.
- `claude.proxy.jsonl` — the actor's HTTP traffic, a different wire entirely
  from its stdout. Used as a second, forward-direction corroboration;
  reconstructing message text from SSE deltas to get a count out of it is
  possible but is not relied on here, and that is said rather than assumed.

Not byte equality; the representations differ by construction, so the
comparison is between **projections** of the two records — and the projection
is where the next hole lives. `project(log) == project(oracle)` keeps the shape
of a structural assertion no matter how much `project` throws away: a
normalization greedy enough to discard a field makes two sequences that ought
to differ compare equal, and the criterion still *reads* like set equality.
**An unwritten projection is an unreviewed projection**, which is exactly the
part of a document defects accumulate in (see below).

**The projection, written out.** An assistant message's identity is the tuple:

| kept | why |
|---|---|
| its position in the sequence | order is part of what a faithful relay preserves |
| the concatenation of its text blocks, trailing whitespace stripped | the message's content |
| the `name` of each tool-use block, in order | *which* actions were taken |
| the sha256 of each tool-use block's input, in order | *with what arguments* — without this a wrapper could alter a tool call's parameters and both sequences would still compare equal |

| dropped | and whether that is a weakening |
|---|---|
| block ids, timestamps, `usage`, `stop_reason`, `model` | no — the two paths assign these independently and disagreement carries no information about relaying |
| thinking blocks | **yes, a stated weakening.** The two records are not guaranteed to represent these the same way, so requiring them risks failing C for a representation difference rather than a defect. A wrapper that dropped only thinking content would not be caught here. Recorded rather than hidden; closing it needs a comparison of how each path represents them, which this run does not have. |

8. **The two records are the same record.** The sequence of identities read
   off the wrapper's actor event log **equals**, element by element and in
   order, the sequence read off the transcript. Equal length, equal order,
   equal multiplicity.
9. **Both sequences are reported**, as per-identity counts plus their lengths,
   so a reader can see the comparison rather than take its verdict.

   *(Three drafts of this criterion failed, each to a weaker form of the same
   defect, and the progression is the point:*

   - *"the wrapper's log matches what the actor produced" — **same-source**:
     the wrapper is the only reader of that stdout, so both sides came from
     it;*
   - *"every message the wrapper logged has an outside witness" — **one
     direction**: a wrapper that drops half passes, because the dropped
     messages never enter the set it quantifies over. Control world: the actor
     emits four, the wrapper records the first and third and hashes that
     truncated log — true, and half the run unsupervised;*
   - *"both directions, and equal counts" — **set-like, not multiset**:
     repeated identities let both directions hold many-to-one. Control world,
     from review: transcript `[A, A, B]`, wrapper log `[A, B, B]`. Counts
     agree at 3, every entry on each side corresponds to one on the other, and
     yet an `A` was dropped and a `B` invented.*

   - *"sequence equality" — **the projection**. The form
     `project(log) == project(oracle)` is only as strong as `project`, and a
     normalization that drops a field makes unequal records compare equal
     while the criterion still reads structural.*

   *Each fix was tighter and each was still a subset assertion in disguise —
   and the fourth is why "sequence equality has no next loophole", which an
   earlier draft of this file said, was itself the same mistake one level up.
   A structural comparison is the right form, and it moves the question rather
   than ending it: from what is compared to **what the comparison can see**.
   Hence the projection is written out above and gets its own control arms
   below.)*

10. **The projection is exercised, one control arm per field it claims to
    keep.** Four fields are kept, so four arms, each perturbing exactly one in
    a copy of the wrapper's log: change one character of message text; rename
    one tool-use block; alter one tool-use input; move one message. **C must
    go red in each.** An arm that stays green means the projection silently
    discards the field it claims to keep — which is precisely how the
    structural form decays back into a subset assertion. The arms are run
    against the real records from this run, and their results are reported
    with the criteria.

11. **If the two sequences differ for a structural reason** — one record
    legitimately carrying assistant messages the other never represents — that
    is reported as **C failed**, together with the difference observed. It is
    **not** grounds for relaxing 8 during or after this run. A criterion
    weakened to accommodate what a run produced is not a criterion, and this
    is the conservative direction on purpose: a criterion that fails for a
    benign reason costs one re-run, while one that passes for a bad reason
    enters the record as data. A revised rule belongs to the next experiment.

12. **The witness is present and non-empty.** The transcript archive exists
    and yields ≥ 1 assistant message. If it does not, C **fails** — it does not
    quietly fall back to the wrapper's own account. An equality assertion over
    two empty sequences is true and says nothing.

*(The first draft of this criterion asked whether the wrapper's log matched
"what the actor produced". **There is no such observation except through the
wrapper**: it is the only reader of the actor's stdout, so both sides of that
comparison came from it. A wrapper that read the stream, dropped half, and
hashed the half it kept would have passed. A comparison whose two sides share
a source is an echo.)*

13. `summary.actor_event_log_sha256` matches the digest of the persisted event
    log. **This is integrity, not authenticity** — it establishes that the file
    was not altered after the wrapper closed it, and says nothing about
    whether its contents are what the actor emitted. That is what 8 and 9 are
    for, and this is not a substitute for either.
14. `summary.criterion_sha256` equals the digest of the criterion rendered into
    the config.

**D. Two upstreams, kept apart.**

15. `claude.proxy.jsonl` has ≥ 1 record with a 2xx response.
16. `supervisor.proxy.jsonl` has ≥ 1 record with a 2xx response.
17. For one record from each log, the **response headers are reproduced in
    full and verbatim** in the report, and then read for which upstream
    answered. The response is the far end's own output, so it is evidence;
    `--target` is our configuration, and proving it against itself is an echo.

    Both halves matter, and the first matters more. My reading is a judgement
    made without prior knowledge of what either upstream's headers look like,
    so "I looked and concluded OpenRouter" is checkable only by trusting me.
    The verbatim headers let a reader decide for themselves whether the two
    sets discriminate at all — **which is worth more than my being right.**

    **No expected header names are written down in advance.** Pre-registering
    an expectation is usually good practice, but it needs a basis, and here
    there is none; writing one now would turn "I found what I expected" into
    the effective criterion, which is screening the observation through the
    expectation.

*(16 is the only artifact-level evidence that the supervisor's model calls
happened at all. **A known weakness, recorded rather than papered over:** the
proxy's log format has no field naming where a request went — it records
request headers/body and response status/headers only, and **not** the
response body, so headers are the only place the far end speaks for itself. So
17 is read off the response after the fact rather than asserted in advance,
and the first run reports what it saw.

If that turns out not to discriminate, **the fix is a field in the proxy's
log, not a stronger claim here** — and that route is open rather than
hypothetical, since cc-reverse-proxy is to move into this repo under `go/`,
making it our code. That is a separate PR and is not to be hung on this
experiment.)*

**E. No credential is anywhere in the record.**

18. **Every file under the run's output directory** is scanned — traversed,
    not listed. No artifact is named here on purpose: a list omits whatever is
    added next, and what is added next is exactly what a list would miss.
    Neither credential's value appears in any of them. Checked by reading the
    values inside a process and printing **only a boolean** — never echoed,
    never an argument, never through a shell.
19. `authorization` appears as `[REDACTED]` in both proxy logs — the positive
    half, so that 18 passing cannot be explained by the proxy having recorded
    no headers at all.

*(**The boundary of 18, written down because an unwritten boundary is read as
"already checked":** it finds a credential only in its literal form. A copy
that was base64'd, URL-encoded, or split across a line boundary is invisible
to it. Not widened for a first end-to-end run — the point is to say what this
check does and does not cover, not to build a scanner.)*

## When an invocation counts, and what it may be called

**A censoring rule exists to exclude noise. It must not exclude our own
defects.** A rule that files "our wiring is broken" as *doesn't count* makes
the experiment permanently blind to the failure it was built to find — and
then spends a re-run driving into the same wall, because the wall is not
random.

The test is mechanical: **could a re-run plausibly give a different result?**
If yes, it is noise. If no, it is a result, however unwelcome.

**The burden falls on exclusion, not inclusion.** An invocation **counts** by
default. Not counting it requires naming, in the report, the specific external
transient cause and why a re-run could plausibly differ. Read as first match:

| # | condition, in order | counts? | may be re-run? |
|---|---|---|---|
| 1 | the invocation script exited **78** — a fail-closed probe fired: no wrapper binary at its path, or an empty credential | **yes** | no, not before a fix |
| 2 | a named external transient — image pull failure, docker daemon unavailable, network unreachable, OOM kill — quoted verbatim in the report | no | once |
| 3 | anything else, `TIMED_OUT` and `SUPERVISION_FAILED` included | **yes** | no |

**Arm 1 is the one this rule exists for.** Exit 78 is *our* probe firing: the
binary is not where the asset was supposed to put it, or the credential did not
cross. That is task 21 §3a failing, and §3a is part of what this run is
checking. A re-run cannot differ — the binary is still not there — so it is a
result, and its verdict is `not-supervised` with the specific reading **"§3a is
not in effect"**. Fix, then run again as a new invocation.

Arm 2 is the only exclusion, and it is not self-executing: the cause must be
named and quoted. "It failed for an unrelated reason" without the reason is the
judgement this document refuses everywhere else.

Arm 3 counts because a bad ending is a result. `TIMED_OUT` and
`SUPERVISION_FAILED` are real supervised runs that ended badly; they are
evaluated against the criteria exactly as a clean run is, and will fail
several.

**At most one re-run**, available only to arm 2. A second occurrence of arm 2
stops and goes to the user rather than becoming a third attempt: 1×1 is not a
budget to spend down. A re-run never replaces an invocation that counted.

## The one sentence each result licenses

The verdict is computed, not chosen, and **a criterion that cannot be evaluated
is a criterion that failed.** There is deliberately no "inconclusive" arm: an
artifact missing for reasons "unrelated to supervision" is exactly the judgement
call that hands the choice back to whoever writes the report.

| verdict | condition | the sentence it licenses, and no stronger |
|---|---|---|
| `supervised` | every criterion 1–19 passes | "One rollout was supervised end to end by the in-sandbox wrapper: its own account says so, an independent witness confirms it watched the whole actor stream, and both upstreams answered." |
| `not-supervised` | any criterion fails | "This run did not demonstrate supervision. The criteria that failed were: …" — naming every one, not the first. |
| `not-supervised (§3a is not in effect)` | the run exited 78 (arm 1 above) | "The wrapper was never placed, or its credential never crossed, so the run refused to start unsupervised. The fail-closed guard worked; the wiring did not." |

Neither sentence may be widened to the runtime. **`n = 1`**: this says a
supervised rollout happened once, not that the native supervisor works, is
reliable, or is ready. Any claim of that shape needs a different experiment and
should not borrow this one's evidence.

## What would falsify, and is still a result

A run that fails any criterion is reported as it happened. The first run
failing is more informative than the first run passing, and a failure that is
explained is worth more than a pass that is assumed. Specifically expected as
plausible first-run failures: the wrapper cannot reach the proxy (network
namespace), the config is refused (a hand-mirrored field drifted), or the
actor's argv arrives wrong (quoting).

**No criterion is to be relaxed to make the run pass.** A run recorded as
successful under a loosened criterion is worse than a failed run, because it
enters the record as data.

## What each criterion's evidence comes from

Written out because the defect this whole file was nearly built on is a
comparison whose two sides share a source. If a row's two columns name the
same producer, the row is an echo and not a check.

| # | the claim | who produced the evidence |
|---|---|---|
| 2 | `accounted_for` | the wrapper, about itself |
| 3 | no `supervision.unhealthy` | our observer, reading the wrapper |
| 4 | `rollout_outcome` | the classifier, reading the metrics |
| 7 | boundaries vs assistant messages | the wrapper, and the actor's own log |
| 8 | nothing dropped, nothing invented | **the transcript — never the wrapper** |
| 10 | the projection sees what it claims | four perturbed copies, each red |
| 12 | the witness exists at all | the agent CLI, which wrote it |
| 13 | the file is unaltered | the wrapper, about its own file (integrity only) |
| 17 | which upstream answered | the upstream, in its response headers |
| 19 | headers were recorded | the proxy |

## Addenda — appended after freezing, criteria text untouched

An addendum is not an amendment. Nothing above this line is edited, because a
frozen document's value is that it cannot be: the moment it may be revised
*when the revision is obviously an improvement*, it is no longer frozen, and
"obviously an improvement" is judged by someone who has seen the results.

### A.1 Criterion 7's `supervision.boundaries` is counted differently than when this froze

*Appended 2026-09-03 by `swelab-integ-impl`, before the run, following the
counting change in [#393](https://github.com/Luolc/swe-lab/pull/393). Criterion
7 is unchanged and stays unchanged.*

Criterion 7 records `supervision.boundaries` alongside the actor's assistant
message count and the configured `judge_every_n_assistant_messages`, and its
note gives the expected relation as roughly `assistant_messages / N`. **The
criterion is frozen; the definition of one of its quantities is not, and it
moved.**

**The change**, read from the source rather than from a description of it —
`supervisor.rs` in #393:

> A result asks that the turn's last evidence be judged; a boundary that has
> yet to take its snapshot will judge it, so it is not a boundary of its own
> then.

Before: an actor `result` event always counted as a boundary. After: it does
not when a boundary is already pending its snapshot, because that boundary will
judge the same evidence — a boundary that never separately happened is no
longer counted. Under `N = 1` with a slow judge, the old rule over-counted.

**What this does and does not do to criterion 7's expected relation:**

- **The shape is unchanged.** Boundaries that fall while a judgment is in
  flight are still recorded — as `unjudged` — and still counted, so the
  `assistant_messages / N` term stands exactly as registered.
- **An additive term shrinks.** The difference between the two definitions is
  bounded by the number of `result` events, and only those coinciding with a
  pending boundary. This run is a single-turn invocation, so the bound is
  **one boundary**.

So this is a difference in magnitude, not in kind, and it cannot flip criterion
7 — which has no threshold by design. It is recorded because **a reader
comparing the reported numbers against the expected relation has to know which
definition produced them**, and nothing in the frozen text says.

**The report must state, beside criterion 7's three numbers, that this run's
`boundaries` uses the post-#393 counting and that the criterion was frozen
before it.** Written here rather than left as an intention, because an
obligation that lives only in someone's memory of what to do after the run is
the one that does not survive the run.

This is the same failure the censoring rules above are written against, one
level down: **the criteria are reviewed, the definitions they cite are not.**
