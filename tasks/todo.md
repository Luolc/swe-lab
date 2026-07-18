# Todo — W2 (solve + eval)

Ordered, verifiable tasks for the current milestone ([`plan.md`](plan.md)). Each
clears the Definition of Done there. Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8
(break down if larger).

> ⚠️ Rollout internals (`core/patch.py`, `rollout/`) and D1–D8 are **provisional /
> code-authoritative**. **Confirm the real current state against the code before
> starting a rollout task** — the acceptance criteria below are the *intended*
> shape, not a verified inventory of what already works.

---

## Task 1: Re-review the patch-extraction decisions (D1–D8)
**Description:** Sit down with the user and reconcile the D1–D8 decision log +
`docs/patch-extraction.md` against the actual code; decide what's true, drop the
provisional banner (or split into real ADRs) once settled.
- **Acceptance:** each of D1–D8 is confirmed / revised / dropped against the code;
  the banner comes off (or the doc is rewritten); if any decision is now stable
  and standalone, it becomes a numbered ADR in `docs/decisions/`.
- **Verification:** `docs/decisions/patch-extraction-decisions.md` no longer says
  "provisional"; code and doc agree.
- **Dependencies:** none (blocks building further on rollout). **Scope:** M
- **Needs the user** — this is a joint review, not an autonomous task.

## Task 2: Confirm rollout runs end-to-end on GitHub Actions
**Description:** Validate the container agent loop on a real instance: GH Actions
container-job model, pinned linux-x64 Claude Code binary mounted, agent edits the
repo, `git diff` → clean `patch.diff`, explicit `outcome` recorded.
- **Acceptance:** one instance runs to a terminal `outcome` (`resolved` /
  `unresolved_tests_failed` / `empty_patch`) in CI; the captured `patch.diff`
  applies against `base_commit`; the trajectory + raw patch are stored (HF pattern).
- **Verification:** a GH Actions run link + the graded result; `empty_patch` is
  never graded as a pass.
- **Dependencies:** Task 1 (settle the extraction base first). **Scope:** L

## Task 3: Matrix eval across many instances
**Description:** One dispatch grading many instances in parallel; shard across
workflows to clear the 256 matrix-cap toward the full 731.
- **Acceptance:** a single trigger grades N instances; results aggregate into one
  report; **any instance dropped by a cap/shard boundary is logged**, never
  silently omitted.
- **Verification:** a run grading a batch (e.g. 20) with a complete,
  reconciled result set.
- **Dependencies:** Task 2. **Scope:** M

### Checkpoint — rollout + matrix eval prove out on a batch before the full 731.

## Task 4: Publish the fixed parquet; retire `patches.py`
**Description:** Re-host a fully-corrected parquet (the 8 truncated `fail_to_pass`
names) on our own HF dataset repo; point the loader at it; delete the in-memory
`patch_fail_to_pass` stopgap.
- **Acceptance:** loader reads the fixed parquet; `patches.py` is gone; gold sweep
  still 731/731.
- **Verification:** `uv run pytest` green without the stopgap; a gold self-test on
  NodeBB/ansible/vuls resolves.
- **Dependencies:** none (independent). **Scope:** S

## Backlog (from the decisions — pull in when they bite)
- **P1:** post-setup diff base (D1) · per-ecosystem `:(exclude)` denylist with
  drop-logging (D2) · faithful binary extract+apply (D3) · deferred
  silent-failure guards with logging: gitignored-new-source, submodules, LFS (D7).
- **P2:** eval-side empty-patch guard (D8) · fuller all-test-file reset (D5).
- **Infra:** lightweight PR CI (`pytest` + `pre-commit`) + branch protection →
  enables `gh pr merge --auto` on green.
