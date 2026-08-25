# ADR-0001: Patch extraction and grading

## Status

Accepted

## Date

2026-07-17

## Context

Workstream 2 runs a coding agent inside an instance's container, captures its
edits as a patch, and grades that patch by re-applying it and running the
instance's tests. The round-trip is deceptively error-prone: a `git diff` that
looks right can fail to re-apply, or apply the wrong bytes, and the failure is
almost always **silent** — it just grades as unresolved. New files, binaries,
leaked git config, nested `.git` dirs, submodules, and LFS pointers each have a
corner case.

We surveyed how the canonical open-source harnesses (SWE-agent, mini-swe-agent,
OpenHands, Agentless, Moatless, R2E-Gym, and Scale's own SWE-Bench Pro harness)
extract and apply patches, and catalogued ~40 corner cases. That survey is
retained as **non-authoritative background** in
[`docs/patch-extraction.md`](../patch-extraction.md); this ADR is the decision of
record, and the **implementing code is the source of truth**:
[`core/patch.py`](../../src/swe_lab/core/patch.py) (extract/clean),
[`rollout/entryscript.py`](../../src/swe_lab/rollout/entryscript.py) +
[`rollout/runner.py`](../../src/swe_lab/rollout/runner.py) (drive + strip), and
[`core/datasets/swebench_pro/grading.py`](../../src/swe_lab/core/datasets/swebench_pro/grading.py)
(apply + grade).

The decision is scoped to the **MVP happy path**: text patches, one strict
apply, graded exactly like Scale. Several richer behaviors are deliberately
deferred; they are recorded here as backlog so a future instance that needs one
knows the intended shape.

## Decision

The extract → apply → grade contract, in eight facets:

| # | Facet | Decision |
| --- | --- | --- |
| 1 | **Diff base** | Extract diffs the worktree against the instance's original **`base_commit`**. |
| 2 | **Build-noise denylist** | **None.** New files are staged from the repo root with no `:(exclude)` pathspec. |
| 3 | **Binary handling** | **Text-only happy path.** Stage new files with `git add -N`, diff **without** `--binary` (binary bytes are never serialized), then strip any residual `Binary files … differ` marker host-side. |
| 4 | **Diff prefixes / config isolation** | Neutralize all ambient git config (global/system/pager/textconv/EOL); pin `diff.noprefix=false` + `diff.mnemonicPrefix=false` for stable `a/ b/` prefixes on any git version. Do **not** use `--default-prefix` (git ≥ 2.41 only). |
| 5 | **Agent-touched test files** | The grader restores the **golden** test files by path, *after* applying the patch, so an agent cannot game the held-out tests. |
| 6 | **Apply strictness** | A single strict `git apply -v`, matching Scale exactly. No fallback ladder. |
| 7 | **Deferred silent-failure guards** | **Documented, not implemented** (see below). |
| 8 | **Empty patch** | The rollout runner classifies an empty/no-op patch as an explicit `empty_patch` outcome — the eval is skipped, never graded as a pass. |

**Extraction hygiene** (part of facet 3/4): before diffing, stray nested `.git`
directories are removed (a dep the agent cloned, or a fixture that ran
`git init`, would otherwise be staged as one opaque gitlink that swallows its
files and breaks apply). The diff is written as **raw bytes** — no text
round-trip — for transport correctness.

**Facet 7 — deferred guards, documented and intentionally not built.** Three
corner cases can silently mis-extract; we accept the risk for the MVP because
each is a rare edge, and record them so they can be added if one ever bites:

- **Gitignored new source files** — `git add -N` skips a genuinely-new source
  file that happens to match `.gitignore`.
- **Submodules / gitlinks** — a `Subproject commit` line could reach the patch.
- **Git LFS pointer-vs-content** — an `filter=lfs` file could diff as its pointer.

The originally-recorded intent was "defer but log when they would fire"; we have
decided **not to implement even the logging now** — these are very small corner
cases and the detection code is not worth its weight yet. If one is suspected on
a real instance, add detection + a log line at that point.

## Alternatives Considered

### Facet 1 — diff against a post-setup commit instead of `base_commit`
- The "commit-the-base" trick snapshots the container's post-provision state, so
  image-setup mutations can't leak into the patch.
- **Rejected (deferred, P1):** diffing against `base_commit` *guarantees* the
  grading round-trip — the grader does `git reset --hard base_commit` then
  `git apply`, and the dataset's own gold patches are diffs vs `base_commit` and
  apply cleanly, so the convention is proven. The post-setup base is nicer but
  fiddlier; revisit only if setup noise becomes a real problem.

### Facet 2 — a per-ecosystem `:(exclude)` build-noise denylist
- mini-swe-agent excludes `pyproject.toml`, `setup.cfg`, lockfiles, etc.
- **Rejected (deferred, P1):** an agent solving a task rarely rewrites build
  config, so there is no material impact today. The `exclude_globs` parameter is
  already wired to add one (with drop-logging, no silent truncation) when needed.

### Facet 3 — faithful binary extract + apply
- Serialize binary hunks (`--binary`) and apply them, for a byte-faithful trace.
- **Rejected (deferred, P1):** Scale strips binary before applying, so emitting
  binary would make our grade *diverge from Scale's* on exactly those patches.
  Text-only keeps us aligned. A faithful two-artifact scheme (clean patch for
  grading + raw for the trace) is a later option.

### Facet 6 — an apply-hardening ladder (`git apply --3way` → `--reject`)
- We hold `base_commit`, so `--3way` is viable and would tolerate more.
- **Rejected (kept optional):** grading strictly like Scale keeps our numbers
  comparable to the leaderboard. Extra tolerance is a clearly-labeled experiment
  behind a flag if we ever want it, never the silent default.

### Facet 8 — an eval-side empty-patch guard
- Have the grader also refuse a directly-supplied empty `--patch-file`.
- **Rejected (deferred, P2):** the rollout side already classifies empties
  explicitly, which covers the real path; a defensive eval-side guard is cheap
  to add later.

## Consequences

- **The round-trip is guaranteed.** A patch relative to `base_commit` applies
  cleanly against the grader's `git reset --hard base_commit`.
- **Patches are text-only.** Binary changes are dropped (the same net effect
  Scale gets by stripping at apply time); the graded patch is always
  cleanly-applyable.
- **Our grade matches Scale's** (single strict apply, no ladder, golden test
  files restored), so results are comparable to the reference and leaderboard.
