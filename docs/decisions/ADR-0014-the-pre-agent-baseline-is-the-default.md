# ADR-0014 — The pre-agent baseline is the default, and a stub agent proves it

## Status

Accepted. Supersedes the **"default off"** clause of
[ADR-0001](ADR-0001-patch-extraction-and-grading.md)'s 2026-08-25 amendment;
the rest of that amendment (the mechanism, the base-ref contract, the
fail-closed discipline) stands unchanged and is what this ADR turns on.

## Date

2026-09-01

## Context

ADR-0001 facet 3 stages new files with `git add -N` so an agent's new files
appear in its patch. The 2026-08-25 amendment then observed that on an image
whose worktree already differs from `base_commit`, the same contract folds the
**image's** state into every agent's patch — in its own words, *"an agent that
changed nothing still produces a large patch, and grading fails identically for
every agent on the affected instances."* It shipped
`DiffExtractObserver.baseline` / `CodingAgentTask.patch_baseline` as the remedy,
**default off**, reasoning that the affected images were a downstream format
*"this repo does not ship."*

**That premise was wrong, and measurement is what found it.** A rollout on
`nodebb__nodebb-...` was investigated because its agent crashed after 1.4 s and
yet produced a non-empty patch, and because grading then ran to its full 1800 s
timeout. Running the image with **no agent at all** — `docker run --entrypoint
bash`, no services started — showed:

- `HEAD` equal to `base_commit`, and `git diff base_commit` **empty**: every
  tracked file is exactly where the dataset says it should be;
- one untracked directory, `appendonlydir/` — a Redis append-only-file store
  baked into the image at build time;
- and therefore, under facet 3's `git add -N`, an extracted patch of
  **3 files, 15,710 insertions, 166,347 bytes, with no agent involvement.**

So the failure the amendment described is not a downstream-only hazard. It is
in the images this repo runs today, and it had been silently shaping results.

Three observations fall out of that one measurement: the non-empty patch on a
crashed run; the plausible cause of the grading hang (NodeBB's suite needs
Redis, and the patch injects a stale 15k-operation AOF into the graded
container — a **hypothesis**, not yet confirmed); and the fact that gold
grading never hung, because the gold patch is the dataset's own clean diff and
carries none of this.

### The general form

The remedy existed. The failure was documented, in detail, by the very ADR that
shipped the remedy. Neither prevented anything, because:

> **A remedy that is off by default is not a remedy. It is a comment.**

This is the repo's own *"an invariant needs a test, or downgrade the claim"*
rule one level up: a document can describe a failure precisely and still have no
forcing function. Nothing turned the switch on, and nothing alerted when a patch
was large while the agent had done nothing — so the described failure kept
happening, and produced not an error but a **plausible-looking artifact and a
zero**.

## Decision

**1. Both halves of the baseline default to on.** `CodingAgentTask.patch_baseline`
and `UnitTestTask.patch_baseline` are `True`. They are a pair — the base ref is
a contract between extraction and the grader — so they move together, and the
naive composition of the two tasks is now the correct one rather than the
contaminated one.

This is safe for a *clean* image, which is what lets it be a default instead of
a per-image opt-in nobody remembers: `baseline_commit_lines` pins identity,
dates and message, and commits `--allow-empty`, so the baseline sha is a pure
function of the tree. A clean worktree simply yields a baseline whose content
equals `base_commit`.

**2. Grading a patch whose base genuinely *is* `base_commit` opts out
explicitly** — `GOLD_UNIT_TEST` and both tasks in `datasets/verify.py`. The
dataset's gold patch is authored against `base_commit`; there is no pre-agent
tree there and no recorded base ref, so baseline mode would be wrong, not
merely unnecessary.

**3. A named test makes it load-bearing.**
`test_a_stub_agent_produces_an_empty_patch_on_a_dirty_image`
builds a repo that ships untracked state, runs **no agent**, and asserts the
extracted patch is empty — with a second assertion that the same repo *does*
produce a non-empty patch against `base_commit`, so the test cannot pass
vacuously. A companion test asserts the baseline does not buy that emptiness by
suppressing a real agent's edits. Without these, this ADR would be one more
copy of the comment.

## Consequences

- **The standalone `unit_test` workflow now has two unbound inputs** — the
  patch and `patch.base_ref.txt`. A caller passing a bare `--input path` is
  refused and must name both; a rollout's store has both, and the
  `rollout → unit_test` edge wires them together. This is deliberate: a patch
  is not interpretable without its base, and a loud missing-input refusal beats
  a cryptic apply failure.
- **Every past rollout's `resolved` number is suspect on affected instances**,
  and how many instances are affected is **not yet measured** — one image was
  examined. Those numbers are not being recomputed; where one is cited, it is
  marked *predates the patch-baseline fix*.
- **`exclude_globs` is not the answer and was not extended here.** A denylist
  blocks names we thought of; the baseline blocks every pre-existing
  difference, including the ones we have not seen. It remains available as a
  second line for genuine build noise an agent generates *during* a run.
- The failure mode stays **closed**: a sandbox that cannot produce or verify a
  baseline fails the run with the error on the record and no verdict, rather
  than falling back to `base_commit` and grading a contaminated patch.
