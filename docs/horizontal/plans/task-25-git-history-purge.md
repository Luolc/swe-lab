# Task 25 — Purge future git history before the agent runs

Deep design for the P0 control of
[ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §3b, closing
[#191](https://github.com/Luolc/swe-lab/issues/191).

**The sequence below is not proposed — it is measured.** It was iterated inside
real SWE-Bench Pro images until every assertion passed on all of them, and it
found two defects in the reference implementations along the way (§3). Evidence
in §2; background and the wider threat model in the
[integrity study](../../reviews/2026-08-06-benchmark-integrity-study.md).

## 1. Goal

Before the agent starts, the repo in the rollout sandbox must contain **exactly
the past**: everything reachable at `base_commit`, and nothing dated after it.
Three properties, all asserted rather than assumed:

1. the solution commit is **unreachable** — by ref *and* by bare sha;
2. `base_commit` is **still reachable** — ADR-0001 diffs against it and the eval
   script resets to it, so losing it breaks extraction and grading;
3. **no reachable commit postdates** `base_commit`, which catches leaks whose
   sha we do not know in advance.

Non-goals: the past is deliberately kept (`git log`/`blame`/`show` on ancestors
is legitimate research a human engineer would have); the eval sandbox is not
touched (it needs refs for its restore step).

## 2. What the exposure actually is

Measured on `jefzda/sweap-images` (flipt), `--network none`, so everything below
is local to the image:

```
HEAD          = edc61fb357077d0384391cdfefae4411d8a1848e   2022-12-12T09:41:35+00:00
refs_total    = 237   (heads 1, remotes 22, tags 214)
origin        = https://github.com/flipt-io/flipt
commits ahead = 3444
```

The image tag's trailing sha **is** the fix commit, and it is 36 minutes into
the future of HEAD:

```
$ git merge-base --is-ancestor 6fe76d02... HEAD  ->  FUTURE COMMIT
$ git log -1 6fe76d02...
  6fe76d02  2022-12-12T10:17:52+00:00  feat(server/auth): validate flipt_client_token cookie in middleware (#1139)
$ git show 6fe76d02... --stat        # the reference solution, verbatim
```

Reachable through `refs/remotes/origin/*` — 22 of them. #93's "100% success
rate on all images" reproduces exactly.

## 3. Two defects in the reference implementations

Both found by running them, not by reading them. Both would have shipped.

### 3.1 A batch ref delete aborts on `origin/HEAD` (symref)

The ConnorBAdams fork — the fix proposed for SWE-Bench Pro in
[#93](https://github.com/scaleapi/SWE-bench_Pro-os/issues/93), and the sequence
#191 points at — deletes refs in one transaction:

```sh
git for-each-ref --format='delete %(refname)' refs/heads refs/remotes refs/tags \
  | git update-ref --stdin
```

On a real image this **fails outright**:

```
fatal: multiple updates for 'refs/remotes/origin/main'
       (including one via symref 'refs/remotes/origin/HEAD') are not allowed
```

`refs/remotes/origin/HEAD` is a symbolic ref to `origin/main`; `update-ref`
refuses to delete a symref and its target in the same transaction, and the whole
batch rolls back. Nothing is purged. Most clones have `origin/HEAD`, so this is
the common case, not an edge one.

**Fix:** delete symbolic refs first, individually, then batch the rest (§4 step
1a/1b).

This is exactly the failure mode ADR-0010 §4 exists for — a purge that silently
stops working. Here it did not fail silently, *because the assertions ran*: they
reported `FAIL solution commit STILL PRESENT` / `FAIL 3426 reachable commits
postdate base`. Without them the run would have looked clean.

### 3.2 The assertion's `date -d` is GNU-only — and Alpine images exist

The fork's build-time assertion computes its cutoff with:

```sh
AFTER_TIMESTAMP=$(date -d "$TARGET_TIMESTAMP + 1 second" '+%Y-%m-%d %H:%M:%S')
```

`date -d` in that form is GNU coreutils. Verified on the protonmail image —
which is **Alpine Linux v3.18** with busybox `date`, the very
Dockerfile/image mismatch reported in
[#75](https://github.com/scaleapi/SWE-bench_Pro-os/issues/75):

```
Alpine Linux v3.18
GNU date -d works?  NO -- busybox date
```

The assertion would fail (or worse, mis-compute) on every Alpine image in the
dataset.

**Fix:** compare committer timestamps as integers — `%ct` and `awk` — which is
pure git plus POSIX and needs no `date` at all (§4 step A3).

## 4. The sequence

Runs in the repo, before the agent starts. POSIX-sh compatible (verified under
`sh` on Alpine).

```sh
BASE="$(git rev-parse HEAD)"
BASE_TS="$(git show -s --format=%ct "$BASE")"

# 0. Detach HEAD so the branch refs deleted below are not the current ref.
git checkout --detach --quiet "$BASE"

# 1a. Symbolic refs FIRST, individually (see §3.1 — a batch delete aborts).
git for-each-ref --format='%(refname) %(symref)' refs/heads refs/remotes refs/tags \
  | while read -r ref target; do
      [ -n "${target:-}" ] && git symbolic-ref --delete "$ref" || true
    done

# 1b. Every remaining branch and remote-tracking ref, atomically.
git for-each-ref --format='delete %(refname)' refs/heads refs/remotes \
  | git update-ref --stdin

# 2. Tags: delete ONLY those whose commit postdates the base. `^{}` dereferences
#    an annotated tag to its commit (`git show-ref --dereference` resolves the
#    real sha, so tag-object indirection is not a hiding place).
git for-each-ref --format='%(refname) %(objectname)' refs/tags \
  | while read -r ref obj; do
      ctime="$(git show -s --format=%ct "${obj}^{}" 2>/dev/null || echo 0)"
      [ "$ctime" -gt "$BASE_TS" ] && printf 'delete %s\n' "$ref"
    done | git update-ref --stdin

# 3. Remotes, and the stray HEAD files.
for r in $(git remote); do git remote remove "$r"; done
rm -f .git/FETCH_HEAD .git/ORIG_HEAD

# 4. Reflog, then prune so unreachable objects stop answering to a bare sha.
git reflog expire --expire=now --all
git gc --prune=now --quiet
```

Then the assertions — **any failure fails the attempt** (ADR-0010 §4):

```sh
# A1. base reachable (ADR-0001 depends on it)
git cat-file -e "$BASE"                        || fail
# A2. solution unreachable (both shas are in the instance id, so this is exact)
! git cat-file -e "$FIX"                       || fail
# A3. nothing reachable postdates base — integer compare, no `date` (§3.2)
[ "$(git log --all --format=%ct | awk -v b="$BASE_TS" '$1>b' | wc -l)" -eq 0 ] || fail
```

### Why each step, and what it would leak without it

| Step | Closes |
|---|---|
| 0 detach | a branch ref cannot be deleted while checked out |
| 1a symrefs | §3.1 — otherwise the entire batch aborts and **nothing** is purged |
| 1b heads+remotes | `git log --all`, `origin/dev`, branch names that leak intent |
| 2 future tags | `git checkout <tag>`, incl. annotated-tag indirection |
| 3 remotes/HEAD files | `.git/config` URL, `ORIG_HEAD`, `FETCH_HEAD` |
| 4 reflog | commit messages after refs are gone |
| 4 `gc --prune=now` | **bare-sha access** and `git fsck --lost-found` dangling objects |

## 5. Results

Five images, four languages, two distro families. Every assertion passes on
every one.

| Image (repo) | Distro | refs before→after | tags before→after | ahead before→after | purge |
|---|---|---|---|---|---|
| flipt (Go) | debian | 237 → 68 | 214 → 68 | 3444 → 18 | 8s |
| ansible (Python) | debian | 698 → 353 | 636 → 353 | 16229 → 9630 | 26s |
| vuls (Go) | debian | 139 → 129 | 129 → 129 | 40 → 13 | 3s |
| tutanota (JS) | debian | 1830 → 672 | 1530 → 672 | 7173 → 185 | 51s |
| protonmail (JS) | **Alpine 3.18** | 201 → 0 | — | 28171 → 0 | 6s |

Timings are on an arm64 host running amd64 images under emulation; native
amd64 will be faster. Paid once per rollout sandbox, before the agent starts.

**Adversarial checks, post-purge (flipt):**

```
git show <fix sha>        blocked
git cat-file -p <fix sha> blocked      # bare sha, no ref involved
git fsck --lost-found     0 entries    # closes the dangling-object escape
git log --all --since     0
git reflog                0
.git/ORIG_HEAD            gone
origin config             gone
```

**ADR-0001 still works** — verified explicitly, since a purge that breaks
extraction is worse than the leak:

```
git add -N -- :/ ; git diff <base> --stat   ->  README.md | 1 +, brand_new.py | 1 +
git reset --hard <base>                     ->  OK
```

### Five findings that shape the design

1. **`ahead` is the wrong assertion.** After a *correct* purge, ansible still
   has **9630** commits that are not ancestors of HEAD — legitimate past
   side-history on preserved past tags (flipt: 18, newest 2022-11-29 vs a
   2022-12-12 base). An ancestry-based check would fail every clean run. Only
   the **date** comparison separates a leak from kept history.
2. **`--aggressive` is not worth it.** `gc --prune=now` takes 8s and drops
   `.git` 78M → 45M; adding `--aggressive` costs **12s more for 5MB**, and
   blocks nothing extra — bare-sha access is already dead without it. SWE-bench
   Verified uses `--aggressive`; we skip it deliberately.
3. **Zero refs is a valid end state.** protonmail keeps no tags at all (every
   ref was future) and finishes with `refs=0`. HEAD alone anchors the base and
   A1 still passes — the sequence must not assume a ref survives.
4. **Idempotent.** A second run exits 0 and the assertions still pass, so a
   retry or a resumed attempt is safe.
5. **`.git/logs/HEAD` survives as a 0-byte file.** `git reflog` reports 0
   entries; harmless, and not worth a special case.

## 6. What the two shas in an `instance_id` actually are

`instance_<Org>__<Repo>-<sha1>[-v<sha2>]`. The dataset has **no fix-commit
column** — only `base_commit` — so the fix commit is read out of the id. That
made it worth proving rather than assuming. Measured against 15 cached images
across 8 repos, plus the full 731-row parquet:

**sha1 is the fix commit.** Not inferred from the naming convention:

| Check | Result |
|---|---|
| `git diff <base_commit> <sha1>` == the `patch` ∪ `test_patch` columns | **15/15 exact** |
| `sha1^` (first parent) == `base_commit` | 15/15 |
| `sha1` is **not** an ancestor of HEAD (it is future history) | 15/15 |

The file-set comparison is the decisive one: the dataset splits the upstream
commit into a solution half (`patch`) and a test half (`test_patch`), and their
union is exactly what that commit changed.

*Two NodeBB instances are **merge commits** (2 parents), where
`git show --name-only` lists nothing — they compared as mismatches until the
diff was taken as `git diff base sha1`. The data was right; the first
comparison was wrong.*

**sha2 is an environment-setup commit, and it is shared.** Present on only
**368/731** ids, and those carry just **58 distinct** values (one appears 25×),
so several instances of a repo point at the same one. Subjects are env/dep
churn — *"docsite requirements path"*, *"v3.98.12"*, *"lint: fix missing
comma"*.

**It can be an ancestor of HEAD** (observed on vuls) — which is why §4's
assertion checks the sha is *future*, not merely present. If the id format ever
reordered the two, an env sha would be found, exist, and let a purge "prove"
something it never removed.

`dockerhub_tag` carries the same sha1, but **211/731 tags hit Docker's 128-char
limit** and are truncated, so the id is the better source of the two.

## 7. Where it lives

`swe_lab/git/` owns the git-state modules — `patch.py` (get the work out),
`history.py` (keep the answer out), `audit.py` (the agent-free sweep) — with
the pure script builders there and the observer that runs them alongside
`diff_extract` in `sandbox/observers/`.

The purge itself is a `SandboxObserver` contributed by `CodingAgentTask`
itself, purging in `after_create` — #191's placement argument, adopted as
stated:

- the sandbox is up and the repo is present, and the agent has not started;
- it attaches to the **rollout only**, so the eval sandbox — which needs refs
  for its restore step — is untouched;
- contributing it from the task rather than requiring callers to pass it means
  it cannot be forgotten on one code path;
- not in the harness's invocation script: that would tie an environment property
  to one harness and every other harness would need its own copy.

The observer needs the base sha (`sb.spec.base_commit`, already there) and the
solution sha for A2. The latter is **not** on `SandboxSpec` today — for
SWE-Bench Pro it is the trailing sha of the instance id, but deriving it by
string-splitting an id inside a generic observer is the wrong layer. Carry it as
an explicit optional field on the observer, supplied by the task from the
instance; when absent, A1 and A3 still run (A3 is the load-bearing one — it
catches leaks whose sha we never knew).

## 8. A standalone audit workflow — sweep the dataset before trusting it

The purge is also the answer to a question worth asking *before* a full run:
**does any instance in the dataset fail to purge cleanly?** Finding that out by
discovering an integrity failure two hours into a 731-instance sweep is the
expensive way.

So the purge ships as its own registered workflow — one entry, no agent — that
can be swept across the whole dataset cheaply:

```sh
swe-lab run git_integrity_audit <instance>      # one instance
```

- **One entry, no harness.** The task's action is trivial; the observer does the
  work in `after_create`. Cost per instance is an image pull, the purge, and the
  assertions — seconds, not an agent budget.
- **`network=False`.** Nothing here needs egress, and it keeps the audit honest
  about what the rollout will actually see.
- **It reports, not just passes/fails.** Every run emits `git_integrity.json`
  (a required declared output) carrying the before/after counts — refs, tags,
  remote refs, commits-ahead, commits-postdating-base — plus each assertion's
  result. A *passing* instance is data too: "3444 ahead → 0" is what makes the
  sweep interpretable, and it is the per-instance evidence behind §5's table.
- **A failing instance fails its attempt**, exactly as in the rollout (§9), so
  a sweep's failed set *is* the list of instances to investigate. Because the
  record is always written (ADR-0009), the reason is on the record rather than
  in a lost run.

This is the same observer and the same assertions as the rollout path — not a
parallel implementation. If the audit passes on an instance, the rollout's purge
on that instance is the same code doing the same thing.

Downstream validates this against the full SWE-Bench Pro dataset before the next
full run; a clean sweep is the precondition for trusting any number that follows.

## 9. Failure handling

A failed assertion is a **failed attempt with a named reason**, never a crash
that loses the record and never a silent pass — ADR-0009 and
[#188](https://github.com/Luolc/swe-lab/issues/188) settled that the record is
always written. The distinction matters here more than anywhere: an
integrity-failed attempt must be visibly distinguishable from a model failure,
or the numbers lie in the other direction.

`should_retry` should be **false** for an integrity failure. It is deterministic
— the same image purges the same way every time — so a retry burns a container
to reach the same verdict.

## 10. Tasks

| # | Work | Size |
|---|---|---|
| 1 | `GitHistoryPurgeObserver` + the sequence as a staged script; unit tests over `FakeSandbox` | M |
| 2 | The three assertions + a distinct failure reason plumbed to the record; `should_retry=False` | S |
| 3 | Wire into `CodingAgentTask`; confirm the eval sandbox is untouched | S |
| 4 | The `git_integrity_audit` workflow (§7) — one entry, no harness, `git_integrity.json` output | S |
| 5 | Live check on ≥5 images incl. one Alpine, asserting §5's table | S |
| 6 | Policy stamp on the record (ADR-0010 §5) — shared with task 26 | S |

**Definition of done:** every rollout sandbox purges before the agent starts;
all three assertions pass on the live matrix; an induced leak (skip step 1a)
fails the attempt with the named reason rather than scoring it; extraction and
grading are byte-unchanged on a known instance; and `git_integrity_audit` runs
standalone on an instance and writes its report.

## 11. Known limits

- **A past-dated commit is kept even if it is topologically future work.**
  Committer date (`%ct`) is the filter, and a rebase or cherry-pick can carry an
  older date. SWE-bench Verified accepts the same risk with the same rule; A2
  catches the actual solution commit regardless.
- **Nothing here defends the network.** A purged repo is one `git clone` from
  being restored, and the network is the larger vector (study §1.2: 57% vs 9%).
  Per ADR-0010's 2026-08-06 amendment that is handled by configuration —
  `network=False` on the Docker backend, an egress policy downstream — not by
  this task. **This control is only sound in combination with that setting**,
  and the policy stamp (task 5) is what records whether it was.
- `alternates` was absent on all five images (checked); a second copy of the
  repo elsewhere in an image was not exhaustively searched.
