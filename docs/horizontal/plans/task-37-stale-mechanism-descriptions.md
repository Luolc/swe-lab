# Task 37 — A mechanical guard for stale descriptions of decided mechanisms

**Status lives in [`plans/README.md`](README.md), not here.** This is a design
record: a proposal with its honest coverage analysis, written because the same
defect appeared three times in one day and "be more careful next time" has
therefore been falsified. Two further instances landed the same day, after it
was written; they are [below](#a-fourth-and-fifth-instance-and-a-subclass-that-fails-differently),
and they make the coverage analysis worse rather than better.

## The defect, and its instances

A document describes a mechanism. The mechanism changes. The description does
not. To a reader the two are indistinguishable, and the reader acts on the
description — which is worse than no description, because a stale one reads as
verified.

Measured 2026-09-01, the first three in the same week's PRs:

| # | Where | What the prose said | What was true |
| :---: | --- | --- | --- |
| 1 | `instance_screening/REPORT.md` (#291) | described the tautological assertion as the mechanism protecting the runnability table | that assertion had been replaced two rounds earlier |
| 2 | `honesty_scorer/README.md` (#292, round 2) | two sentences called the timeout-reporting rule an "open question" pending a ruling | the ruling was fixed, by a blind reviewer, three sections above |
| 3 | a `killpg` path (#290) | newly written prose endorsed the path as safe | the path was not safe |

They differ in an important way. **Instance 2 announces itself**: it contains
pending vocabulary ("open question"), which a script can see. **Instances 1 and
3 do not**: the prose is confident, well-formed, and simply wrong. Nothing in
the text signals staleness.

### A fourth and fifth instance, and a subclass that fails differently

Both landed on 2026-09-01, after the above was written, and they are not more
of the same. They are **absence claims** — sentences asserting that something
does *not* exist — and that shape fails in a way none of the first three do.

**The asymmetry.** Reported by msetup, and recorded here because it survived
being checked rather than because it sounds right:

> A sentence describing how X works, when wrong, is **walked into**: the next
> person to use X meets the discrepancy, because using X is what the sentence
> invites. A sentence asserting that X does not exist, when wrong, is **never**
> walked into — it discourages the one action that would discover the error. A
> reader told "no consumer yet" does not go looking for the consumer.

So this subclass is not merely inaccurate. It is an **active cause of duplicate
implementations**, and it supplies a responsible-sounding reason for writing
one. That matters here specifically: three times in one day this repo and its
neighbours shipped a second implementation of a behaviour, and the newer one
was the less safe of the two each time.

| # | Where | What the prose said | What was true |
| :---: | --- | --- | --- |
| 4 | `conventions.md:57` (fix pending in [#305](https://github.com/Luolc/swe-lab/pull/305), open at the time of writing) | `OPENROUTER_API_KEYS` — "no code consumer yet" | `experiments/trace_synthesis/steered_rerun/supervisor.py` (`key_pool`) had been a consumer for days — and carried the correct splitting rule |
| 5 | machine-setup — "this one is *later* mapped to `CLAUDE_CODE_OAUTH_TOKEN` by swe-lab" | a mapping that had not happened | `packaging/claude-code-bundle/smoke-test.sh` already read it, alongside a repo-scoped fallback variable |

**Instance 5 is msetup's finding and is not reproduced here** — it is in
another repository, and this document records its provenance rather than
claiming a measurement of ours.

Instance 4's consequence is the whole argument. On the day it was found, the
key-splitting behaviour was implemented a second time, in a shell — which is
exactly what the existing consumer's own comment warns against. The document
did not merely fail to help. **It argued for the duplicate**, and it did so
while looking careful.

### An audit of the rest — 1 false of 7

Every absence claim in the **reference** documents (`conventions.md`, the
`README`s, `doc-map.md`, workstream READMEs — the ones a person consults
*before* acting), checked against the code rather than read, 2026-09-01:

| Claim | Verdict | How it was checked |
| --- | :---: | --- |
| `conventions.md:57` — "no code consumer yet" | **false** | `supervisor.py` (`key_pool`) and `judge_steps.py` read it; left to #305 rather than fixed twice |
| `conventions.md:76` — "no vault item yet" for `OPENAI_API_KEY` / `XAI_API_KEY` / `ANTHROPIC_API_KEY` | true | `op item list --vault dev-shared` (titles only): seven items, none of the three |
| `conventions.md:263` — `related_files` "not yet on the engine" | true | no `register_workflow` / `register_task` entry; it has its own `__main__.py`; the only mentions outside the package are a path docstring and one comment |
| `spec.md:184` — the guidebook's privacy, "nothing enforces that yet" | true | no `belief_state` / guidebook test in `src` or `tests`; §12 lists it unenforced |
| `spec.md:559` — the no-silent-gaps requirement, "which nothing implements yet" | true | no per-boundary sequence number or gap record in `src` |
| `w1-related-files/README.md:278` — the `outputs/` restructure "not done yet" | true | `outputs/swebench_pro/` holds only `patch_validation` |
| `redaction.py:335` — `publication_blockers`, "nothing calls this on the way to an upload yet" | true | only test callers; the upload path uses `exchange_publication_blockers` |

Dated records of *process* state — an experiment's "pre-registered, not yet
run", a report's "nobody has yet run an actor", a plan's deferral — were not
counted. They are claims about a schedule, not about the codebase, and they
carry their date.

**The last row is the mitigation, and it is cheap.** That docstring makes the
same kind of claim as instance 4 and is safe, because it says *what does the
job instead* in the same breath. A reader who came looking for the consumer is
handed one. An absence claim that names the thing that exists cannot send
anybody off to build a second one — which suggests the rule to prefer is not
"never write these" but **"never write one that leaves the reader with nowhere
to go"**.

### What this does to Proposal A's coverage — it makes it worse

The `#NNN` hook cannot reach either new instance, and the reason is not a gap
a longer word list closes.

`#NNN` enforcement acts on **pending markers**: text that admits to being
provisional. Instances 4 and 5 admit nothing. They are **false statements in
the confident present**, and they are false in the one direction that stops
anybody checking. A hook can require that a claim carry a reference. It cannot
decide that a claim is *true*.

So the arithmetic gets worse: A covers **one of five**, not one of three, and
both additions are reachable only by Proposal B. Instance 4 is precisely the PR
`SL10` would have caught — the PR that made `supervisor.py` a consumer is the
PR that should have been asked to name the paragraph saying there was none.

**This strengthens "ship A and B together, or neither" rather than qualifying
it.** That recommendation was argued from A covering a third; it now covers a
fifth, and every instance A misses is one B reaches.

### Vocabulary evidence, offered without a proposal

Recorded so the hook's scope can be decided on data rather than on the memory
of one bad day. **This is not a proposed rule**, and the naive version of it
should not ship: `no … yet` is used legitimately and often in `plans/` and
`docs/decisions/`, where a dated deferral is exactly what belongs, so a
pygrep on this vocabulary would drown the signal it is meant to raise.

Words scanned: `no … yet`, `not … yet`, `none yet`, `not implemented`, `TBD`,
`尚未`, `暂无`, `还没有`, `目前没有`, `以后`, `将来`. Repo-wide they hit 38
lines across 33 files; restricted to reference documents, 7 — the table above.
**The discriminator that mattered was not the vocabulary, it was the
document's kind**: reference docs make load-bearing absence claims, plans and
ADRs make dated ones.

If a mechanical check for this subclass is ever wanted, the honest form is
therefore not lexical. It is the same shape [#291 landed](#proposal-a-covers-one-instance-of-five--so-it-is-not-the-whole-answer):
have the assertion be produced by the thing it describes — a claim of "no
consumer" that a test re-derives by searching for consumers.

### The test any addition here has to pass

*What breaks if this is not written down?* An addition that cannot answer that
concretely should be refused, however much stricter it sounds. This one
answers: **on 2026-09-01 the same behaviour was implemented three times, and a
sentence asserting there was no implementation is on the record as one reason
why.** The cost of the wrong answer is not a stale doc; it is a second, less
safe copy of something that already existed.


## Proposal A — a pygrep hook for unreferenced pending markers

Forbid pending vocabulary in `docs/**` and `experiments/**/*.md` unless the same
paragraph carries a `#NNN` issue or PR reference:

- vocabulary: `open question`, `pending decision`, `to be decided`, `TBD`,
  `待裁决`, `owed to`, `will be fixed later`;
- the required reference is a **`#NNN`**, not a section anchor. That distinction
  is what makes the hook fire on instance 2, whose stale sentences *did* carry
  links — to a heading in the same file.

**Acceptance criterion, as specified:** the hook must go red on #292's round-2
state (ruling fixed, sentences still present). It does, and it was checked
against that exact text rather than assumed.

**The false-positive question is real and the answer is a feature.** The
screening report has a legitimate `## Open questions` section; so do other
reports. The hook fires there too — and that is correct: **an open question with
no tracking issue is itself the defect**, and requiring `#NNN` in those sections
improves them.

The position is **falsifiable, and held until it is falsified**: if a real
example turns up of an open question that genuinely should carry no tracking
issue, that is the moment to reconsider. Until such an example exists, an
exemption is a real hole bought with a hypothetical cost, and a request for one
on friction grounds is escalated rather than conceded.

**No exemption list means no knob**, which is the same principle amendment 12
settled for the honesty-scorer protocol, in its words: *removing the cut ends
the discretion, re-tuning it only relocates it.* The exemption list **is** that
cut. Recording the reasoning here matters more than the decision, because the
next person to hit the friction has to be able to see the hole was left closed
**on purpose** rather than by oversight.

**What the hook cannot do:** decide whether the referenced issue is still open.
That needs the network and belongs in CI or a periodic sweep, not in a
sub-second commit hook. The local hook enforces the *form* (a reference exists);
a separate check can enforce the *state* (it is still open). Splitting them this
way keeps the commit hook inside its budget.

## Proposal A covers one instance of five — so it is not the whole answer

This is the part worth being explicit about, because shipping A alone and
calling the family handled would itself be an instance of "a fix that covered
half".

For instances 1 and 3 there is no lexical signal at all, and no grep can
acquire one. For instances 4 and 5 there is vocabulary but no *pending* marker
— the sentences are confident and false, and a hook that checks for a tracking
reference has nothing to check.

The only mechanism that has actually worked on this family is the one #291
landed: **make the assertion about the prose be produced by the
mechanism the prose describes** — `table_check.py` parses the paragraph's table
and checks it against the data, so editing the prose without the mechanism turns
it red. That is the general shape, and it is already written down as a
convention in [`conventions.md`](../../conventions.md) ("a check that guards a
committed artifact belongs under `tests/`").

## Proposal B — the review-time half

What remains is the case where the described mechanism is code and the
description is prose that no test reads. That is a review question, not a hook
question, and it takes the checklist form:

> **`SL10` (proposed): a PR that changes a mechanism must point at the paragraph
> describing it, or state that none exists.** Not "did you update the docs" —
> the specific, answerable form: *name the prose that describes what you just
> changed.* An unanswerable question is a finding.

**`SL10` would be a new review policy, and it has to argue for itself.** An
earlier draft of this document claimed it was merely the existing rule with a
wider domain, and that claim was wrong: [`AGENTS.md`](../../../AGENTS.md) binds
this obligation for exactly one case — an ADR that supersedes a section of a
spec rewrites that section in the same PR, *and if you cannot point at the
paragraph you changed, the ADR is not finished*. That is the **precedent and
the motivation** for `SL10`; it is not its authority. Widening the domain from
"an ADR changing a spec" to "any change to a mechanism" is a change of scope
that a reader must be able to accept or reject on its own merits, and borrowing
the older rule's standing would have smuggled it past exactly the scrutiny a
new policy is owed. (It would also have been this document's own subject matter
in miniature: a claim about what is already true, which nothing checks.)

So `SL10` is proposed with its scope stated:

- **Applies to** a PR that changes the behavior of a mechanism a document
  describes — a check, a protocol rule, a lifecycle, a contract.
- **Does not apply to** new mechanisms with no prior description, pure
  refactors that preserve behavior, or `experiments/` scratch that no document
  describes.
- **Cost** is one search per PR, and the failure state is definite: a PR that
  changes behavior and can name no paragraph is either incomplete or is
  asserting something the reviewer can confirm in that same search.
- **Argument for it**: five instances in one day, listed above, four of which no
  hook can reach — and instance 4 is the exact shape this asks about: the PR
  that made `supervisor.py` a consumer is the PR that should have been asked to
  name the paragraph saying there was none. The ADR clause is evidence that this obligation is workable in
  practice, not evidence that it is already required here.

The distinction from generic "update the docs" advice is that it is
**mechanically promptable** and has a definite failure state: a PR that changes
behavior and names no paragraph is either incomplete or is asserting something a
reviewer can check in one search.

## Recommendation

Ship A and B together, or neither. A alone hands back a green check that covers
a fifth of the family, which is the failure mode this whole task exists to
attack.