- **An unresolved run's reason is never guessed** — `empty_patch` vs
  `unresolved_tests_failed` is an explicit outcome.
- **Facet 7's three corners fail silently** if hit — an accepted MVP risk, not an
  oversight. A gold/rollout sweep is the safety net; add detection when one bites.
- **Backlog:** P1 — post-setup base (1), denylist (2), faithful binary (3),
  facet-7 detection (7); P2 — eval-side empty guard (8), fuller test-file reset
  (5). Adding any is an extension, not a rewrite (the seams/parameters exist).

## Amendment — 2026-08-25: Facet 1 lands, opt-in and default off

Facet 1 above (a post-setup base instead of `base_commit`) was deferred with a
named revisit condition: *"revisit only if setup noise becomes a real problem."*
It became one — [#231](https://github.com/Luolc/swe-lab/issues/231) reports
task images, in the Harbor format this repo does not ship but downstream uses,
whose worktree already differs from `base_commit` when the agent starts: files
deleted or modified during the image build and never committed. Nothing in that
format forbids it, and it is invisible until you diff. The extracted patch then
carries those pre-existing changes as well as the agent's, so an agent that
changed nothing still produces a large patch, and grading fails identically for
every agent on the affected instances.

**Decision.** `DiffExtractObserver.baseline` (and `CodingAgentTask.patch_baseline`)
commits the tree as the agent found it and diffs against that. **Default off**,
so the accepted decision above remains this repo's behavior and nothing that
works today changes.

Diffing the *worktree* against the baseline — not `baseline..HEAD` — is
deliberate and is what makes this strictly more robust than the benchmark
format's own answer: it captures the agent's uncommitted edits *and* its
commits, where a commit-to-commit diff silently yields an empty patch if the
agent never commits. A harness that runs arbitrary agent binaries cannot rely
on one honoring a "commit when you are done" instruction.

**The base ref is a contract between extraction and the grader, not a property
of extraction alone.** This is the same reasoning that made `base_commit` the
default: a patch is only applicable against a tree that matches the base it was
taken from. Concretely, `swebench_pro`'s grading script runs

```sh
git reset --hard <base_commit>
git clean -fd
git checkout <base_commit>
git apply -v <patch>
```

which wipes exactly the image mutations a baseline commit captures. Measured in
a real instance container, the interaction is **conditional**, not a flat
incompatibility:

- The agent touches only paths the image did **not** mutate → the patch applies
  cleanly, and is *better* than the default (which would have carried the
  image's own edits too).
- The agent touches a path the image **did** mutate → apply fails. Recreating a
  file the image deleted produces a `new file` hunk, and against a tree reset to
  `base_commit` that file is already there: `error: README.md: already exists in
  working directory`.

So the mode is **end-to-end or not at all**: `UnitTestTask.patch_baseline` is
the grading half, and the two flags must be set together — each side alone
moves the patch and the tree apart. In baseline mode the compiled eval script
replaces the reset trio with:

1. **Recompute the baseline** with the *same* pinned commands the rollout side
   ran (`baseline_commit_lines` in `git/patch.py` is the one source — identity,
   dates and message all enter the commit hash, so a drifted copy would fail
   every verify).
2. **Verify the sha** against the run's recorded base. The sha is a pure
   function of the image's tree, so equality *proves* this container is about
   to grade the tree the patch was taken from — the round-trip guarantee that
   made `base_commit` the default, restored as a checked precondition. A
   mismatch aborts with both shas named, rather than surfacing minutes later
   as a cryptic apply error. (This also fires if the image mutates its own
   tree at container start — not a false positive: a tree that differs between
   two containers of one image is precisely where apply is unsafe.)
3. **Reset to the baseline** (`reset --hard HEAD` + `clean -fd`) — the reset
   discipline pointed at the right target. The baseline has everything
   tracked, so `clean` cannot eat a shipped-untracked file.

How the base travels: the rollout side emits it as an artifact
(`patch.base_ref.txt`, inline **from memory** — the workspace copy sat
agent-writable for the whole run), the grading side declares it as an input,
and the workflow wires it along the same edge as the patch. Running a baseline
grading entry without a baseline rollout is therefore a loud missing-input
failure, not a wrong grade.

Two further guards:

- **The resolved base is recorded** on the attempt's shard as
  `patch_base_ref`, so "which base produced this patch" is answerable from the
  manifest alone, without re-opening artifacts.
- **Baseline creation fails closed.** If the commit cannot be made, the run
  aborts rather than falling back to `base_commit` — the fallback would
  silently produce exactly the contaminated patch this exists to prevent.

**Backlog:** Facet 1 moves from P1 to done-as-opt-in. The rest stands.
