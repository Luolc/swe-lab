# Todo — W2 (solve + eval)

Ordered, verifiable tasks for the current milestone ([`plan.md`](plan.md)). Each
clears the Definition of Done there. Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8
(break down if larger).

> Patch extraction is settled in ADR-0001 (Accepted). Rollout internals are still
> **code-authoritative**: **confirm the real current state against the code before
> starting a rollout task** — the acceptance criteria below are the *intended*
> shape, not a verified inventory of what already works.

---

## Task 1: Re-review the patch-extraction decisions ✅ done
Reconciled D1–D8 against the code with the user and formalized them as
[ADR-0001](../../decisions/ADR-0001-patch-extraction-and-grading.md) (Accepted);
the survey is demoted to non-authoritative background, and the code no longer
carries fragile doc references (only inline conclusions + a stable `ADR-0001`
pointer).

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
